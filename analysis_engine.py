from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from market_data import Candle
from wave_context import analyze_wave_context


@dataclass
class PatternSignal:
    name: str
    direction: str
    confidence: int
    status: str
    evidence: str


@dataclass
class TimeframeAnalysis:
    interval: str
    structure: str
    close: float
    rsi: float | None
    rsi_previous: float | None
    change_24h: float | None
    ema20: float | None
    ema50: float | None
    ema200: float | None
    volume_ratio: float | None
    taker_buy_ratio: float | None
    taker_buy_ratio_5: float | None
    macd: float | None
    macd_signal: float | None
    atr_percent: float | None
    ema20_distance_percent: float | None
    breakout_20: bool
    ema20_touched: bool
    pullback_reversal: bool
    higher_low_confirmed: bool
    support: float | None
    resistance: float | None
    direction_score: int
    bottom_score: int
    patterns: list[PatternSignal] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    invalidation: str = ""


@dataclass
class HistoricalAnalog:
    start_date: str
    end_date: str
    similarity: int
    return_short: float | None
    return_long: float | None


@dataclass
class MarketAnalysis:
    symbol: str
    generated_at: str
    current_price: float
    direction_score: int
    signal_strength: int
    bias: str
    bottom_score: int
    timeframes: dict[str, TimeframeAnalysis]
    important_patterns: list[dict]
    analogs: list[HistoricalAnalog]
    wave_context: dict | None
    dominance_context: dict | None
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def ema(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    seed = statistics.fmean(values[:period])
    result[period - 1] = seed
    multiplier = 2 / (period + 1)
    current = seed
    for index in range(period, len(values)):
        current = (values[index] - current) * multiplier + current
        result[index] = current
    return result


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    avg_gain = statistics.fmean(gains[:period])
    avg_loss = statistics.fmean(losses[:period])
    result[period] = _rsi_value(avg_gain, avg_loss)
    for index in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[index - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index - 1]) / period
        result[index] = _rsi_value(avg_gain, avg_loss)
    return result


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def macd(values: list[float]) -> tuple[list[float | None], list[float | None]]:
    fast = ema(values, 12)
    slow = ema(values, 26)
    line: list[float | None] = [
        (a - b) if a is not None and b is not None else None
        for a, b in zip(fast, slow)
    ]
    valid = [value for value in line if value is not None]
    signal_valid = ema(valid, 9)
    signal: list[float | None] = [None] * len(values)
    offset = len(values) - len(valid)
    for index, value in enumerate(signal_valid):
        signal[offset + index] = value
    return line, signal


def atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None
    true_ranges = []
    for index in range(1, len(candles)):
        candle = candles[index]
        previous = candles[index - 1]
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous.close),
                abs(candle.low - previous.close),
            )
        )
    current = statistics.fmean(true_ranges[:period])
    for value in true_ranges[period:]:
        current = (current * (period - 1) + value) / period
    return current


