from __future__ import annotations

from dataclasses import dataclass

from analysis_engine import TimeframeAnalysis


@dataclass(frozen=True)
class WeeklyDecision:
    action: str
    label: str
    score: int
    reasons: tuple[str, ...]


def evaluate_weekly_cycle(detail: TimeframeAnalysis) -> WeeklyDecision:
    reasons: list[str] = []
    score = 0
    rsi = detail.rsi or 50.0
    rsi_previous = detail.rsi_previous or rsi

    if detail.pullback_reversal:
        score += 35
        reasons.append("주봉 조정 뒤 양봉·직전 주 고점 회복")
    if 30 <= rsi <= 58 and rsi > rsi_previous:
        score += 20
        reasons.append(f"주봉 RSI {rsi:.0f} 반등")
    if (detail.volume_ratio or 0) >= 0.9:
        score += 15
        reasons.append(f"주봉 거래량 {detail.volume_ratio:.1f}배")
    if detail.higher_low_confirmed:
        score += 15
        reasons.append("주봉 저점 상승")
    if detail.ema50 is not None and detail.close <= detail.ema50 * 1.05:
        score += 10
        reasons.append("주봉 EMA50 과대 추격 구간 아님")
    if detail.bottom_score >= 50:
        score += 5
        reasons.append(f"바닥 단서 {detail.bottom_score}/100(확률 아님)")

    buy_candidate = bool(
        detail.pullback_reversal
        and rsi > rsi_previous
        and (detail.volume_ratio or 0) >= 0.9
        and score >= 65
    )
    sell_candidate = bool(
        detail.ema20 is not None
        and detail.close < detail.ema20
        and rsi < rsi_previous
        and detail.direction_score <= 42
        and (detail.volume_ratio or 0) >= 1.0
    )
    if sell_candidate:
        return WeeklyDecision(
            "sell_candidate",
            "주봉 SELL · 축소 검토",
            max(65, 100 - detail.direction_score),
            (
                "주봉 EMA20 아래 마감",
                f"주봉 RSI {rsi_previous:.0f}→{rsi:.0f} 약화",
                f"주봉 거래량 {detail.volume_ratio:.1f}배",
            ),
        )
    if buy_candidate:
        return WeeklyDecision(
            "buy_candidate",
            "주봉 BUY · 장기 매집 후보",
            min(score, 100),
            tuple(reasons),
        )
    return WeeklyDecision(
        "wait",
        "주봉 WAIT",
        min(score, 100),
        tuple(reasons) or ("주봉 반전 조건이 아직 부족합니다.",),
    )
