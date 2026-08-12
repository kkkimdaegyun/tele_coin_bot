from __future__ import annotations

from dataclasses import dataclass

from analysis_engine import MarketAnalysis, TimeframeAnalysis
from formatter import _fmt_price


TIMEFRAME_LABELS = {"1d": "1D", "4h": "4H", "1h": "1H", "15m": "15m"}


@dataclass(frozen=True)
class InvestorNotice:
    level: str
    title: str
    detail: str


@dataclass(frozen=True)
class BottomDecision:
    score: int
    stage: str
    action: str
    confirmation_ready: bool
    confirmations: tuple[str, ...]


def build_bottom_decision(analysis: MarketAnalysis) -> BottomDecision:
    """Translate the evidence score into an explicit, non-probabilistic rule."""
    score = analysis.bottom_score
    detail = analysis.timeframes.get("4h") or analysis.timeframes.get("1h") or next(
        iter(analysis.timeframes.values())
    )
    confirmations: list[str] = []
    has_structure = detail.structure in {"bullish", "bullish_transition"}
    if has_structure:
        confirmations.append("4H 상승 구조")
    has_ema_recovery = detail.ema20 is not None and detail.close >= detail.ema20
    if has_ema_recovery:
        confirmations.append("4H EMA20 회복")
    has_volume = detail.volume_ratio is not None and detail.volume_ratio >= 1.0
    if has_volume:
        confirmations.append("4H 평균 이상 거래량")
    has_confirmed_pattern = any(
        item.get("direction") == "bullish"
        and item.get("status") == "confirmed"
        and int(item.get("confidence", 0)) >= 75
        and item.get("timeframe") in {"4h", "1h"}
        for item in analysis.important_patterns
    )
    if has_confirmed_pattern:
        confirmations.append("확인된 상승 패턴")

    confirmation_ready = (
        has_ema_recovery
        and (has_structure or has_confirmed_pattern)
        and (has_volume or has_confirmed_pattern)
        and len(confirmations) >= 2
    )

    if score < 25:
        return BottomDecision(
            score,
            "바닥 근거 부족",
            "바닥 매수 판단 보류",
            False,
            tuple(confirmations),
        )
    if score < 50:
        return BottomDecision(
            score,
            "초기 단서",
            "신규 진입 보류 · 구조와 거래량 관찰",
            False,
            tuple(confirmations),
        )
    if score < 75:
        return BottomDecision(
            score,
            "후보 형성",
            "확인봉·거래량 전 진입 대기",
            False,
            tuple(confirmations),
        )
    if confirmation_ready:
        return BottomDecision(
            score,
            "강한 후보·확인 동반",
            "소액 분할 진입 검토 가능 · 자동 매수 아님",
            True,
            tuple(confirmations),
        )
    return BottomDecision(
        score,
        "강한 후보지만 미확정",
        "확인 신호 전 신규 진입 대기",
        False,
        tuple(confirmations),
    )