def analyze_timeframe(interval: str, candles: list[Candle]) -> TimeframeAnalysis:
    if len(candles) < 30:
        raise ValueError(f"At least 30 closed candles are required for {interval}")
    closes = [candle.close for candle in candles]
    current = candles[-1]
    rsi_series = rsi(closes)
    ema20_series = ema(closes, 20)
    ema50_series = ema(closes, 50)
    ema200_series = ema(closes, 200)
    macd_line, macd_signal_line = macd(closes)
    highs = _pivots(candles, "high")
    lows = _pivots(candles, "low")
    structure = _structure(highs, lows)
    volume_average = statistics.fmean([candle.volume for candle in candles[-21:-1]])
    volume_ratio = current.volume / volume_average if volume_average else None
    taker_buy_ratio = _taker_buy_ratio([current])
    taker_buy_ratio_5 = _taker_buy_ratio(candles[-5:])
    atr_value = atr(candles)
    support, resistance = _support_resistance(current.close, highs, lows)
    patterns = _detect_patterns(candles, highs, lows, rsi_series)

    score, reasons = _direction_score(
        current.close,
        structure,
        rsi_series[-1],
        ema20_series[-1],
        ema50_series[-1],
        ema200_series[-1],
        macd_line[-1],
        macd_signal_line[-1],
        volume_ratio,
        patterns,
    )
    bottom_score = _bottom_score(
        candles,
        rsi_series[-1],
        support,
        atr_value,
        volume_ratio,
        patterns,
        structure,
    )
    invalidation = _invalidation(current.close, support, resistance, score)
    lookback_24h = {"15m": 96, "1h": 24, "4h": 6, "1d": 1}.get(interval, 24)
    change_24h = (
        (current.close / candles[-lookback_24h - 1].close - 1) * 100
        if len(candles) > lookback_24h
        else None
    )
    prior_highs = [candle.high for candle in candles[-21:-1]]
    breakout_20 = bool(prior_highs and current.close > max(prior_highs))
    current_ema20 = ema20_series[-1]
    ema20_touched = bool(
        current_ema20 is not None
        and current.low <= current_ema20 * 1.001
        and current.high >= current_ema20 * 0.999
    )
    pullback_reversal = _pullback_reversal(candles, atr_value, rsi_series)
    higher_low_confirmed = bool(len(lows) >= 2 and lows[-1][1] > lows[-2][1])
    return TimeframeAnalysis(
        interval=interval,
        structure=structure,
        close=current.close,
        rsi=_rounded(rsi_series[-1]),
        rsi_previous=_rounded(rsi_series[-2]),
        change_24h=_rounded(change_24h, 2),
        ema20=_rounded(ema20_series[-1]),
        ema50=_rounded(ema50_series[-1]),
        ema200=_rounded(ema200_series[-1]),
        volume_ratio=_rounded(volume_ratio, 3),
        taker_buy_ratio=_rounded(taker_buy_ratio, 4),
        taker_buy_ratio_5=_rounded(taker_buy_ratio_5, 4),
        macd=_rounded(macd_line[-1]),
        macd_signal=_rounded(macd_signal_line[-1]),
        atr_percent=_rounded((atr_value / current.close * 100) if atr_value else None, 2),
        ema20_distance_percent=_rounded(
            ((current.close / current_ema20 - 1) * 100) if current_ema20 else None,
            2,
        ),
        breakout_20=breakout_20,
        ema20_touched=ema20_touched,
        pullback_reversal=pullback_reversal,
        higher_low_confirmed=higher_low_confirmed,
        support=_rounded(support),
        resistance=_rounded(resistance),
        direction_score=score,
        bottom_score=bottom_score,
        patterns=patterns,
        reasons=reasons[:5],
        invalidation=invalidation,
    )


def _pullback_reversal(
    candles: list[Candle],
    atr_value: float | None,
    rsi_series: list[float | None],
) -> bool:
    """Confirm that a real pullback has started reversing on a closed candle."""
    if len(candles) < 9:
        return False

    current = candles[-1]
    previous = candles[-2]
    earlier = candles[-9:-5]
    later = candles[-5:-1]
    prior_peak = max(candle.high for candle in earlier)
    pullback_low = min(candle.low for candle in later)
    decline_percent = (
        (prior_peak - pullback_low) / prior_peak * 100 if prior_peak > 0 else 0.0
    )
    atr_percent = (
        atr_value / current.close * 100
        if atr_value is not None and current.close > 0
        else 0.0
    )
    meaningful_pullback = decline_percent >= max(0.35, atr_percent * 0.6)
    bullish_reclaim = current.close > current.open and current.close > previous.high
    rsi_turning_up = bool(
        len(rsi_series) >= 2
        and rsi_series[-1] is not None
        and rsi_series[-2] is not None
        and rsi_series[-1] > rsi_series[-2]
    )
    return meaningful_pullback and bullish_reclaim and rsi_turning_up


def _taker_buy_ratio(candles: list[Candle]) -> float | None:
    available = [
        candle
        for candle in candles
        if candle.taker_buy_volume is not None and candle.volume > 0
    ]
    if not available:
        return None
    total_volume = sum(candle.volume for candle in available)
    if total_volume <= 0:
        return None
    ratio = sum(float(candle.taker_buy_volume) for candle in available) / total_volume
    return max(0.0, min(ratio, 1.0))


