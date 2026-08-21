from __future__ import annotations

import os
from dataclasses import dataclass

from analysis_engine import MarketAnalysis, TimeframeAnalysis
from indicator_summary import ema_alignment_label


@dataclass(frozen=True)
class CapitalPlan:
    planned_krw: int
    first_percent: float
    first_krw: int
    second_percent: float
    second_krw: int
    third_percent: float
    third_krw: int
    reserve_percent: float
    reserve_krw: int


@dataclass(frozen=True)
class EntryDecision:
    score: int
    action: str
    action_label: str
    setup: str
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    entry_low: float | None
    entry_high: float | None
    invalidation: float | None
    capital_plan: CapitalPlan

    @property
    def explanation(self) -> str:
        if self.blockers:
            return " · ".join(self.blockers[:2])
        if self.reasons:
            return " · ".join(self.reasons[:3])
        return "아직 진입 근거가 충분히 모이지 않았습니다."


def evaluate_entry(analysis: MarketAnalysis) -> EntryDecision:
    capital_plan = _capital_plan(analysis.symbol)
    one_day = analysis.timeframes.get("1d")
    four_hour = analysis.timeframes.get("4h")
    one_hour = analysis.timeframes.get("1h")
    fifteen = analysis.timeframes.get("15m")
    if fifteen is None or one_hour is None or four_hour is None:
        return EntryDecision(
            score=0,
            action="wait",
            action_label="관망",
            setup="데이터 부족",
            reasons=(),
            blockers=("15m·1H·4H 확정봉 데이터가 부족합니다.",),
            entry_low=None,
            entry_high=None,
            invalidation=None,
            capital_plan=capital_plan,
        )

    score = 0
    reasons: list[str] = []
    blockers: list[str] = []

    for label, detail, direction_points, ema_points, alignment_points in (
        ("1D", one_day, 3, 3, 3),
        ("4H", four_hour, 8, 6, 6),
        ("1H", one_hour, 8, 6, 6),
    ):
        if detail is None:
            continue
        if detail.direction_score >= 55:
            score += direction_points
            reasons.append(f"{label} 방향 우위")
        if detail.ema20 is not None and detail.close >= detail.ema20:
            score += ema_points
            reasons.append(f"{label} EMA20 위")
        if ema_alignment_label(detail) == "정배열":
            score += alignment_points
            reasons.append(f"{label} 정배열")

    flow_confirmed = bool(
        (fifteen.volume_ratio or 0) >= 1.2
        and (fifteen.taker_buy_ratio or 0) >= 0.54
    )
    breakout = bool(fifteen.breakout_20 and flow_confirmed)
    pullback = bool(
        fifteen.ema20_touched
        and fifteen.ema20 is not None
        and fifteen.close >= fifteen.ema20
        and one_hour.ema20 is not None
        and one_hour.close >= one_hour.ema20
        and flow_confirmed
    )
    oversold_recovery = bool(
        one_hour.rsi is not None
        and one_hour.rsi_previous is not None
        and one_hour.rsi_previous <= 30 < one_hour.rsi
    )
    if breakout:
        setup = "15m 돌파 확인"
        score += 20
        reasons.append("15m 20봉 고점 돌파")
    elif pullback:
        setup = "EMA20 눌림 지지"
        score += 16
        reasons.append("15m EMA20 접촉 후 위 마감")
    elif oversold_recovery:
        setup = "1H 과매도 회복"
        score += 18
        reasons.append("1H RSI 30 회복")
    else:
        setup = "진입 조건 미완성"

    if fifteen.volume_ratio is not None:
        if fifteen.volume_ratio >= 1.5:
            score += 10
            reasons.append(f"15m 거래량 {fifteen.volume_ratio:.1f}배")
        elif fifteen.volume_ratio >= 1.2:
            score += 5
            reasons.append(f"15m 거래량 {fifteen.volume_ratio:.1f}배")
    if fifteen.taker_buy_ratio is not None:
        if fifteen.taker_buy_ratio >= 0.60:
            score += 10
            reasons.append(f"15m 체결매수 {fifteen.taker_buy_ratio * 100:.0f}%")
        elif fifteen.taker_buy_ratio >= 0.54:
            score += 5
            reasons.append(f"15m 체결매수 {fifteen.taker_buy_ratio * 100:.0f}%")
    if fifteen.rsi is not None:
        if 50 <= fifteen.rsi <= 68:
            score += 8
            reasons.append(f"15m RSI {fifteen.rsi:.0f} 상승 여력")
        elif 68 < fifteen.rsi <= 75:
            score += 4
            reasons.append(f"15m RSI {fifteen.rsi:.0f} 강한 모멘텀")
        elif fifteen.rsi >= 80:
            score -= 10

    distance = abs(fifteen.ema20_distance_percent or 0.0)
    atr_percent = max(fifteen.atr_percent or 0.0, 0.1)
    if distance <= max(0.6, atr_percent):
        score += 8
        reasons.append("EMA20과 가까워 손절 기준 설정 가능")

    wave = analysis.wave_context or {}
    wave_direction = wave.get("direction")
    wave_extension = float(wave.get("current_extension", 0.0) or 0.0)
    if wave_direction == "bullish" and 0.0 <= wave_extension < 1.55:
        score += 5
        reasons.append(f"4H 상승 파동 {wave_extension:.2f}배 진행 후보")
    elif wave_direction == "bearish" and wave_extension >= 1.0:
        blockers.append("4H 하락 파동 진행 후보")

    change_24h = fifteen.change_24h or one_hour.change_24h or 0.0
    if change_24h >= 8:
        blockers.append(f"24시간 {change_24h:+.1f}% 급등 후 추격 위험")
    if _extended(one_hour, minimum_distance=3.0, atr_multiple=2.5, rsi_limit=75):
        blockers.append(
            f"1H RSI {one_hour.rsi:.0f}·EMA20 이격 {one_hour.ema20_distance_percent:+.1f}% 과열"
        )
    if _extended(fifteen, minimum_distance=1.5, atr_multiple=2.0, rsi_limit=82):
        blockers.append(
            f"15m RSI {fifteen.rsi:.0f}·EMA20 이격 {fifteen.ema20_distance_percent:+.1f}% 급등"
        )
    if _extended(four_hour, minimum_distance=6.0, atr_multiple=3.0, rsi_limit=80):
        blockers.append(
            f"4H RSI {four_hour.rsi:.0f}·EMA20 이격 {four_hour.ema20_distance_percent:+.1f}% 과열"
        )
    if wave_direction == "bullish" and wave_extension >= 1.55 and (fifteen.rsi or 0) >= 70:
        blockers.append(f"상승 파동 {wave_extension:.2f}배 확장·피보나치 저항 접근")

    dominance = analysis.dominance_context or {}
    dominance_regime = dominance.get("regime")
    dominance_change = dominance.get("change_24h_pp")
    if dominance_regime == "risk_off":
        blockers.append(
            "USDT.D 상승+전체 시총 하락 · 시장 전체 현금 대피 경계"
        )
    elif dominance_regime == "risk_on":
        score += 4
        reasons.append(
            f"USDT.D {float(dominance_change or 0):+.2f}%p 하락 · 시장 위험선호 우호"
        )

    score = max(0, min(round(score), 100))
    has_trigger = breakout or pullback or oversold_recovery
    if blockers:
        action = "wait_pullback"
        action_label = "추격 금지 · 눌림 대기"
    elif score >= 65 and has_trigger:
        action = "first_entry_review"
        action_label = "1차 분할 진입 검토"
    elif score >= 52:
        action = "watch"
        action_label = "진입 준비 · 확정봉 대기"
    else:
        action = "wait"
        action_label = "관망"

    entry_low, entry_high, invalidation = _price_plan(fifteen, action)
    return EntryDecision(
        score=score,
        action=action,
        action_label=action_label,
        setup=setup,
        reasons=tuple(dict.fromkeys(reasons)),
        blockers=tuple(dict.fromkeys(blockers)),
        entry_low=entry_low,
        entry_high=entry_high,
        invalidation=invalidation,
        capital_plan=capital_plan,
    )


