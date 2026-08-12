from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from html import escape
from typing import TYPE_CHECKING

from analysis_engine import MarketAnalysis, TimeframeAnalysis
from formatter import _fmt_price
from investor_guidance import build_bottom_decision, build_investor_notices

if TYPE_CHECKING:
    from ema_signals import EmaSignal


TIMEFRAME_LABELS = {"1d": "1D", "4h": "4H", "1h": "1H", "15m": "15m"}
STRUCTURE_LABELS = {
    "bullish": ("🟢", "상승 구조"),
    "bullish_transition": ("🟢", "상승 전환 시도"),
    "bearish": ("🔴", "하락 구조"),
    "bearish_transition": ("🔴", "하락 전환 경계"),
    "mixed": ("🟡", "혼조 구조"),
    "insufficient": ("⚪", "데이터 부족"),
}
KST = timezone(timedelta(hours=9))


def build_analysis_message(analysis: MarketAnalysis, trigger: str | None = None) -> str:
    primary = analysis.important_patterns[0] if analysis.important_patterns else None
    if primary:
        title = f"🚨 <b>{escape(analysis.symbol)} 중요 변화 감지</b>"
        subtitle = f"<b>{TIMEFRAME_LABELS.get(primary['timeframe'], primary['timeframe'])} {escape(primary['name'])}</b>"
    else:
        title = f"📊 <b>{escape(analysis.symbol)} 차트 선생님 분석</b>"
        subtitle = f"<b>현재 종합 방향: {_bias_label(analysis.bias)}</b>"

    lines = [
        title,
        "",
        subtitle,
        f"현재가: <b>{escape(_fmt_price(analysis.current_price))}</b>",
        f"방향 점수: <b>{analysis.direction_score}/100</b> · 신호 강도: <b>{analysis.signal_strength}/100</b>",
        "",
    ]
    for interval in ("1d", "4h", "1h", "15m"):
        timeframe = analysis.timeframes.get(interval)
        if timeframe:
            icon, label = STRUCTURE_LABELS.get(timeframe.structure, ("🟡", timeframe.structure))
            lines.append(f"{TIMEFRAME_LABELS[interval]} {icon} {label} ({timeframe.direction_score})")

    detail = analysis.timeframes.get("4h") or next(iter(analysis.timeframes.values()))
    lines.extend(["", "<b>핵심 지표</b>"])
    if detail.volume_ratio is not None:
        difference = (detail.volume_ratio - 1) * 100
        lines.append(f"거래량: 20봉 평균 대비 {difference:+.0f}%")
    lines.append(f"RSI: {_number(detail.rsi, 1)} — {_rsi_label(detail.rsi)}")
    lines.append(f"EMA: {_ema_label(detail)}")
    if detail.macd is not None and detail.macd_signal is not None:
        macd_label = "MACD가 시그널 위" if detail.macd > detail.macd_signal else "MACD가 시그널 아래"
        lines.append(f"MACD: {macd_label}")
    if detail.support is not None:
        lines.append(f"가까운 지지: {_fmt_price(detail.support)}")
    if detail.resistance is not None:
        lines.append(f"가까운 저항: {_fmt_price(detail.resistance)}")

    if analysis.important_patterns:
        lines.extend(["", "<b>패턴 후보</b>"])
        for item in analysis.important_patterns[:4]:
            status = "확인" if item["status"] == "confirmed" else "후보"
            direction = "🟢" if item["direction"] == "bullish" else "🔴" if item["direction"] == "bearish" else "🟡"
            lines.append(f"{direction} {TIMEFRAME_LABELS.get(item['timeframe'], item['timeframe'])} {escape(item['name'])} — {status} {item['confidence']}%")
            if len(lines) < 30 and item.get("evidence"):
                lines.append(f"  └ {escape(item['evidence'])}")

    bottom = build_bottom_decision(analysis)
    lines.extend(["", "<b>바닥 단서 · 확률 아님</b>"])
    lines.append(f"점수: <b>{bottom.score}/100</b> · 단계: <b>{escape(bottom.stage)}</b>")
    lines.append(f"행동 원칙: {escape(bottom.action)}")
    if bottom.confirmations:
        lines.append("확인 항목: " + escape(" · ".join(bottom.confirmations)))

    reasons = []
    for interval in ("1d", "4h", "1h", "15m"):
        timeframe = analysis.timeframes.get(interval)
        if timeframe:
            reasons.extend(timeframe.reasons[:2])
    if reasons:
        lines.extend(["", "<b>왜 이렇게 판단했나?</b>"])
        for reason in _unique(reasons)[:4]:
            lines.append(f"• {escape(reason)}")

    if analysis.analogs:
        lines.extend(["", "<b>과거 유사 구간</b>"])
        for analog in analysis.analogs[:3]:
            lines.append(
                f"• {analog.start_date}~{analog.end_date} 유사도 {analog.similarity}% "
                f"→ 이후 1일 {analog.return_short:+.1f}%, 5일 {analog.return_long:+.1f}%"
            )
        lines.append("과거 유사도는 모양 비교이며 같은 결과를 뜻하지 않습니다.")

    notices = build_investor_notices(analysis, detail)
    if notices:
        lines.extend(["", "<b>투자자가 알아야 할 점</b>"])
        icons = {"risk": "🚨", "caution": "⚠️", "info": "ℹ️"}
        for notice in notices:
            lines.append(
                f"{icons.get(notice.level, '•')} <b>{escape(notice.title)}</b>: {escape(notice.detail)}"
            )

    lines.extend(["", "<b>무효화·다음 확인</b>", escape(detail.invalidation)])
    if trigger:
        lines.append(f"감지 계기: {escape(trigger)}")
    lines.extend([
        "",
        "⚠️ 기술적 분석 학습 보조용입니다. 확정 매수·매도 신호가 아니며 뉴스·유동성·포지션 위험은 별도로 확인해야 합니다.",
    ])
    message = "\n".join(lines)
    return message if len(message) <= 4000 else message[:3960] + "\n…(일부 설명 생략)"


