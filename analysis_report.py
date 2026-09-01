from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from html import escape
from typing import TYPE_CHECKING

from analysis_engine import MarketAnalysis, TimeframeAnalysis
from dominance_context import UsdtDominanceSnapshot, format_pp
from entry_strategy import (
    EntryDecision,
    capital_action_line,
    capital_plan_line,
    entry_headline,
    evaluate_entry,
)
from formatter import _fmt_price
from indicator_summary import core_indicator_line
from investor_guidance import build_bottom_decision, build_investor_notices
from pattern_education import pattern_one_line
from sentiment_context import FearGreedSnapshot, format_change
from wave_context import wave_summary

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


def build_usdt_dominance_message(snapshot: UsdtDominanceSnapshot) -> str:
    total_change = (
        "-"
        if snapshot.total_market_change_24h is None
        else f"{snapshot.total_market_change_24h:+.2f}%"
    )
    volume_change = (
        "-"
        if snapshot.volume_change_24h is None
        else f"{snapshot.volume_change_24h:+.2f}%"
    )
    action = (
        "시장 환경은 우호적이지만 개별 코인이 과열이면 눌림을 기다립니다."
        if snapshot.regime in {"risk_on", "supportive"}
        else "신규 진입을 보류하고 코인 가격이 1H EMA20과 지지를 지키는지 먼저 확인합니다."
        if snapshot.regime in {"risk_off", "cautious"}
        else "USDT.D 방향이 뚜렷하지 않아 개별 코인 진입 조건을 우선합니다."
    )
    lines = [
        "🧭 <b>USDT 도미넌스 변화</b>",
        f"현재 <b>{snapshot.value:.2f}%</b> · 24h {format_pp(snapshot.change_24h_pp)}",
        f"관찰 변화: 1h {format_pp(snapshot.change_1h_pp)} · 4h {format_pp(snapshot.change_4h_pp)}",
        f"전체 코인 시총 24h {total_change} · 거래량 {volume_change}",
        "",
        f"<b>시장 판정 · {escape(snapshot.label)}</b>",
        escape(snapshot.guidance),
        f"진입 원칙: {escape(action)}",
        "",
        "쉽게 말해: USDT.D 하락은 코인 시장에 우호적일 수 있고, 상승은 현금 대피를 뜻할 수 있습니다. 다만 시총 비율이라 단독 매수 신호는 아닙니다.",
        "출처: CoinGecko 공개 시장 데이터 · 별도 유료 API 키 없음",
    ]
    return _limited_message(lines, 1500)


