from __future__ import annotations

from dataclasses import dataclass

from market_data import Candle


@dataclass(frozen=True)
class WaveContext:
    direction: str
    phase: str
    structure_score: int
    anchor_start: float
    wave_one_end: float
    correction_end: float
    current_extension: float
    fib_100: float
    fib_1618: float
    fib_2618: float
    next_level_label: str | None
    next_level: float | None


def analyze_wave_context(candles: list[Candle]) -> WaveContext | None:
    if len(candles) < 40:
        return None
    pivots = _pivots(candles)
    bullish = _bullish_wave(candles[-1].close, pivots)
    bearish = _bearish_wave(candles[-1].close, pivots)
    candidates = [item for item in (bullish, bearish) if item is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.structure_score)


def wave_summary(context: dict | WaveContext | None) -> str:
    if not context:
        return "뚜렷한 엘리어트 파동 후보를 찾지 못했습니다."
    value = context.__dict__ if isinstance(context, WaveContext) else context
    direction = "상승" if value["direction"] == "bullish" else "하락"
    extension = float(value["current_extension"])
    next_label = value.get("next_level_label")
    next_level = value.get("next_level")
    next_text = (
        f" · 다음 피보나치 {next_label} {float(next_level):,.2f}"
        if next_label and next_level is not None
        else " · 주요 확장 구간을 이미 통과해 조정 위험 확인"
    )
    return (
        f"{direction} {value['phase']} · 현재 확장 {extension:.2f}배"
        f"{next_text} · 구조 점수 {int(value['structure_score'])}/100(확률 아님)"
    )


def _pivots(candles: list[Candle], left: int = 2, right: int = 2) -> list[tuple[int, str, float]]:
    raw: list[tuple[int, str, float]] = []
    for index in range(left, len(candles) - right):
        sample = candles[index - left:index + right + 1]
        candle = candles[index]
        is_high = candle.high >= max(item.high for item in sample)
        is_low = candle.low <= min(item.low for item in sample)
        if is_high and not is_low:
            raw.append((index, "high", candle.high))
        elif is_low and not is_high:
            raw.append((index, "low", candle.low))
    compressed: list[tuple[int, str, float]] = []
    for pivot in raw:
        if compressed and compressed[-1][1] == pivot[1]:
            prior = compressed[-1]
            more_extreme = pivot[2] > prior[2] if pivot[1] == "high" else pivot[2] < prior[2]
            if more_extreme:
                compressed[-1] = pivot
        else:
            compressed.append(pivot)
    return compressed[-16:]


def _bullish_wave(current: float, pivots: list[tuple[int, str, float]]) -> WaveContext | None:
    for correction_index in range(len(pivots) - 1, 1, -1):
        correction = pivots[correction_index]
        wave_one = pivots[correction_index - 1]
        anchor = pivots[correction_index - 2]
        if (anchor[1], wave_one[1], correction[1]) != ("low", "high", "low"):
            continue
        length = wave_one[2] - anchor[2]
        if length <= 0:
            continue
        retracement = (wave_one[2] - correction[2]) / length
        if not 0.20 <= retracement <= 0.90 or correction[2] <= anchor[2] * 0.97:
            continue
        extension = (current - correction[2]) / length
        score = 52
        if 0.382 <= retracement <= 0.786:
            score += 15
        if current >= wave_one[2]:
            score += 18
        if 0.8 <= extension <= 1.8:
            score += 10
        return _context("bullish", anchor[2], wave_one[2], correction[2], extension, score)
    return None


def _bearish_wave(current: float, pivots: list[tuple[int, str, float]]) -> WaveContext | None:
    for correction_index in range(len(pivots) - 1, 1, -1):
        correction = pivots[correction_index]
        wave_one = pivots[correction_index - 1]
        anchor = pivots[correction_index - 2]
        if (anchor[1], wave_one[1], correction[1]) != ("high", "low", "high"):
            continue
        length = anchor[2] - wave_one[2]
        if length <= 0:
            continue
        retracement = (correction[2] - wave_one[2]) / length
        if not 0.20 <= retracement <= 0.90 or correction[2] >= anchor[2] * 1.03:
            continue
        extension = (correction[2] - current) / length
        score = 52
        if 0.382 <= retracement <= 0.786:
            score += 15
        if current <= wave_one[2]:
            score += 18
        if 0.8 <= extension <= 1.8:
            score += 10
        return _context("bearish", anchor[2], wave_one[2], correction[2], extension, score)
    return None


def _context(
    direction: str,
    anchor: float,
    wave_one: float,
    correction: float,
    extension: float,
    score: int,
) -> WaveContext:
    length = abs(wave_one - anchor)
    sign = 1 if direction == "bullish" else -1
    levels = {
        "1.0": correction + sign * length,
        "1.618": correction + sign * length * 1.618,
        "2.618": correction + sign * length * 2.618,
    }
    if extension < 1.0:
        phase, next_label = "3파 시작 후보", "1.0"
    elif extension < 1.618:
        phase, next_label = "3파 진행 후보", "1.618"
    elif extension < 2.618:
        phase, next_label = "3파 확장 후보", "2.618"
    else:
        phase, next_label = "확장 과열·4파 조정 경계", None
    return WaveContext(
        direction=direction,
        phase=phase,
        structure_score=min(100, score),
        anchor_start=round(anchor, 8),
        wave_one_end=round(wave_one, 8),
        correction_end=round(correction, 8),
        current_extension=round(extension, 3),
        fib_100=round(levels["1.0"], 8),
        fib_1618=round(levels["1.618"], 8),
        fib_2618=round(levels["2.618"], 8),
        next_level_label=next_label,
        next_level=round(levels[next_label], 8) if next_label else None,
    )