def build_hourly_summary_message(analyses: list[MarketAnalysis]) -> str:
    generated = datetime.now(UTC).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    lines = [
        "🕐 <b>1시간 정기 차트 브리핑</b>",
        f"Binance 현물 시세 기준: {generated} KST",
    ]

    for analysis in analyses:
        symbol = analysis.symbol.removesuffix("USDT")
        lines.extend(["", f"<b>{escape(symbol)} · {_fmt_price(analysis.current_price)}</b>"])
        structures = []
        for interval in ("1d", "4h", "1h", "15m"):
            timeframe = analysis.timeframes.get(interval)
            if timeframe:
                icon, label = STRUCTURE_LABELS.get(timeframe.structure, ("🟡", timeframe.structure))
                structures.append(f"{TIMEFRAME_LABELS[interval]} {icon} {label}")
        lines.append(" · ".join(structures))

        detail = analysis.timeframes.get("1h") or analysis.timeframes.get("4h")
        if detail:
            lines.append(
                f"방향 {analysis.direction_score}/100 · RSI {_number(detail.rsi, 1)} · "
                f"EMA {_ema_label(detail)}"
            )
        bottom = build_bottom_decision(analysis)
        lines.append(
            f"바닥 단서 {bottom.score}/100(확률 아님) · 신호 강도 {analysis.signal_strength}/100"
        )
        lines.append(f"바닥 룰: {escape(bottom.stage)} — {escape(bottom.action)}")
        if detail:
            lines.append(f"한줄평: {_market_one_line(analysis, detail)}")
            notices = build_investor_notices(analysis, detail, limit=2)
            if notices:
                lines.append(
                    "투자자 체크: " + " · ".join(escape(item.title) for item in notices)
                )

        primary = analysis.important_patterns[0] if analysis.important_patterns else None
        if primary:
            status = "확인" if primary["status"] == "confirmed" else "후보"
            lines.append(
                f"패턴: {TIMEFRAME_LABELS.get(primary['timeframe'], primary['timeframe'])} "
                f"{escape(primary['name'])} {status} {primary['confidence']}%"
            )
        else:
            lines.append("패턴: 현재 뚜렷한 후보 없음")

    lines.extend([
        "",
        "중요 돌파·이탈·강한 패턴은 정기 보고와 별도로 발견 즉시 알립니다.",
        "⚠️ 확정 매수·매도 신호가 아닌 기술적 분석 학습 보조 자료입니다.",
    ])
    message = "\n".join(lines)
    return message if len(message) <= 4000 else message[:3960] + "\n…(일부 설명 생략)"


