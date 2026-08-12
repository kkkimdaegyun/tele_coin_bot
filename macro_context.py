from __future__ import annotations

import asyncio
import csv
import io
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx


@dataclass(frozen=True)
class MacroIndicator:
    series_id: str
    label: str
    value: float
    change_5d: float
    change_20d: float
    unit: str
    latest_date: str
    impact: int


@dataclass(frozen=True)
class MacroContext:
    score: int
    regime: str
    indicators: tuple[MacroIndicator, ...]
    source: str = "FRED · Federal Reserve Bank of St. Louis"


SERIES = {
    "DGS10": ("미국 10년물", "%", "yield"),
    "DTWEXBGS": ("달러지수", "", "risk_inverse"),
    "VIXCLS": ("VIX", "", "vix"),
    "NASDAQCOM": ("나스닥", "", "risk_positive"),
}

_cache: tuple[float, MacroContext] | None = None
_cache_lock = asyncio.Lock()


async def get_macro_context(cache_seconds: int = 3600) -> MacroContext | None:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < cache_seconds:
        return _cache[1]
    async with _cache_lock:
        now = time.monotonic()
        if _cache is not None and now - _cache[0] < cache_seconds:
            return _cache[1]
        try:
            context = await _fetch_macro_context()
        except Exception:
            return _cache[1] if _cache is not None else None
        _cache = (time.monotonic(), context)
        return context


async def _fetch_macro_context() -> MacroContext:
    start = (datetime.now(UTC) - timedelta(days=100)).date().isoformat()

    async def fetch(series_id: str) -> MacroIndicator:
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(url, params={"id": series_id, "cosd": start})
        except httpx.HTTPError:
            raise RuntimeError("Macro data request failed") from None
        if response.status_code >= 400:
            raise RuntimeError(f"Macro data API returned HTTP {response.status_code}")
        reader = csv.DictReader(io.StringIO(response.text))
        values: list[tuple[str, float]] = []
        for row in reader:
            raw = row.get(series_id)
            if raw in (None, "", "."):
                continue
            try:
                values.append((next(iter(row.values())), float(raw)))
            except (TypeError, ValueError):
                continue
        if len(values) < 21:
            raise RuntimeError(f"Macro series {series_id} has insufficient observations")
        latest_date, latest = values[-1]
        change_5d = _change(series_id, latest, values[-6][1])
        change_20d = _change(series_id, latest, values[-21][1])
        label, unit, kind = SERIES[series_id]
        return MacroIndicator(
            series_id=series_id,
            label=label,
            value=latest,
            change_5d=change_5d,
            change_20d=change_20d,
            unit=unit,
            latest_date=latest_date,
            impact=_impact(kind, latest, change_5d),
        )

    indicators = tuple(await asyncio.gather(*(fetch(series_id) for series_id in SERIES)))
    score = max(0, min(100, round(50 + sum(item.impact for item in indicators) * 12.5)))
    regime = "위험자산 우호" if score >= 65 else "위험자산 부담" if score <= 35 else "중립·혼조"
    return MacroContext(score=score, regime=regime, indicators=indicators)


def _change(series_id: str, latest: float, previous: float) -> float:
    if series_id == "DGS10":
        return latest - previous
    return (latest / previous - 1.0) * 100 if previous else 0.0


def _impact(kind: str, latest: float, change_5d: float) -> int:
    if kind == "yield":
        return 1 if change_5d <= -0.05 else -1 if change_5d >= 0.05 else 0
    if kind == "risk_inverse":
        return 1 if change_5d <= -0.30 else -1 if change_5d >= 0.30 else 0
    if kind == "risk_positive":
        return 1 if change_5d >= 1.0 else -1 if change_5d <= -1.0 else 0
    if kind == "vix":
        if latest >= 25 or change_5d >= 10:
            return -1
        if latest <= 18 and change_5d <= 0:
            return 1
    return 0
