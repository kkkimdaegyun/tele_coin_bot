from __future__ import annotations

import asyncio
from dataclasses import dataclass

from analysis_engine import ema
from analysis_service import TIMEFRAMES, normalize_symbol
from market_data import BinanceMarketData, Candle
from storage import ChartTeacherStore


@dataclass(frozen=True)
class EmaSignal:
    event: str
    interval: str
    bar_open_time: int
    kind: str
    description: str
    ema_period: int | None = None
    ema_value: float | None = None
    alignment: str | None = None


def alignment_state(ema20: float | None, ema50: float | None, ema200: float | None) -> str:
    if ema20 is None or ema50 is None or ema200 is None:
        return "mixed"
    if ema20 > ema50 > ema200:
        return "bullish"
    if ema20 < ema50 < ema200:
        return "bearish"
    return "mixed"


def detect_ema_signals(
    datasets: dict[str, list[Candle]],
    live_candles: dict[str, Candle],
    *,
    tolerance_percent: float = 0.05,
) -> list[EmaSignal]:
    signals: list[EmaSignal] = []
    tolerance = max(0.0, min(float(tolerance_percent), 1.0)) / 100.0

    for interval in TIMEFRAMES:
        candles = datasets.get(interval, [])
        if len(candles) < 202:
            continue
        closes = [candle.close for candle in candles]
        ema20_values = ema(closes, 20)
        ema50_values = ema(closes, 50)
        ema200_values = ema(closes, 200)

        current_alignment = alignment_state(
            ema20_values[-1], ema50_values[-1], ema200_values[-1]
        )
        previous_alignment = alignment_state(
            ema20_values[-2], ema50_values[-2], ema200_values[-2]
        )
        if current_alignment in {"bullish", "bearish"} and current_alignment != previous_alignment:
            korean = "정배열" if current_alignment == "bullish" else "역배열"
            signals.append(
                EmaSignal(
                    event=f"ema_alignment_{current_alignment}",
                    interval=interval,
                    bar_open_time=candles[-1].open_time,
                    kind="alignment",
                    description=f"EMA 20·50·200 {korean} 전환 확정",
                    alignment=current_alignment,
                )
            )

        live = live_candles.get(interval)
        if live is None:
            continue
        for period, level in (
            (20, ema20_values[-1]),
            (50, ema50_values[-1]),
            (200, ema200_values[-1]),
        ):
            if level is None:
                continue
            touched = live.low <= level * (1.0 + tolerance) and live.high >= level * (1.0 - tolerance)
            if touched:
                signals.append(
                    EmaSignal(
                        event=f"ema_{period}_touch",
                        interval=interval,
                        bar_open_time=live.open_time,
                        kind="touch",
                        description=f"EMA {period} 터치",
                        ema_period=period,
                        ema_value=float(level),
                    )
                )
    return signals


async def collect_ema_signals(
    symbol: str,
    *,
    provider: BinanceMarketData | None = None,
    store: ChartTeacherStore | None = None,
    tolerance_percent: float = 0.05,
) -> list[EmaSignal]:
    symbol = normalize_symbol(symbol)
    provider = provider or BinanceMarketData()
    store = store or ChartTeacherStore()
    datasets = {
        interval: store.load_candles(symbol, interval, limit=1000)
        for interval in TIMEFRAMES
    }

    async def fetch_live(interval: str) -> tuple[str, Candle | None]:
        candles = await provider.fetch_klines(symbol, interval, limit=2)
        return interval, candles[-1] if candles else None

    results = await asyncio.gather(*(fetch_live(interval) for interval in TIMEFRAMES))
    live_candles = {interval: candle for interval, candle in results if candle is not None}
    return detect_ema_signals(
        datasets,
        live_candles,
        tolerance_percent=tolerance_percent,
    )