def build_ema_signal_message(
    analysis: MarketAnalysis,
    signals: list[EmaSignal],
    *,
    tolerance_percent: float,
) -> str:
    symbol = analysis.symbol.removesuffix("USDT")
    generated = datetime.now(UTC).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"🚨 <b>{escape(symbol)} 이평선 변화 감지</b>",
        f"현재가: <b>{_fmt_price(analysis.current_price)}</b> · Binance 현물",
        f"확인 시각: {generated} KST",
        "",
    ]

    touches = [signal for signal in signals if signal.kind == "touch"]
    alignments = [signal for signal in signals if signal.kind == "alignment"]
    if touches:
        lines.append("<b>이평선 터치</b>")
        for signal in touches:
            lines.append(
                f"• {TIMEFRAME_LABELS.get(signal.interval, signal.interval)} "
                f"EMA {signal.ema_period} 터치 · 기준 {_fmt_price(signal.ema_value)}"
            )
        lines.append(f"진행 중인 봉의 꼬리 포함 · 허용 범위 {tolerance_percent:.2f}%")

    if alignments:
        if touches:
            lines.append("")
        lines.append("<b>배열 전환 확정</b>")
        for signal in alignments:
            icon = "🟢" if signal.alignment == "bullish" else "🔴"
            korean = "정배열" if signal.alignment == "bullish" else "역배열"
            lines.append(
                f"• {TIMEFRAME_LABELS.get(signal.interval, signal.interval)} {icon} "
                f"EMA 20 {'>' if signal.alignment == 'bullish' else '<'} 50 "
                f"{'>' if signal.alignment == 'bullish' else '<'} 200 · {korean}"
            )
        lines.append("배열 전환은 마감된 봉으로만 확정합니다.")

    lines.extend([
        "",
        "<b>한줄 결론</b>",
        _ema_one_line(analysis, signals),
    ])
    signal_rank = {"1d": 4, "4h": 3, "1h": 2, "15m": 1}
    preferred = max(signals, key=lambda item: signal_rank.get(item.interval, 0)).interval if signals else "4h"
    detail = analysis.timeframes.get(preferred) or analysis.timeframes.get("4h")
    notices = build_investor_notices(analysis, detail, limit=3)
    if notices:
        lines.extend(["", "<b>투자자가 알아야 할 점</b>"])
        for notice in notices:
            lines.append(f"• <b>{escape(notice.title)}</b>: {escape(notice.detail)}")
    lines.extend([
        "",
        "<b>다음 확인</b>",
        "터치 뒤 종가가 이평선 위에서 지지되는지, 거래량이 붙는지, 다음 봉이 같은 방향으로 이어지는지 확인하세요.",
        "",
        "⚠️ 이평선 터치는 관찰 신호이며 단독 매수·매도 근거가 아닙니다.",
    ])
    return "\n".join(lines)


