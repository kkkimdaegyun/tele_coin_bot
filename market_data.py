from __future__ import annotations

import csv
import math
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx


INTERVAL_MS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "1w": 7 * 24 * 60 * 60 * 1000,
}


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    taker_buy_volume: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class BinanceMarketData:
    def __init__(self, base_url: str | None = None, timeout: float = 15.0):
        self.base_url = (base_url or os.getenv("BINANCE_API_BASE") or "https://api.binance.com").rstrip("/")
        self.timeout = timeout

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int = 1000,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[Candle]:
        if interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported interval: {interval}")
        symbol = symbol.upper().strip()
        params: dict[str, str | int] = {
            "symbol": symbol,
            "interval": interval,
            "limit": max(1, min(limit, 1000)),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/api/v3/klines", params=params)
        except httpx.HTTPError:
            raise RuntimeError("Market data request failed") from None
        if response.status_code >= 400:
            raise RuntimeError(f"Market data API returned HTTP {response.status_code}")

        rows = response.json()
        if not isinstance(rows, list):
            raise RuntimeError("Market data API returned an invalid response")
        return [
            Candle(
                open_time=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                close_time=int(row[6]),
                taker_buy_volume=float(row[9]),
            )
            for row in rows
        ]

    async def fetch_price(self, symbol: str) -> float:
        """Return the latest Binance spot price at request time."""
        symbol = symbol.upper().strip()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/api/v3/ticker/price",
                    params={"symbol": symbol},
                )
        except httpx.HTTPError:
            raise RuntimeError("Live price request failed") from None
        if response.status_code >= 400:
            raise RuntimeError(f"Live price API returned HTTP {response.status_code}")

        try:
            price = float(response.json()["price"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("Live price API returned an invalid response") from None
        if not math.isfinite(price) or price <= 0:
            raise RuntimeError("Live price API returned an invalid price")
        return price

    async def fetch_history(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        *,
        end_time: int | None = None,
        max_pages: int = 500,
    ) -> list[Candle]:
        end_time = end_time or int(datetime.now(UTC).timestamp() * 1000)
        cursor = int(start_time)
        candles: list[Candle] = []
        for _ in range(max_pages):
            page = await self.fetch_klines(
                symbol,
                interval,
                limit=1000,
                start_time=cursor,
                end_time=end_time,
            )
            if not page:
                break
            candles.extend(page)
            next_cursor = page[-1].open_time + INTERVAL_MS[interval]
            if next_cursor <= cursor or next_cursor > end_time or len(page) < 1000:
                break
            cursor = next_cursor
        return _deduplicate(candles)


def closed_candles(candles: list[Candle], now_ms: int | None = None) -> list[Candle]:
    now_ms = now_ms or int(datetime.now(UTC).timestamp() * 1000)
    return [candle for candle in candles if candle.close_time < now_ms]


def import_tradingview_csv(path: Path, interval: str) -> list[Candle]:
    if interval not in INTERVAL_MS:
        raise ValueError(f"Unsupported interval: {interval}")
    candles: list[Candle] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            normalized = {str(key).strip().lower(): value for key, value in raw.items()}
            time_value = normalized.get("time") or normalized.get("date") or normalized.get("datetime")
            if not time_value:
                continue
            opened = _parse_time_ms(time_value)
            try:
                candles.append(
                    Candle(
                        open_time=opened,
                        open=float(normalized["open"]),
                        high=float(normalized["high"]),
                        low=float(normalized["low"]),
                        close=float(normalized["close"]),
                        volume=float(normalized.get("volume") or 0),
                        close_time=opened + INTERVAL_MS[interval] - 1,
                        taker_buy_volume=None,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    return _deduplicate(candles)


def _parse_time_ms(value: str) -> int:
    value = value.strip()
    try:
        number = float(value)
        return int(number if number > 10_000_000_000 else number * 1000)
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)


def _deduplicate(candles: list[Candle]) -> list[Candle]:
    return [
        Candle(**values)
        for _, values in sorted(
            {candle.open_time: candle.to_dict() for candle in candles}.items()
        )
    ]