def analyze_market(symbol: str, datasets: dict[str, list[Candle]]) -> MarketAnalysis:
    order = [interval for interval in ("1d", "4h", "1h", "15m") if interval in datasets]
    analyses = {
        interval: analyze_timeframe(interval, datasets[interval])
        for interval in order
    }
    if not analyses:
        raise ValueError("No timeframe data supplied")
    weights = {"1d": 0.35, "4h": 0.30, "1h": 0.20, "15m": 0.15}
    weight_sum = sum(weights[interval] for interval in analyses)
    direction_score = round(
        sum(analyses[interval].direction_score * weights[interval] for interval in analyses)
        / weight_sum
    )
    bottom_score = round(
        sum(analyses[interval].bottom_score * weights[interval] for interval in analyses)
        / weight_sum
    )
    all_patterns = [
        {"timeframe": interval, **asdict(pattern)}
        for interval, analysis in analyses.items()
        for pattern in analysis.patterns
    ]
    important = sorted(
        all_patterns,
        key=lambda item: (item["status"] == "confirmed", item["confidence"]),
        reverse=True,
    )[:6]
    agreement = _timeframe_agreement(list(analyses.values()))
    signal_strength = min(100, round(agreement * 0.45 + abs(direction_score - 50) * 1.1))
    primary_interval = "4h" if "4h" in datasets else order[0]
    analogs = find_historical_analogs(datasets[primary_interval])
    wave = analyze_wave_context(datasets[primary_interval])
    current_price = analyses[order[-1]].close
    warnings = [
        "패턴과 과거 유사도는 확률적 참고자료이며 미래 가격을 보장하지 않습니다.",
        "미완성 봉은 분석에서 제외했습니다.",
    ]
    return MarketAnalysis(
        symbol=symbol.upper(),
        generated_at=datetime.now(UTC).isoformat(),
        current_price=current_price,
        direction_score=direction_score,
        signal_strength=signal_strength,
        bias=_bias(direction_score),
        bottom_score=bottom_score,
        timeframes=analyses,
        important_patterns=important,
        analogs=analogs,
        wave_context=asdict(wave) if wave else None,
        dominance_context=None,
        warnings=warnings,
    )


def find_historical_analogs(
    candles: list[Candle], window: int = 24, short_horizon: int = 6, long_horizon: int = 30
) -> list[HistoricalAnalog]:
    if len(candles) < window + long_horizon + 60:
        return []
    closes = [candle.close for candle in candles]
    current_vector = _shape_vector(closes[-window:])
    candidates: list[tuple[float, int]] = []
    latest_start = len(candles) - window - long_horizon
    for start in range(0, latest_start, 4):
        vector = _shape_vector(closes[start:start + window])
        distance = math.sqrt(statistics.fmean((a - b) ** 2 for a, b in zip(current_vector, vector)))
        candidates.append((distance, start))
    selected: list[tuple[float, int]] = []
    for candidate in sorted(candidates):
        if all(abs(candidate[1] - prior[1]) >= window for prior in selected):
            selected.append(candidate)
        if len(selected) == 3:
            break

    results = []
    for distance, start in selected:
        end_index = start + window - 1
        base = closes[end_index]
        short_return = (closes[end_index + short_horizon] / base - 1) * 100
        long_return = (closes[end_index + long_horizon] / base - 1) * 100
        results.append(
            HistoricalAnalog(
                start_date=_date(candles[start].open_time),
                end_date=_date(candles[end_index].open_time),
                similarity=max(0, min(99, round(math.exp(-distance * 35) * 100))),
                return_short=round(short_return, 2),
                return_long=round(long_return, 2),
            )
        )
    return results


def _pivots(candles: list[Candle], field_name: str, left: int = 3, right: int = 3) -> list[tuple[int, float]]:
    result = []
    for index in range(left, len(candles) - right):
        value = getattr(candles[index], field_name)
        neighbors = [
            getattr(candles[candidate], field_name)
            for candidate in range(index - left, index + right + 1)
            if candidate != index
        ]
        previous_value = getattr(candles[index - 1], field_name)
        if field_name == "high" and value >= max(neighbors) and value > previous_value:
            result.append((index, value))
        elif field_name == "low" and value <= min(neighbors) and value < previous_value:
            result.append((index, value))
    return result


def _structure(highs: list[tuple[int, float]], lows: list[tuple[int, float]]) -> str:
    if len(highs) < 2 or len(lows) < 2:
        return "insufficient"
    higher_high = highs[-1][1] > highs[-2][1]
    higher_low = lows[-1][1] > lows[-2][1]
    if higher_high and higher_low:
        return "bullish"
    if not higher_high and not higher_low:
        return "bearish"
    if higher_low:
        return "bullish_transition"
    if not higher_high:
        return "bearish_transition"
    return "mixed"


def _support_resistance(close: float, highs, lows) -> tuple[float | None, float | None]:
    supports = [price for _, price in lows[-12:] if price < close]
    resistances = [price for _, price in highs[-12:] if price > close]
    return (max(supports) if supports else None, min(resistances) if resistances else None)


