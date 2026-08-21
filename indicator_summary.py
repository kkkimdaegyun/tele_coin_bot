from __future__ import annotations

from analysis_engine import TimeframeAnalysis


def ema_alignment_label(timeframe: TimeframeAnalysis) -> str:
    if timeframe.ema20 is None or timeframe.ema50 is None or timeframe.ema200 is None:
        return "배열 데이터 부족"
    if timeframe.ema20 > timeframe.ema50 > timeframe.ema200:
        return "정배열"
    if timeframe.ema20 < timeframe.ema50 < timeframe.ema200:
        return "역배열"
    return "혼조 배열"


def rsi_zone(value: float | None) -> str:
    if value is None:
        return "데이터 부족"
    if value >= 70:
        return "과매수 주의"
    if value <= 30:
        return "과매도 주의"
    if value >= 55:
        return "상승 우위"
    if value <= 45:
        return "하락 우위"
    return "중립"


def price_ema20_position(timeframe: TimeframeAnalysis) -> str:
    if timeframe.ema20 is None:
        return "EMA20 위치 불명"
    return "가격 EMA20 위" if timeframe.close >= timeframe.ema20 else "가격 EMA20 아래"


def core_indicator_line(timeframe: TimeframeAnalysis, label: str) -> str:
    rsi_text = "RSI -" if timeframe.rsi is None else f"RSI {timeframe.rsi:.1f} {rsi_zone(timeframe.rsi)}"
    return (
        f"{label} · {rsi_text} · {ema_alignment_label(timeframe)} · "
        f"{price_ema20_position(timeframe)}"
    )
