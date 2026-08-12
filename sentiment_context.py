from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx


API_URL = "https://api.alternative.me/fng/"


@dataclass(frozen=True)
class FearGreedSnapshot:
    value: int
    label: str
    yesterday_value: int | None
    week_value: int | None
    change_1d: int | None
    change_7d: int | None
    timestamp: int
    guidance: str
    source: str = "Alternative.me"
    scope: str = "BTC 중심 시장심리"


_cache: tuple[float, FearGreedSnapshot] | None = None
_cache_lock = asyncio.Lock()


async def get_fear_greed(cache_seconds: int = 3600) -> FearGreedSnapshot | None:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < cache_seconds:
        return _cache[1]
    async with _cache_lock:
        now = time.monotonic()
        if _cache is not None and now - _cache[0] < cache_seconds:
            return _cache[1]
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(API_URL, params={"limit": 8, "format": "json"})
            if response.status_code >= 400:
                raise RuntimeError(f"Fear and Greed API returned HTTP {response.status_code}")
            snapshot = snapshot_from_payload(response.json())
        except Exception:
            return _cache[1] if _cache is not None else None
        _cache = (time.monotonic(), snapshot)
        return snapshot


def snapshot_from_payload(payload: dict) -> FearGreedSnapshot:
    metadata = payload.get("metadata") or {}
    if metadata.get("error"):
        raise ValueError("Fear and Greed API returned an error")
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Fear and Greed API returned no data")

    values: list[tuple[int, int, str]] = []
    for row in rows:
        try:
            value = max(0, min(100, int(row["value"])))
            timestamp = int(row["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        values.append((timestamp, value, str(row.get("value_classification", ""))))
    if not values:
        raise ValueError("Fear and Greed API data is invalid")
    values.sort(reverse=True)

    timestamp, current, classification = values[0]
    yesterday = values[1][1] if len(values) >= 2 else None
    week = values[7][1] if len(values) >= 8 else values[-1][1] if len(values) >= 2 else None
    return FearGreedSnapshot(
        value=current,
        label=_korean_label(classification, current),
        yesterday_value=yesterday,
        week_value=week,
        change_1d=current - yesterday if yesterday is not None else None,
        change_7d=current - week if week is not None else None,
        timestamp=timestamp,
        guidance=fear_greed_guidance(current, current - week if week is not None else None),
    )


def fear_greed_guidance(value: int, change_7d: int | None = None) -> str:
    if value <= 24:
        if change_7d is not None and change_7d < 0:
            return "극단적 공포가 더 심해지는 중 · 떨어지는 칼 주의, 가격 구조 확인 전 진입 근거 아님"
        return "극단적 공포 · 역발상 관찰 구간이지만 가격 구조 확인 전 매수 신호 아님"
    if value <= 44:
        return "공포 구간 · 과매도 가능성 관찰, EMA·지지·거래량 확인 전 매수 신호 아님"
    if value <= 55:
        return "중립 구간 · 시장심리만으로는 방향 우위가 없음"
    if value <= 74:
        return "탐욕 구간 · 상승 추세일 수 있으나 추격 매수와 FOMO 주의"
    return "극단적 탐욕 · 과열 및 조정 위험 경계, 신규 추격 진입 주의"


def _korean_label(classification: str, value: int) -> str:
    labels = {
        "extreme fear": "극단적 공포",
        "fear": "공포",
        "neutral": "중립",
        "greed": "탐욕",
        "extreme greed": "극단적 탐욕",
    }
    normalized = " ".join(classification.strip().lower().split())
    if normalized in labels:
        return labels[normalized]
    if value <= 24:
        return "극단적 공포"
    if value <= 44:
        return "공포"
    if value <= 55:
        return "중립"
    if value <= 74:
        return "탐욕"
    return "극단적 탐욕"


def format_change(value: int | None) -> str:
    return "-" if value is None else f"{value:+d}"
