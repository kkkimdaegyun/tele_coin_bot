from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace

import httpx

from storage import ChartTeacherStore


GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"


@dataclass(frozen=True)
class UsdtDominanceSnapshot:
    value: float
    change_24h_pp: float | None
    change_24h_percent: float | None
    change_1h_pp: float | None
    change_4h_pp: float | None
    usdt_market_cap_usd: float
    total_market_cap_usd: float
    total_market_change_24h: float | None
    volume_change_24h: float | None
    timestamp: int
    regime: str
    label: str
    guidance: str
    source: str = "CoinGecko"


_cache: tuple[float, UsdtDominanceSnapshot] | None = None
_cache_lock = asyncio.Lock()


async def get_usdt_dominance(
    *,
    store: ChartTeacherStore | None = None,
    cache_seconds: int = 900,
) -> UsdtDominanceSnapshot | None:
    """Fetch USDT dominance without requiring an account or API secret.

    CoinGecko's current USDT and total-market figures provide an immediate
    24-hour comparison. Locally stored samples add 1-hour and 4-hour changes
    after enough observations have accumulated.
    """

    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < cache_seconds:
        return _cache[1]
    async with _cache_lock:
        now = time.monotonic()
        if _cache is not None and now - _cache[0] < cache_seconds:
            return _cache[1]
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                global_response, tether_response = await asyncio.gather(
                    client.get(GLOBAL_URL),
                    client.get(
                        MARKETS_URL,
                        params={
                            "vs_currency": "usd",
                            "ids": "tether",
                            "price_change_percentage": "24h",
                        },
                    ),
                )
            if global_response.status_code >= 400:
                raise RuntimeError(
                    f"CoinGecko global API returned HTTP {global_response.status_code}"
                )
            if tether_response.status_code >= 400:
                raise RuntimeError(
                    f"CoinGecko markets API returned HTTP {tether_response.status_code}"
                )
            snapshot = snapshot_from_payloads(
                global_response.json(),
                tether_response.json(),
            )
            store = store or ChartTeacherStore()
            store.record_dominance_snapshot(
                observed_at=snapshot.timestamp,
                usdt_dominance=snapshot.value,
                usdt_market_cap_usd=snapshot.usdt_market_cap_usd,
                total_market_cap_usd=snapshot.total_market_cap_usd,
                dominance_change_24h_pp=snapshot.change_24h_pp,
                total_market_change_24h=snapshot.total_market_change_24h,
                volume_change_24h=snapshot.volume_change_24h,
            )
            snapshot = _with_local_changes(snapshot, store)
        except Exception:
            return _cache[1] if _cache is not None else None
        _cache = (time.monotonic(), snapshot)
        return snapshot


def snapshot_from_payloads(
    global_payload: dict,
    tether_payload: list[dict],
) -> UsdtDominanceSnapshot:
    try:
        data = global_payload["data"]
        tether = tether_payload[0]
        value = float(data["market_cap_percentage"]["usdt"])
        total_market_cap = float(data["total_market_cap"]["usd"])
        usdt_market_cap = float(tether["market_cap"])
        total_change = _optional_float(data.get("market_cap_change_percentage_24h_usd"))
        volume_change = _optional_float(data.get("volume_change_percentage_24h_usd"))
        usdt_change = _optional_float(tether.get("market_cap_change_percentage_24h"))
        timestamp = int(data["updated_at"])
    except (IndexError, KeyError, TypeError, ValueError):
        raise ValueError("CoinGecko dominance data is invalid") from None
    if not 0 < value < 100 or total_market_cap <= 0 or usdt_market_cap <= 0:
        raise ValueError("CoinGecko dominance values are invalid")

    change_pp = None
    change_percent = None
    if total_change is not None and usdt_change is not None:
        total_denominator = 1 + total_change / 100
        usdt_denominator = 1 + usdt_change / 100
        if total_denominator > 0 and usdt_denominator > 0:
            # Apply the two market-cap growth rates to CoinGecko's own current
            # dominance value. This avoids a small mismatch between the global
            # index universe and the raw market-cap fields.
            previous_value = value * total_denominator / usdt_denominator
            change_pp = value - previous_value
            change_percent = (value / previous_value - 1) * 100

    regime, label, guidance = dominance_regime(value, change_pp, total_change)
    return UsdtDominanceSnapshot(
        value=round(value, 4),
        change_24h_pp=round(change_pp, 4) if change_pp is not None else None,
        change_24h_percent=(
            round(change_percent, 3) if change_percent is not None else None
        ),
        change_1h_pp=None,
        change_4h_pp=None,
        usdt_market_cap_usd=usdt_market_cap,
        total_market_cap_usd=total_market_cap,
        total_market_change_24h=total_change,
        volume_change_24h=volume_change,
        timestamp=timestamp,
        regime=regime,
        label=label,
        guidance=guidance,
    )


def dominance_regime(
    value: float,
    change_24h_pp: float | None,
    total_market_change_24h: float | None,
) -> tuple[str, str, str]:
    change = change_24h_pp or 0.0
    market = total_market_change_24h or 0.0
    if change <= -0.10 and market >= 1.0:
        return (
            "risk_on",
            "위험선호 우호",
            "테더 비중 하락+전체 시총 상승 · 코인 흐름에 우호적이지만 과열 추격 근거는 아님",
        )
    if change >= 0.10 and market <= -1.0:
        return (
            "risk_off",
            "현금 대피 경계",
            "테더 비중 상승+전체 시총 하락 · 신규 진입을 줄이고 지지·EMA 이탈 확인",
        )
    if change <= -0.05:
        return (
            "supportive",
            "코인 우호 관찰",
            "테더 비중은 하락 중이나 전체 시총 방향까지 함께 확인해야 함",
        )
    if change >= 0.05:
        return (
            "cautious",
            "방어 수요 관찰",
            "테더 비중이 상승 중 · 코인별 돌파보다 지지 유지 여부를 우선 확인",
        )
    return (
        "neutral",
        "중립",
        "테더 비중 변화가 작아 USDT.D만으로 방향을 정하기 어려움",
    )


def format_pp(value: float | None) -> str:
    return "수집 중" if value is None else f"{value:+.2f}%p"


def _with_local_changes(
    snapshot: UsdtDominanceSnapshot,
    store: ChartTeacherStore,
) -> UsdtDominanceSnapshot:
    one_hour = store.load_dominance_before(snapshot.timestamp - 55 * 60)
    four_hour = store.load_dominance_before(snapshot.timestamp - 235 * 60)
    change_1h = _bounded_change(snapshot, one_hour, 45 * 60, 90 * 60)
    change_4h = _bounded_change(snapshot, four_hour, 210 * 60, 300 * 60)
    return replace(snapshot, change_1h_pp=change_1h, change_4h_pp=change_4h)


def _bounded_change(
    current: UsdtDominanceSnapshot,
    prior: dict | None,
    minimum_age: int,
    maximum_age: int,
) -> float | None:
    if prior is None:
        return None
    age = current.timestamp - int(prior["observed_at"])
    if not minimum_age <= age <= maximum_age:
        return None
    return round(current.value - float(prior["usdt_dominance"]), 4)


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