def build_analysis_message(
    analysis: MarketAnalysis,
    trigger: str | None = None,
    sentiment: FearGreedSnapshot | None = None,
) -> str:
    primary = analysis.important_patterns[0] if analysis.important_patterns else None
    entry = evaluate_entry(analysis)
    title = f"<b>{escape(entry_headline(analysis.symbol, entry))}</b>"
    if primary:
        subtitle = f"<b>{TIMEFRAME_LABELS.get(primary['timeframe'], primary['timeframe'])} {escape(primary['name'])}</b>"
    else:
        subtitle = f"<b>현재 종합 방향: {_bias_label(analysis.bias)}</b>"

    lines = [
        title,
        "",
        subtitle,
        f"현재가: <b>{escape(_fmt_price(analysis.current_price))}</b>",
    ]
    if primary:
        lines.append(
            f"한줄 설명: {escape(pattern_one_line(primary['name'], primary['status']))}"
        )
    lines.extend([
        f"방향 점수: <b>{analysis.direction_score}/100</b> · 신호 강도: <b>{analysis.signal_strength}/100</b>",
        "",
        "<b>핵심 3종 · RSI / EMA 배열 / 가격 위치</b>",
    ])
    for interval in ("1d", "4h", "1h"):
        timeframe = analysis.timeframes.get(interval)
        if timeframe:
            lines.append(escape(core_indicator_line(timeframe, TIMEFRAME_LABELS[interval])))

    lines.extend([
        "",
        f"<b>진입 판단 · {escape(entry.action_label)}</b> · "
        f"조건 충족 {entry.score}/100(확률 아님)",
    ])
    lines.append(f"{escape(entry.setup)} · {escape(entry.explanation)}")
    lines.append(f"자금 판단: <b>{escape(capital_action_line(entry))}</b>")
    if capital_plan_line(entry):
        lines.append(f"분할 계획: {escape(capital_plan_line(entry))}")
    lines.extend(_entry_price_lines(entry))
    dominance_line = _dominance_summary(analysis)
    if dominance_line:
        lines.append(f"USDT.D 시장 필터: {escape(dominance_line)}")
    lines.append(f"파동·피보나치: {escape(wave_summary(analysis.wave_context))}")

    lines.extend(["", "<b>시간대 구조</b>"])
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
    if detail.taker_buy_ratio is not None:
        latest = detail.taker_buy_ratio * 100
        recent = (
            f" · 최근 5봉 {detail.taker_buy_ratio_5 * 100:.0f}%"
            if detail.taker_buy_ratio_5 is not None
            else ""
        )
        lines.append(
            f"체결 매수 비중: 마지막 확정봉 <b>{latest:.0f}%</b>{recent} "
            f"— {_buy_pressure_label(detail.taker_buy_ratio)}"
        )
    lines.append(f"RSI: {_number(detail.rsi, 1)} — {_rsi_label(detail.rsi)}")
    lines.append(f"EMA: {_ema_label(detail)}")
    if detail.macd is not None and detail.macd_signal is not None:
        macd_label = "MACD가 시그널 위" if detail.macd > detail.macd_signal else "MACD가 시그널 아래"
        lines.append(f"MACD: {macd_label}")
    if detail.support is not None:
        lines.append(f"가까운 지지: {_fmt_price(detail.support)}")
    if detail.resistance is not None:
        lines.append(f"가까운 저항: {_fmt_price(detail.resistance)}")

    if sentiment is not None:
        lines.extend([
            "",
            "<b>공포·탐욕 지수 · BTC 중심</b>",
            f"<b>{sentiment.value}/100 · {escape(sentiment.label)}</b> · "
            f"전일 {format_change(sentiment.change_1d)} · 7일 {format_change(sentiment.change_7d)}",
            escape(sentiment.guidance),
            "출처: Alternative.me · 단독 매수·매도 신호 아님",
        ])

    if analysis.important_patterns:
        lines.extend(["", "<b>패턴 후보</b>"])
        for item in analysis.important_patterns[:4]:
            status = "확인" if item["status"] == "confirmed" else "후보"
            direction = "🟢" if item["direction"] == "bullish" else "🔴" if item["direction"] == "bearish" else "🟡"
            lines.append(f"{direction} {TIMEFRAME_LABELS.get(item['timeframe'], item['timeframe'])} {escape(item['name'])} — {status} {item['confidence']}%")
            lines.append(
                f"  └ 한줄 설명: {escape(pattern_one_line(item['name'], item['status']))}"
            )
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
    return _limited_message(lines, 4000)


def build_compact_analysis_caption(
    analysis: MarketAnalysis,
    significant: dict,
) -> str:
    coin = analysis.symbol.removesuffix("USDT")
    interval = significant.get("timeframe", "4h")
    interval_label = TIMEFRAME_LABELS.get(interval, interval)
    event = str(significant.get("event", ""))
    description = str(significant.get("description", "중요 변화"))
    explanation = _signal_one_line(analysis, significant)
    entry = evaluate_entry(analysis)
    detail = analysis.timeframes.get(interval) or analysis.timeframes.get("4h")
    if event.startswith("pattern:"):
        event_label = event.split(":", 1)[1]
    elif event.startswith("entry:"):
        event_label = description
    else:
        event_label = description.removeprefix(f"{interval} ")
    lines = [
        f"<b>{escape(entry_headline(analysis.symbol, entry))}</b>",
        f"<b>감지 계기</b> · {escape(interval_label)} {escape(event_label)}",
        f"현재가 <b>{escape(_fmt_price(analysis.current_price))}</b>",
        f"<b>진입 판단</b> · {escape(entry.action_label)} · "
        f"조건 충족 {entry.score}/100(확률 아님)",
        f"{escape(entry.setup)} · {escape(entry.explanation)}",
        f"<b>자금</b> · {escape(capital_action_line(entry))}",
        *(
            [f"<b>USDT.D</b> · {escape(_dominance_summary(analysis))}"]
            if _dominance_summary(analysis)
            else []
        ),
        f"<b>파동·피보나치</b> · {escape(wave_summary(analysis.wave_context))}",
        f"<b>한줄 설명</b> · {escape(explanation)}",
    ]
    if (
        entry.action == "first_entry_review"
        and entry.entry_low is not None
        and entry.entry_high is not None
        and entry.invalidation is not None
    ):
        lines.insert(
            5,
            f"검토 구간 <b>{_fmt_price(entry.entry_low)}~{_fmt_price(entry.entry_high)}</b> · "
            f"무효화 {_fmt_price(entry.invalidation)} 아래",
        )
    if detail:
        lines.extend([
            "<b>핵심 3종</b>",
            escape(core_indicator_line(detail, interval_label)),
        ])
    lines.append("⚠️ 단독 매수·매도 신호가 아닌 확인용 알림입니다.")
    return _limited_message(lines, 1024)