def _detect_patterns(candles, highs, lows, rsi_series) -> list[PatternSignal]:
    patterns: list[PatternSignal] = []
    patterns.extend(_candlestick_patterns(candles))
    patterns.extend(_double_patterns(candles, highs, lows))
    patterns.extend(_head_shoulders(highs, lows))
    patterns.extend(_consolidation_patterns(candles, highs, lows))
    cup = _cup_and_handle(candles)
    if cup:
        patterns.append(cup)
    flag = _flag(candles)
    if flag:
        patterns.append(flag)
    divergence = _divergence(candles, lows, highs, rsi_series)
    patterns.extend(divergence)
    trendline = _trendline_signal(candles, highs, lows)
    patterns.extend(trendline)
    return sorted(patterns, key=lambda item: item.confidence, reverse=True)[:8]


def _candlestick_patterns(candles: list[Candle]) -> list[PatternSignal]:
    current, previous = candles[-1], candles[-2]
    body = abs(current.close - current.open)
    span = max(current.high - current.low, 1e-12)
    upper = current.high - max(current.open, current.close)
    lower = min(current.open, current.close) - current.low
    result = []
    recent_decline = candles[-6].close > current.close
    recent_rise = candles[-6].close < current.close
    if body / span <= 0.1:
        result.append(PatternSignal("도지", "neutral", 55, "candidate", "몸통이 전체 변동폭의 10% 이하입니다."))
    if recent_decline and lower >= max(body * 2, span * 0.45) and upper <= span * 0.2:
        result.append(PatternSignal("망치형", "bullish", 65, "candidate", "하락 뒤 긴 아래꼬리가 나타났습니다."))
    if recent_rise and upper >= max(body * 2, span * 0.45) and lower <= span * 0.2:
        result.append(PatternSignal("유성형", "bearish", 65, "candidate", "상승 뒤 긴 위꼬리가 나타났습니다."))
    if previous.close < previous.open and current.close > current.open and current.open <= previous.close and current.close >= previous.open:
        result.append(PatternSignal("상승 장악형", "bullish", 72, "confirmed", "양봉 몸통이 직전 음봉 몸통을 감쌌습니다."))
    if previous.close > previous.open and current.close < current.open and current.open >= previous.close and current.close <= previous.open:
        result.append(PatternSignal("하락 장악형", "bearish", 72, "confirmed", "음봉 몸통이 직전 양봉 몸통을 감쌌습니다."))
    return result


def _double_patterns(candles, highs, lows) -> list[PatternSignal]:
    result = []
    close = candles[-1].close
    if len(lows) >= 2:
        first, second = lows[-2], lows[-1]
        separation = second[0] - first[0]
        difference = abs(second[1] - first[1]) / max(first[1], second[1])
        between = [high for index, high in highs if first[0] < index < second[0]]
        if separation >= 5 and difference <= 0.035 and between:
            neckline = max(between)
            confirmed = close > neckline
            result.append(PatternSignal("쌍바닥", "bullish", 82 if confirmed else 68, "confirmed" if confirmed else "candidate", f"두 저점 차이 {difference * 100:.1f}%, 넥라인 {neckline:.2f}."))
    if len(highs) >= 2:
        first, second = highs[-2], highs[-1]
        separation = second[0] - first[0]
        difference = abs(second[1] - first[1]) / max(first[1], second[1])
        between = [low for index, low in lows if first[0] < index < second[0]]
        if separation >= 5 and difference <= 0.035 and between:
            neckline = min(between)
            confirmed = close < neckline
            result.append(PatternSignal("쌍봉", "bearish", 82 if confirmed else 68, "confirmed" if confirmed else "candidate", f"두 고점 차이 {difference * 100:.1f}%, 넥라인 {neckline:.2f}."))
    return result


def _head_shoulders(highs, lows) -> list[PatternSignal]:
    result = []
    if len(highs) >= 3:
        left, head, right = highs[-3:]
        shoulders_close = abs(left[1] - right[1]) / max(left[1], right[1]) <= 0.05
        if shoulders_close and head[1] > max(left[1], right[1]) * 1.025:
            result.append(PatternSignal("헤드앤숄더", "bearish", 70, "candidate", "가운데 고점이 양쪽 어깨보다 높고 어깨 높이가 유사합니다."))
    if len(lows) >= 3:
        left, head, right = lows[-3:]
        shoulders_close = abs(left[1] - right[1]) / max(left[1], right[1]) <= 0.05
        if shoulders_close and head[1] < min(left[1], right[1]) * 0.975:
            result.append(PatternSignal("역헤드앤숄더", "bullish", 70, "candidate", "가운데 저점이 양쪽 어깨보다 낮고 어깨 높이가 유사합니다."))
    return result