def format_krw(amount: int) -> str:
    amount = max(0, int(amount))
    if amount >= 100_000_000:
        value = amount / 100_000_000
        return f"{value:.1f}억원" if value % 1 else f"{int(value)}억원"
    return f"{amount // 10_000:,}만원"


def capital_action_line(decision: EntryDecision) -> str:
    plan = decision.capital_plan
    if plan.planned_krw <= 0:
        return "계획금액 미설정 · 자동 주문 없음"
    first = f"1차 {format_krw(plan.first_krw)}({plan.first_percent:g}%)"
    if decision.action == "first_entry_review":
        return f"{first} 분할 진입 검토 · 자동 주문 없음"
    return f"지금 신규진입 0원 · 조건 충족 시 {first} 검토"


def capital_plan_line(decision: EntryDecision) -> str:
    plan = decision.capital_plan
    if plan.planned_krw <= 0:
        return ""
    return (
        f"계획 {format_krw(plan.planned_krw)} · "
        f"1차 {format_krw(plan.first_krw)} · "
        f"2차 {format_krw(plan.second_krw)} · "
        f"3차 {format_krw(plan.third_krw)} · "
        f"예비 {format_krw(plan.reserve_krw)}"
    )


def entry_headline(symbol: str, decision: EntryDecision) -> str:
    coin = symbol.upper().removesuffix("USDT")
    first_amount = (
        format_krw(decision.capital_plan.first_krw)
        if decision.capital_plan.planned_krw > 0
        else "금액 미설정"
    )
    if decision.action == "first_entry_review":
        return f"🟢 {coin} · 1차 분할 진입 검토 · {first_amount}"
    if decision.action == "wait_pullback":
        return f"🔴 {coin} · 지금 신규진입 0원 · 추격 금지"
    if decision.action == "watch":
        return f"🟡 {coin} · 진입 준비 · 1차 {first_amount} 대기"
    return f"⚪ {coin} · 관망 · 지금 신규진입 0원"