def _ema_one_line(analysis: MarketAnalysis, signals: list[EmaSignal]) -> str:
    timeframe_rank = {"1d": 4, "4h": 3, "1h": 2, "15m": 1}
    alignments = sorted(
        (signal for signal in signals if signal.kind == "alignment"),
        key=lambda item: timeframe_rank.get(item.interval, 0),
        reverse=True,
    )
    if alignments:
        signal = alignments[0]
        label = TIMEFRAME_LABELS.get(signal.interval, signal.interval)
        if signal.alignment == "bullish":
            return (
                f"{label} 정배열 전환 — 종가가 EMA20 위를 유지하면 상승 우위이며, "
                "EMA20 아래로 밀린 뒤 EMA50까지 이탈하면 전환 실패를 경계하세요."
            )
        return (
            f"{label} 역배열 전환 — EMA20을 회복하기 전에는 하락 우위이며, "
            "저점을 더 낮추면 하락 지속 가능성이 커지고 EMA20 위 마감 시 약화됩니다."
        )

    touches = sorted(
        (signal for signal in signals if signal.kind == "touch" and signal.ema_value),
        key=lambda item: (timeframe_rank.get(item.interval, 0), item.ema_period or 0),
        reverse=True,
    )
    if touches:
        signal = touches[0]
        label = TIMEFRAME_LABELS.get(signal.interval, signal.interval)
        period = signal.ema_period
        if analysis.current_price >= float(signal.ema_value):
            return (
                f"{label} EMA{period} 지지 시험 중 — 이 시간봉이 선 아래로 마감하고 다음 봉도 "
                "저점을 낮추면 하락 가능성이 커지며, 선 위에서 반등하면 지지 확인 쪽입니다."
            )
        return (
            f"{label} EMA{period} 아래로 밀린 상태 — 이 시간봉이 선을 회복하지 못하고 다음 봉이 "
            "저점을 낮추면 하락 지속 가능성이 커지며, 다시 선 위로 마감하면 이탈 실패입니다."
        )
    return "현재는 배열·터치 변화만 확인됐으며 다음 봉의 종가와 거래량 확인이 필요합니다."


def _market_one_line(analysis: MarketAnalysis, detail: TimeframeAnalysis) -> str:
    label = TIMEFRAME_LABELS.get(detail.interval, detail.interval)
    if detail.ema20 is None:
        return "EMA 데이터가 부족해 방향 확정을 보류합니다."
    above = analysis.current_price >= detail.ema20
    if analysis.direction_score >= 60:
        return (
            f"상승 우위; {label} EMA20 위 유지 시 흐름 지속, 아래 마감 시 상승 힘 약화."
            if above
            else f"상승 점수는 우위지만 {label} EMA20 아래라 재회복 전까지 추격 주의."
        )
    if analysis.direction_score <= 40:
        return (
            f"하락 우위; {label} EMA20 아래에서 저점을 낮추면 추가 하락 경계, 위 회복 시 약화."
            if not above
            else f"하락 점수는 우위지만 {label} EMA20을 회복해 반등 지속 여부 확인 필요."
        )
    return (
        f"혼조 구간; {label} EMA20 위 마감은 단기 개선, 아래 마감은 하락 압력 우위."
    )


def _ema_label(timeframe: TimeframeAnalysis) -> str:
    values = [(20, timeframe.ema20), (50, timeframe.ema50), (200, timeframe.ema200)]
    available = [(period, value) for period, value in values if value is not None]
    if len(available) < 2:
        return "장기 데이터 부족"
    ordered = sorted(available, key=lambda item: item[1], reverse=True)
    relation = " > ".join(str(period) for period, _ in ordered)
    if timeframe.ema200 is not None:
        position = "가격은 200 위" if timeframe.close > timeframe.ema200 else "가격은 200 아래"
        return f"{relation}, {position}"
    return relation


def _rsi_label(value: float | None) -> str:
    if value is None:
        return "데이터 부족"
    if value >= 70:
        return "과매수 주의"
    if value <= 30:
        return "과매도 구간"
    if value >= 55:
        return "상승 모멘텀 우위"
    if value <= 45:
        return "하락 모멘텀 우위"
    return "중립"


def _bias_label(value: str) -> str:
    return {
        "bullish": "상승 우위",
        "bullish_attempt": "상승 전환 시도",
        "bearish": "하락 우위",
        "bearish_warning": "하락 경계",
        "neutral": "중립",
    }.get(value, value)


def _bottom_label(score: int) -> str:
    if score >= 75:
        return "강한 후보지만 구조 확인 필요"
    if score >= 50:
        return "중간 단계 후보"
    if score >= 25:
        return "초기 단서만 존재"
    return "현재는 뚜렷하지 않음"


def _number(value: float | None, digits: int) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