def _consolidation_patterns(candles, highs, lows) -> list[PatternSignal]:
    if len(highs) < 3 or len(lows) < 3:
        return []
    recent_highs, recent_lows = highs[-3:], lows[-3:]
    high_slope = _slope(recent_highs)
    low_slope = _slope(recent_lows)
    result = []
    if high_slope < 0 < low_slope:
        current_index = len(candles) - 1
        upper_now = _project(recent_highs[-2], recent_highs[-1], current_index)
        upper_before = _project(recent_highs[-2], recent_highs[-1], current_index - 1)
        lower_now = _project(recent_lows[-2], recent_lows[-1], current_index)
        consolidation_start = min(recent_highs[0][0], recent_lows[0][0])
        impulse_start = max(0, consolidation_start - 20)
        impulse = candles[consolidation_start].close / candles[impulse_start].close - 1 if consolidation_start > impulse_start else 0
        broke_up = candles[-2].close <= upper_before and candles[-1].close > upper_now
        broke_down = candles[-2].close >= _project(recent_lows[-2], recent_lows[-1], current_index - 1) and candles[-1].close < lower_now
        pole_size = abs(candles[consolidation_start].close - candles[impulse_start].close)
        if impulse >= 0.08:
            target = candles[-1].close + pole_size
            result.append(PatternSignal(
                "상승 페넌트",
                "bullish",
                86 if broke_up else 72,
                "confirmed" if broke_up else "candidate",
                f"선행 상승 {impulse * 100:.1f}% 뒤 삼각수렴입니다. 상단 {upper_now:.2f}, 하단 {lower_now:.2f}, 돌파 시 단순 측정폭 참고값 {target:.2f}.",
            ))
        elif impulse <= -0.08:
            target = max(0, candles[-1].close - pole_size)
            result.append(PatternSignal(
                "하락 페넌트",
                "bearish",
                86 if broke_down else 72,
                "confirmed" if broke_down else "candidate",
                f"선행 하락 {impulse * 100:.1f}% 뒤 삼각수렴입니다. 상단 {upper_now:.2f}, 하단 {lower_now:.2f}, 이탈 시 단순 측정폭 참고값 {target:.2f}.",
            ))
        else:
            direction = "bullish" if broke_up else "bearish" if broke_down else "neutral"
            result.append(PatternSignal(
                "대칭 삼각수렴",
                direction,
                80 if (broke_up or broke_down) else 70,
                "confirmed" if (broke_up or broke_down) else "candidate",
                f"고점은 낮아지고 저점은 높아집니다. 상단 {upper_now:.2f}, 하단 {lower_now:.2f}.",
            ))
    elif high_slope < 0 and low_slope < 0 and abs(high_slope) > abs(low_slope) * 1.15:
        result.append(PatternSignal("하락 쐐기", "bullish", 65, "candidate", "두 경계가 하락하면서 폭이 좁아집니다."))
    elif high_slope > 0 and low_slope > 0 and low_slope > high_slope * 1.15:
        result.append(PatternSignal("상승 쐐기", "bearish", 65, "candidate", "두 경계가 상승하면서 폭이 좁아집니다."))
    return result