def build_investor_notices(
    analysis: MarketAnalysis,
    detail: TimeframeAnalysis | None = None,
    *,
    limit: int = 4,
) -> list[InvestorNotice]:
    """Return the most decision-relevant facts without issuing a trade command."""
    detail = detail or analysis.timeframes.get("4h") or next(iter(analysis.timeframes.values()))
    label = TIMEFRAME_LABELS.get(detail.interval, detail.interval)
    current = analysis.current_price
    notices: list[InvestorNotice] = []

    if analysis.direction_score >= 55 and detail.support is not None:
        distance = (detail.support / current - 1) * 100
        notices.append(
            InvestorNotice(
                "risk",
                "상승 해석 무효화",
                f"{label} 종가가 지지 {_fmt_price(detail.support)} 아래로 내려가면 현재 상승 해석이 약해집니다 "
                f"(현재가 대비 {distance:+.1f}%).",
            )
        )
    elif analysis.direction_score <= 45 and detail.resistance is not None:
        distance = (detail.resistance / current - 1) * 100
        notices.append(
            InvestorNotice(
                "risk",
                "하락 해석 무효화",
                f"{label} 종가가 저항 {_fmt_price(detail.resistance)} 위로 회복하면 현재 하락 해석이 약해집니다 "
                f"(현재가 대비 {distance:+.1f}%).",
            )
        )
    elif detail.support is not None and detail.resistance is not None:
        notices.append(
            InvestorNotice(
                "caution",
                "혼조 범위",
                f"{label} 지지 {_fmt_price(detail.support)}와 저항 {_fmt_price(detail.resistance)} 사이에서는 방향 확정보다 이탈 확인이 우선입니다.",
            )
        )

    bullish = [
        TIMEFRAME_LABELS.get(interval, interval)
        for interval, timeframe in analysis.timeframes.items()
        if timeframe.direction_score >= 55
    ]
    bearish = [
        TIMEFRAME_LABELS.get(interval, interval)
        for interval, timeframe in analysis.timeframes.items()
        if timeframe.direction_score <= 45
    ]
    if bullish and bearish:
        notices.append(
            InvestorNotice(
                "caution",
                "시간대 충돌",
                f"상승 우위 {', '.join(bullish)} · 하락 우위 {', '.join(bearish)}입니다. 단기 반등을 장기 추세 전환으로 단정하지 마세요.",
            )
        )

    atr_threshold = max(detail.atr_percent or 0.0, 0.8)
    if detail.resistance is not None and current <= detail.resistance:
        distance = (detail.resistance / current - 1) * 100
        if 0 <= distance <= atr_threshold:
            notices.append(
                InvestorNotice(
                    "caution",
                    "저항 근접",
                    f"저항 {_fmt_price(detail.resistance)}까지 {distance:.1f}%입니다. 종가 돌파와 거래량 확인 전 추격은 되밀림 위험이 있습니다.",
                )
            )
    if detail.support is not None and current >= detail.support:
        distance = (current / detail.support - 1) * 100
        if 0 <= distance <= atr_threshold:
            notices.append(
                InvestorNotice(
                    "caution",
                    "지지 시험",
                    f"지지 {_fmt_price(detail.support)}까지 {distance:.1f}%입니다. 종가 이탈 시 다음 지지까지 변동이 빨라질 수 있습니다.",
                )
            )

    if detail.volume_ratio is not None and detail.volume_ratio < 0.8:
        notices.append(
            InvestorNotice(
                "caution",
                "거래량 부족",
                f"{label} 거래량이 20봉 평균의 {detail.volume_ratio:.2f}배입니다. 가격 움직임의 확인 강도가 약합니다.",
            )
        )
    elif detail.volume_ratio is not None and detail.volume_ratio >= 1.5:
        notices.append(
            InvestorNotice(
                "info",
                "거래량 확대",
                f"{label} 거래량이 20봉 평균의 {detail.volume_ratio:.2f}배입니다. 돌파인지 투매인지 봉 마감 방향을 함께 확인하세요.",
            )
        )

    if detail.atr_percent is not None and detail.atr_percent >= 3.0:
        notices.append(
            InvestorNotice(
                "risk",
                "변동성 확대",
                f"{label} ATR이 가격의 {detail.atr_percent:.1f}%입니다. 평소와 같은 수량을 사용하면 원화 손실폭도 커질 수 있습니다.",
            )
        )

    if detail.rsi is not None and detail.rsi >= 70:
        notices.append(
            InvestorNotice("caution", "RSI 과열", f"{label} RSI {detail.rsi:.1f}로 과매수권입니다. 강한 추세일 수 있지만 추격 진입 위험도 커집니다.")
        )
    elif detail.rsi is not None and detail.rsi <= 30:
        notices.append(
            InvestorNotice("caution", "RSI 과매도", f"{label} RSI {detail.rsi:.1f}로 과매도권입니다. 반등 가능성과 하락 추세 지속을 모두 열어둬야 합니다.")
        )

    if analysis.signal_strength < 35:
        notices.append(
            InvestorNotice(
                "info",
                "낮은 확신도",
                f"신호 강도 {analysis.signal_strength}/100으로 방향 근거가 약합니다. 관망도 유효한 판단입니다.",
            )
        )

    unique: list[InvestorNotice] = []
    used_titles: set[str] = set()
    for notice in notices:
        if notice.title in used_titles:
            continue
        used_titles.add(notice.title)
        unique.append(notice)
    return unique[: max(1, limit)]


def investor_notice_line(notice: InvestorNotice) -> str:
    return f"{notice.title}: {notice.detail}"