def build_compact_ema_caption(
    analysis: MarketAnalysis,
    signals: list[EmaSignal],
) -> str:
    coin = analysis.symbol.removesuffix("USDT")
    rank = {"1d": 4, "4h": 3, "1h": 2, "15m": 1}
    preferred = max(signals, key=lambda item: rank.get(item.interval, 0)).interval
    detail = analysis.timeframes.get(preferred) or analysis.timeframes.get("4h")
    events = " · ".join(dict.fromkeys(signal.description for signal in signals))
    entry = evaluate_entry(analysis)
    lines = [
        f"<b>{escape(entry_headline(analysis.symbol, entry))}</b>",
        f"<b>감지 계기</b> · {escape(TIMEFRAME_LABELS.get(preferred, preferred))} 이평선 변화 · {escape(events)}",
        f"<b>진입 판단</b> · {escape(entry.action_label)} · "
        f"조건 충족 {entry.score}/100(확률 아님)",
        f"<b>자금</b> · {escape(capital_action_line(entry))}",
        *(
            [f"<b>USDT.D</b> · {escape(_dominance_summary(analysis))}"]
            if _dominance_summary(analysis)
            else []
        ),
        f"<b>한줄 결론</b> · {escape(_ema_one_line(analysis, signals))}",
    ]
    if detail:
        lines.extend([
            "<b>핵심 3종</b>",
            escape(core_indicator_line(detail, TIMEFRAME_LABELS.get(preferred, preferred))),
        ])
    return _limited_message(lines, 1024)