def _cup_and_handle(candles: list[Candle]) -> PatternSignal | None:
    if len(candles) < 60:
        return None
    sample = candles[-90:]
    size = len(sample)
    left_zone = sample[: max(10, size // 3)]
    middle_zone = sample[size // 4: size * 3 // 4]
    right_zone = sample[size * 2 // 3: -5]
    handle_zone = sample[-10:]
    if not right_zone:
        return None
    left_rim = max(c.high for c in left_zone)
    bottom = min(c.low for c in middle_zone)
    right_rim = max(c.high for c in right_zone)
    rim = (left_rim + right_rim) / 2
    depth = (rim - bottom) / rim
    rim_difference = abs(left_rim - right_rim) / rim
    handle_low = min(c.low for c in handle_zone)
    handle_depth = (rim - handle_low) / max(rim - bottom, 1e-12)
    near_rim = sample[-1].close >= rim * 0.93
    if 0.10 <= depth <= 0.50 and rim_difference <= 0.07 and handle_depth <= 0.55 and near_rim:
        confidence = round(60 + (0.07 - rim_difference) * 200 + (0.55 - handle_depth) * 15)
        confirmed = sample[-1].close > max(left_rim, right_rim)
        return PatternSignal("컵앤핸들", "bullish", min(88, confidence + (8 if confirmed else 0)), "confirmed" if confirmed else "candidate", f"컵 깊이 {depth * 100:.1f}%, 손잡이 되돌림 {handle_depth * 100:.1f}%입니다.")
    return None


def _flag(candles: list[Candle]) -> PatternSignal | None:
    if len(candles) < 35:
        return None
    impulse_start = candles[-25].close
    impulse_end = candles[-9].close
    impulse = impulse_end / impulse_start - 1
    consolidation = candles[-1].close / impulse_end - 1
    recent_range = (max(c.high for c in candles[-9:]) - min(c.low for c in candles[-9:])) / impulse_end
    if impulse >= 0.08 and -0.05 <= consolidation <= 0.02 and recent_range <= abs(impulse) * 0.65:
        return PatternSignal("상승 플래그", "bullish", 66, "candidate", f"선행 상승 {impulse * 100:.1f}% 뒤 완만한 조정입니다.")
    if impulse <= -0.08 and -0.02 <= consolidation <= 0.05 and recent_range <= abs(impulse) * 0.65:
        return PatternSignal("하락 플래그", "bearish", 66, "candidate", f"선행 하락 {impulse * 100:.1f}% 뒤 완만한 반등입니다.")
    return None


def _divergence(candles, lows, highs, rsi_series) -> list[PatternSignal]:
    result = []
    if len(lows) >= 2:
        first, second = lows[-2], lows[-1]
        first_rsi, second_rsi = rsi_series[first[0]], rsi_series[second[0]]
        if first_rsi is not None and second_rsi is not None and second[1] < first[1] and second_rsi > first_rsi + 2:
            result.append(PatternSignal("상승 다이버전스", "bullish", 75, "confirmed", "가격은 낮은 저점, RSI는 높은 저점을 만들었습니다."))
    if len(highs) >= 2:
        first, second = highs[-2], highs[-1]
        first_rsi, second_rsi = rsi_series[first[0]], rsi_series[second[0]]
        if first_rsi is not None and second_rsi is not None and second[1] > first[1] and second_rsi < first_rsi - 2:
            result.append(PatternSignal("하락 다이버전스", "bearish", 75, "confirmed", "가격은 높은 고점, RSI는 낮은 고점을 만들었습니다."))
    return result


def _trendline_signal(candles, highs, lows) -> list[PatternSignal]:
    result = []
    index = len(candles) - 1
    if len(highs) >= 2 and highs[-1][1] < highs[-2][1]:
        line_now = _project(highs[-2], highs[-1], index)
        line_before = _project(highs[-2], highs[-1], index - 1)
        if candles[-2].close <= line_before and candles[-1].close > line_now:
            result.append(PatternSignal("하락 추세선 돌파", "bullish", 78, "confirmed", f"종가가 추정 추세선 {line_now:.2f} 위로 마감했습니다."))
    if len(lows) >= 2 and lows[-1][1] > lows[-2][1]:
        line_now = _project(lows[-2], lows[-1], index)
        line_before = _project(lows[-2], lows[-1], index - 1)
        if candles[-2].close >= line_before and candles[-1].close < line_now:
            result.append(PatternSignal("상승 추세선 이탈", "bearish", 78, "confirmed", f"종가가 추정 추세선 {line_now:.2f} 아래로 마감했습니다."))
    return result


def _direction_score(close, structure, rsi_value, ema20_value, ema50_value, ema200_value, macd_value, macd_signal_value, volume_ratio, patterns):
    score = 50
    reasons = []
    if structure == "bullish":
        score += 14; reasons.append("고점과 저점이 함께 높아지는 상승 구조입니다.")
    elif structure == "bearish":
        score -= 14; reasons.append("고점과 저점이 함께 낮아지는 하락 구조입니다.")
    elif structure == "bullish_transition":
        score += 6; reasons.append("저점이 높아져 상승 전환을 시도합니다.")
    elif structure == "bearish_transition":
        score -= 6; reasons.append("고점이 낮아져 하락 전환 가능성이 있습니다.")
    if ema20_value is not None and ema50_value is not None:
        if close > ema20_value > ema50_value:
            score += 10; reasons.append("가격과 EMA 20·50 배열이 단기 상승 우위입니다.")
        elif close < ema20_value < ema50_value:
            score -= 10; reasons.append("가격과 EMA 20·50 배열이 단기 하락 우위입니다.")
    if ema200_value is not None:
        if close > ema200_value:
            score += 6; reasons.append("가격이 EMA 200 위에 있습니다.")
        else:
            score -= 6; reasons.append("가격이 EMA 200 아래에 있습니다.")
    if rsi_value is not None:
        if 52 <= rsi_value <= 68:
            score += 5; reasons.append(f"RSI {rsi_value:.1f}로 상승 모멘텀이 우세합니다.")
        elif 32 <= rsi_value <= 48:
            score -= 5; reasons.append(f"RSI {rsi_value:.1f}로 하락 모멘텀이 우세합니다.")
        elif rsi_value >= 75:
            score -= 3; reasons.append(f"RSI {rsi_value:.1f}로 단기 과열 위험이 있습니다.")
    if macd_value is not None and macd_signal_value is not None:
        score += 5 if macd_value > macd_signal_value else -5
    pattern_effect = sum((1 if p.direction == "bullish" else -1 if p.direction == "bearish" else 0) * p.confidence / 20 for p in patterns[:3])
    if volume_ratio and volume_ratio >= 1.3 and pattern_effect:
        pattern_effect *= 1.2
        reasons.append(f"거래량이 평균의 {volume_ratio:.2f}배로 신호를 강화합니다.")
    score += round(pattern_effect)
    return max(0, min(100, score)), reasons


def _bottom_score(candles, rsi_value, support, atr_value, volume_ratio, patterns, structure) -> int:
    score = 0
    close = candles[-1].close
    if rsi_value is not None and rsi_value <= 35:
        score += 18
    if support and atr_value and abs(close - support) <= atr_value * 1.25:
        score += 15
    if volume_ratio and volume_ratio >= 1.5 and candles[-1].close > candles[-1].open:
        score += 12
    if structure == "bullish_transition":
        score += 15
    names = {pattern.name for pattern in patterns if pattern.direction == "bullish"}
    if "상승 다이버전스" in names:
        score += 24
    if "쌍바닥" in names or "역헤드앤숄더" in names:
        score += 20
    if "망치형" in names or "상승 장악형" in names:
        score += 10
    return min(100, score)


def _invalidation(close: float, support: float | None, resistance: float | None, score: int) -> str:
    if score >= 55 and support:
        return f"종가가 최근 지지 {support:.2f} 아래로 내려가면 상승 해석이 약해집니다."
    if score < 45 and resistance:
        return f"종가가 최근 저항 {resistance:.2f} 위로 올라가면 하락 해석이 약해집니다."
    return "다음 확정봉에서 구조와 거래량이 같은 방향으로 이어지는지 확인하세요."


def _timeframe_agreement(analyses: list[TimeframeAnalysis]) -> int:
    if not analyses:
        return 0
    bullish = sum(analysis.direction_score >= 55 for analysis in analyses)
    bearish = sum(analysis.direction_score <= 45 for analysis in analyses)
    return round(max(bullish, bearish) / len(analyses) * 100)


def _shape_vector(values: list[float]) -> list[float]:
    return [math.log(values[index] / values[index - 1]) for index in range(1, len(values))]


def _slope(points: list[tuple[int, float]]) -> float:
    if len(points) < 2:
        return 0.0
    x_mean = statistics.fmean(point[0] for point in points)
    y_mean = statistics.fmean(point[1] for point in points)
    denominator = sum((point[0] - x_mean) ** 2 for point in points)
    if denominator == 0 or y_mean == 0:
        return 0.0
    return sum((point[0] - x_mean) * (point[1] - y_mean) for point in points) / denominator / y_mean


def _project(first: tuple[int, float], second: tuple[int, float], target: int) -> float:
    slope = (second[1] - first[1]) / max(second[0] - first[0], 1)
    return second[1] + slope * (target - second[0])


def _bias(score: int) -> str:
    if score >= 65:
        return "bullish"
    if score >= 55:
        return "bullish_attempt"
    if score <= 35:
        return "bearish"
    if score <= 45:
        return "bearish_warning"
    return "neutral"


def _rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