def _capital_plan(symbol: str) -> CapitalPlan:
    raw = os.getenv(
        f"PLANNED_CAPITAL_KRW_{symbol.upper()}",
        os.getenv("PLANNED_CAPITAL_KRW_PER_SYMBOL", "0"),
    )
    try:
        planned = max(0, int(float(raw)))
    except (TypeError, ValueError):
        planned = 0

    first = _percentage("ENTRY_FIRST_TRANCHE_PERCENT", 10.0)
    second = _percentage("ENTRY_SECOND_TRANCHE_PERCENT", 15.0)
    third = _percentage("ENTRY_THIRD_TRANCHE_PERCENT", 25.0)
    total = first + second + third
    if total > 100:
        scale = 100 / total
        first, second, third = first * scale, second * scale, third * scale
    reserve = max(0.0, 100 - first - second - third)
    return CapitalPlan(
        planned_krw=planned,
        first_percent=first,
        first_krw=_rounded_amount(planned, first),
        second_percent=second,
        second_krw=_rounded_amount(planned, second),
        third_percent=third,
        third_krw=_rounded_amount(planned, third),
        reserve_percent=reserve,
        reserve_krw=_rounded_amount(planned, reserve),
    )


def _percentage(name: str, default: float) -> float:
    try:
        return max(0.0, min(float(os.getenv(name, str(default))), 100.0))
    except (TypeError, ValueError):
        return default


def _rounded_amount(planned: int, percent: float) -> int:
    return int(round(planned * percent / 100 / 10_000) * 10_000)


def _extended(
    detail: TimeframeAnalysis,
    *,
    minimum_distance: float,
    atr_multiple: float,
    rsi_limit: float,
) -> bool:
    if (
        detail.rsi is None
        or detail.ema20_distance_percent is None
        or detail.atr_percent is None
    ):
        return False
    threshold = max(minimum_distance, detail.atr_percent * atr_multiple)
    return detail.rsi >= rsi_limit and detail.ema20_distance_percent >= threshold


def _price_plan(
    detail: TimeframeAnalysis,
    action: str,
) -> tuple[float | None, float | None, float | None]:
    if action != "first_entry_review":
        return None, None, None
    atr_amount = detail.close * max(detail.atr_percent or 0.6, 0.3) / 100
    entry_low = detail.close - atr_amount * 0.35
    entry_high = detail.close + atr_amount * 0.15
    if detail.ema20 is not None:
        invalidation = detail.ema20 - atr_amount * 0.35
    else:
        invalidation = detail.close - atr_amount * 1.5
    return round(entry_low, 8), round(entry_high, 8), round(invalidation, 8)