def build_hourly_summary_message(
    analyses: list[MarketAnalysis],
    sentiment: FearGreedSnapshot | None = None,
) -> str:
    generated = datetime.now(UTC).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    lines = [
        "🗓 <b>정기 사이클 차트 브리핑</b>",
        f"Binance 현물 시세 기준: {generated} KST",
    ]
    if sentiment is not None:
        lines.extend([
            f"시장심리(BTC 중심): <b>{sentiment.value}/100 · {escape(sentiment.label)}</b> "
            f"· 전일 {format_change(sentiment.change_1d)} · 7일 {format_change(sentiment.change_7d)}",
            f"심리 해석: {escape(sentiment.guidance)}",
            "출처: Alternative.me",
        ])
    dominance_line = _dominance_summary(analyses[0]) if analyses else ""
    if dominance_line:
        lines.extend([
            f"USDT.D 시장 필터: <b>{escape(dominance_line)}</b>",
            "USDT.D는 시장 환경 필터이며 개별 코인의 단독 진입 신호가 아닙니다.",
        ])

    for analysis in analyses:
        symbol = analysis.symbol.removesuffix("USDT")
        entry = evaluate_entry(analysis)
        lines.extend([
            "",
            f"<b>{escape(entry_headline(analysis.symbol, entry))}</b>",
            f"현재가: {_fmt_price(analysis.current_price)}",
        ])
        lines.append("<b>핵심 3종 · RSI / EMA 배열 / 가격 위치</b>")
        for interval in ("1d", "4h", "1h"):
            timeframe = analysis.timeframes.get(interval)
            if timeframe:
                lines.append(escape(core_indicator_line(timeframe, TIMEFRAME_LABELS[interval])))
        if entry.action == "market_context":
            lines.append(
                f"역할: <b>{escape(entry.action_label)}</b> · {escape(entry.setup)}"
            )
        else:
            lines.append(
                f"진입 판단: <b>{escape(entry.action_label)}</b> · "
                f"조건 충족 {entry.score}/100(확률 아님) · "
                f"{escape(entry.setup)}"
            )
        lines.append(f"자금 판단: <b>{escape(capital_action_line(entry))}</b>")
        if capital_plan_line(entry):
            lines.append(f"분할 계획: {escape(capital_plan_line(entry))}")
        if entry.blockers:
            lines.append(f"진입 주의: {escape(entry.explanation)}")
        lines.append(f"파동·피보나치: {escape(wave_summary(analysis.wave_context))}")
        structures = []
        for interval in ("1d", "4h", "1h", "15m"):
            timeframe = analysis.timeframes.get(interval)
            if timeframe:
                icon, label = STRUCTURE_LABELS.get(timeframe.structure, ("🟡", timeframe.structure))
                structures.append(f"{TIMEFRAME_LABELS[interval]} {icon} {label}")
        lines.append(" · ".join(structures))

        detail = analysis.timeframes.get("1h") or analysis.timeframes.get("4h")
        if detail:
            volume_text = (
                f"{(detail.volume_ratio - 1) * 100:+.0f}%"
                if detail.volume_ratio is not None
                else "-"
            )
            buy_text = (
                f"{detail.taker_buy_ratio * 100:.0f}%"
                if detail.taker_buy_ratio is not None
                else "-"
            )
            lines.append(
                f"방향 {analysis.direction_score}/100 · 거래량 {volume_text} · "
                f"체결매수 {buy_text}"
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
            lines.append(
                f"패턴 설명: {escape(pattern_one_line(primary['name'], primary['status']))}"
            )
        else:
            lines.append("패턴: 현재 뚜렷한 후보 없음")

    lines.extend([
        "",
        "중요 돌파·이탈·강한 패턴은 정기 보고와 별도로 발견 즉시 알립니다.",
        "⚠️ 확정 매수·매도 신호가 아닌 기술적 분석 학습 보조 자료입니다.",
    ])
    return _limited_message(lines, 4000)


def build_ema_signal_message(
    analysis: MarketAnalysis,
    signals: list[EmaSignal],
    *,
    tolerance_percent: float,
    sentiment: FearGreedSnapshot | None = None,
) -> str:
    symbol = analysis.symbol.removesuffix("USDT")
    generated = datetime.now(UTC).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"🚨 <b>{escape(symbol)} 이평선 변화 감지</b>",
        f"현재가: <b>{_fmt_price(analysis.current_price)}</b> · Binance 현물",
        f"확인 시각: {generated} KST",
        "",
    ]
    if sentiment is not None:
        lines.extend([
            f"시장심리(BTC 중심): <b>{sentiment.value}/100 · {escape(sentiment.label)}</b> "
            f"· 7일 {format_change(sentiment.change_7d)}",
            f"{escape(sentiment.guidance)} · 출처 Alternative.me",
            "",
        ])
    dominance_line = _dominance_summary(analysis)
    if dominance_line:
        lines.extend([
            f"USDT.D 시장 필터: <b>{escape(dominance_line)}</b>",
            "",
        ])

    touches = [signal for signal in signals if signal.kind == "touch"]
    alignments = [signal for signal in signals if signal.kind == "alignment"]
    if touches:
        lines.append("<b>이평선 마감 반응</b>")
        for signal in touches:
            reaction = "선 위 마감 · 지지 반응" if signal.reaction == "closed_above" else "선 아래 마감 · 저항/이탈 반응"
            lines.append(
                f"• {TIMEFRAME_LABELS.get(signal.interval, signal.interval)} "
                f"EMA {signal.ema_period} 접촉 · 기준 {_fmt_price(signal.ema_value)} · {reaction}"
            )
        lines.append(f"마감된 봉 기준 · 접촉 허용 범위 {tolerance_percent:.2f}%")

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
        f"<b>자금 판단</b>: {escape(capital_action_line(evaluate_entry(analysis)))}",
    ])
    signal_rank = {"1d": 4, "4h": 3, "1h": 2, "15m": 1}
    preferred = max(signals, key=lambda item: signal_rank.get(item.interval, 0)).interval if signals else "4h"
    detail = analysis.timeframes.get(preferred) or analysis.timeframes.get("4h")
    if detail and (detail.volume_ratio is not None or detail.taker_buy_ratio is not None):
        label = TIMEFRAME_LABELS.get(detail.interval, detail.interval)
        volume_text = (
            f"20봉 평균 대비 {(detail.volume_ratio - 1) * 100:+.0f}%"
            if detail.volume_ratio is not None
            else "데이터 부족"
        )
        buy_text = (
            f"시장가 매수 {detail.taker_buy_ratio * 100:.0f}% · "
            f"{_buy_pressure_label(detail.taker_buy_ratio)}"
            if detail.taker_buy_ratio is not None
            else "체결 방향 데이터 부족"
        )
        lines.extend([
            "",
            "<b>거래량·체결 강도</b>",
            f"{label} 거래량 {volume_text} · {buy_text}",
        ])
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
        if signal.reaction == "closed_above":
            return (
                f"{label} EMA{period} 접촉 후 선 위 마감 — 지지 반응이 확인됐습니다. "
                "다음 봉이 선 아래로 다시 마감하면 지지 실패를 경계하세요."
            )
        return (
            f"{label} EMA{period} 접촉 후 선 아래 마감 — 저항 또는 이탈 반응입니다. "
            "다음 봉이 선 위를 회복하면 이탈 실패로 해석이 약해집니다."
        )
    return "현재는 배열·터치 변화만 확인됐으며 다음 봉의 종가와 거래량 확인이 필요합니다."


