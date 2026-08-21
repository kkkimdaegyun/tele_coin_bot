from __future__ import annotations

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
    reaction: str | None = None


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

    touch_intervals = {"1h", "4h"}
    alignment_intervals = {"1h", "4h", "1d"}
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
        if (
            interval in alignment_intervals
            and current_alignment in {"bullish", "bearish"}
            and current_alignment != previous_alignment
        ):
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

        if interval not in touch_intervals:
            continue
        confirmed = candles[-1]
        for period, level in (
            (20, ema20_values[-1]),
            (50, ema50_values[-1]),
            (200, ema200_values[-1]),
        ):
            if level is None:
                continue
            touched = (
                confirmed.low <= level * (1.0 + tolerance)
                and confirmed.high >= level * (1.0 - tolerance)
            )
            if touched:
                reaction = "closed_above" if confirmed.close >= level else "closed_below"
                reaction_label = "위 마감" if reaction == "closed_above" else "아래 마감"
                signals.append(
                    EmaSignal(
                        event=f"ema_{period}_touch_{reaction}",
                        interval=interval,
                        bar_open_time=confirmed.open_time,
                        kind="touch",
                        description=f"EMA {period} 접촉 후 {reaction_label}",
                        ema_period=period,
                        ema_value=float(level),
                        reaction=reaction,
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

    return detect_ema_signals(
        datasets,
        {},
        tolerance_percent=tolerance_percent,
    )
