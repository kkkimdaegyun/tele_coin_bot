from __future__ import annotations

from dataclasses import dataclass

from analysis_engine import MarketAnalysis
from entry_strategy import capital_guidance_enabled, evaluate_entry, format_krw
from strategy_universe import is_trade_symbol


@dataclass(frozen=True)
class PositionDecision:
    action: str
    label: str
    reason: str
    amount_krw: int
    critical: bool = False


def estimate_invalidation(analysis: MarketAnalysis, entry_price: float) -> float:
    detail = analysis.timeframes.get("1h") or analysis.timeframes.get("4h")
    fallback = entry_price * 0.97
    if detail is None:
        return fallback
    atr_amount = detail.close * max(detail.atr_percent or 0.8, 0.3) / 100
    candidates = []
    if detail.support is not None and 0 < detail.support < entry_price:
        candidates.append(detail.support - atr_amount * 0.25)
    if detail.ema20 is not None and 0 < detail.ema20 < entry_price:
        candidates.append(detail.ema20 - atr_amount * 0.35)
    line = max(candidates) if candidates else fallback
    return round(min(line, entry_price * 0.995), 8)


def evaluate_position(
    analysis: MarketAnalysis,
    position: dict,
) -> PositionDecision:
    current = float(analysis.current_price)
    average = float(position["average_entry_price"])
    stage = int(position["stage"])
    invalidation = position.get("invalidation_price")
    invalidation = float(invalidation) if invalidation is not None else None
    entry = evaluate_entry(analysis)
    fifteen = analysis.timeframes.get("15m")
    one_hour = analysis.timeframes.get("1h")
    four_hour = analysis.timeframes.get("4h")

    if invalidation is not None and current <= invalidation:
        return PositionDecision(
            action="risk_exit_review",
            label="SELL 위험 · 손절/축소 검토",
            reason=(
                f"현재가가 기록 당시 보호 기준 아래입니다. "
                f"추가매수보다 위험 축소를 먼저 확인하세요."
            ),
            amount_krw=0,
            critical=True,
        )

    if (
        four_hour is not None
        and four_hour.ema20 is not None
        and four_hour.close < four_hour.ema20
        and four_hour.direction_score <= 42
    ):
        return PositionDecision(
            action="reduce_review",
            label="SELL 후보 · 4H 추세 약화",
            reason="4H EMA20 아래 마감과 방향 약화가 함께 확인됐습니다.",
            amount_krw=0,
            critical=True,
        )

    risk = average - invalidation if invalidation is not None else 0.0
    reward_multiple = (current - average) / risk if risk > 0 else 0.0
    near_resistance = bool(
        four_hour is not None
        and four_hour.resistance is not None
        and current >= four_hour.resistance * 0.995
    )
    overheated_with_selling = bool(
        four_hour is not None
        and (four_hour.rsi or 0) >= 72
        and (four_hour.taker_buy_ratio_5 or 0.5) <= 0.46
    )
    if reward_multiple >= 2 and (near_resistance or overheated_with_selling):
        return PositionDecision(
            action="partial_profit_review",
            label="SELL 후보 · 일부 분할매도 검토",
            reason=(
                f"기록한 위험폭 대비 약 {reward_multiple:.1f}배 상승했고 "
                "저항 또는 매도 체결 우위가 감지됐습니다."
            ),
            amount_krw=0,
            critical=False,
        )

    flow_confirmed = bool(
        fifteen is not None
        and (fifteen.volume_ratio or 0) >= 1.2
        and (fifteen.taker_buy_ratio or 0) >= 0.54
    )
    if (
        is_trade_symbol(analysis.symbol)
        and capital_guidance_enabled()
        and stage == 1
        and fifteen is not None
        and one_hour is not None
        and fifteen.pullback_reversal
        and one_hour.higher_low_confirmed
        and flow_confirmed
        and entry.action != "wait_pullback"
        and current <= average * 1.05
    ):
        return PositionDecision(
            action="second_entry_review",
            label=f"BUY 후보 · 2차 {format_krw(entry.capital_plan.second_krw)} 검토",
            reason="재조정 뒤 반등과 1H 저점 상승, 거래량·체결매수가 확인됐습니다.",
            amount_krw=entry.capital_plan.second_krw,
        )

    if (
        is_trade_symbol(analysis.symbol)
        and capital_guidance_enabled()
        and stage == 2
        and four_hour is not None
        and one_hour is not None
        and four_hour.breakout_20
        and four_hour.direction_score >= 55
        and four_hour.ema20 is not None
        and one_hour.ema20 is not None
        and four_hour.close >= four_hour.ema20
        and one_hour.close >= one_hour.ema20
        and current <= average * 1.10
        and entry.action != "wait_pullback"
    ):
        return PositionDecision(
            action="third_entry_review",
            label=f"BUY 후보 · 3차 {format_krw(entry.capital_plan.third_krw)} 검토",
            reason="4H 20봉 고점 돌파와 1H·4H EMA20 상단 유지가 확인됐습니다.",
            amount_krw=entry.capital_plan.third_krw,
        )

    return PositionDecision(
        action="hold",
        label="보유 관찰",
        reason="추가매수·축소·분할매도 조건이 아직 확정되지 않았습니다.",
        amount_krw=0,
    )


def position_change_percent(position: dict, current_price: float) -> float:
    average = float(position["average_entry_price"])
    return (float(current_price) / average - 1) * 100 if average > 0 else 0.0