def _signal_one_line(analysis: MarketAnalysis, significant: dict) -> str:
    event = str(significant.get("event", ""))
    interval = significant.get("timeframe", "4h")
    if event.startswith("pattern:"):
        name = event.split(":", 1)[1]
        matched = next(
            (
                item
                for item in analysis.important_patterns
                if item["name"] == name and item["timeframe"] == interval
            ),
            None,
        )
        status = matched["status"] if matched else "confirmed"
        return pattern_one_line(name, status)
    if event.startswith("entry:"):
        return evaluate_entry(analysis).explanation
    if event == "rsi_overbought_entry":
        return "RSI가 70 위로 올라 단기 상승 힘은 강하지만 추격 매수와 과열 조정을 함께 경계할 구간입니다."
    if event == "rsi_overbought_exit":
        return "RSI가 70 아래로 내려와 과열은 완화됐지만 가격도 함께 밀리면 상승 힘 약화를 경계해야 합니다."
    if event == "rsi_oversold_entry":
        return "RSI가 30 아래로 내려 하락 압력이 강하며, 과매도만으로 바닥을 확정하지 말고 반등 확인이 필요합니다."
    if event == "rsi_oversold_exit":
        return "RSI가 30 위를 회복해 매도 압력이 완화됐지만 가격과 거래량의 후속 반등이 필요합니다."
    if event == "high_volume_buy_pressure":
        return "평균보다 큰 거래량과 시장가 매수 우위가 함께 나타나 상승 시도에 힘이 실린 상태입니다."
    if event == "high_volume_sell_pressure":
        return "평균보다 큰 거래량과 시장가 매도 우위가 함께 나타나 하락 압력이 커진 상태입니다."
    if event == "bottom_candidate":
        return "여러 바닥 단서가 모였지만 확률이 아니므로 지지 확인과 추세 전환 뒤에만 진입을 검토합니다."
    if event == "timeframe_confluence":
        return "여러 시간대가 같은 방향을 가리키지만 가까운 지지·저항과 과열 여부를 함께 확인해야 합니다."
    return str(significant.get("description", "중요 변화가 감지되어 다음 확정봉 확인이 필요합니다."))


def _dominance_summary(analysis: MarketAnalysis) -> str:
    context = analysis.dominance_context or {}
    if context.get("value") is None:
        return ""

    def pp(value: object) -> str:
        try:
            return f"{float(value):+.2f}%p"
        except (TypeError, ValueError):
            return "수집 중"

    return (
        f"{float(context['value']):.2f}% · 24h {pp(context.get('change_24h_pp'))} · "
        f"1h {pp(context.get('change_1h_pp'))} · 4h {pp(context.get('change_4h_pp'))} · "
        f"{context.get('label', '중립')}"
    )


def _entry_price_lines(entry: EntryDecision) -> list[str]:
    if (
        entry.action != "first_entry_review"
        or entry.entry_low is None
        or entry.entry_high is None
        or entry.invalidation is None
    ):
        if entry.action == "wait_pullback":
            return [
                "행동 원칙: 급등 직후 추격하지 말고 15m·1H EMA20 눌림과 지지 마감을 기다립니다."
            ]
        return ["행동 원칙: 조건이 완성되기 전에는 현금을 보존합니다."]
    return [
        f"검토 구간: {_fmt_price(entry.entry_low)}~{_fmt_price(entry.entry_high)}",
        f"무효화 기준: {_fmt_price(entry.invalidation)} 아래 확정 마감",
        "자금 원칙: 예정 금액 전부가 아니라 1차 분할만 검토하고, 세 코인은 하나의 고위험 묶음으로 봅니다.",
    ]


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


def _buy_pressure_label(value: float | None) -> str:
    if value is None:
        return "데이터 부족"
    if value >= 0.62:
        return "강한 매수 체결 우위"
    if value >= 0.54:
        return "매수 체결 우위"
    if value <= 0.38:
        return "강한 매도 체결 우위"
    if value <= 0.46:
        return "매도 체결 우위"
    return "매수·매도 체결 균형"


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


def _limited_message(lines: list[str], max_chars: int) -> str:
    message = "\n".join(lines)
    if len(message) <= max_chars:
        return message
    suffix = "\n…(일부 설명 생략)"
    kept: list[str] = []
    current_length = 0
    for line in lines:
        added = len(line) + (1 if kept else 0)
        if current_length + added + len(suffix) > max_chars:
            break
        kept.append(line)
        current_length += added
    return "\n".join(kept) + suffix
