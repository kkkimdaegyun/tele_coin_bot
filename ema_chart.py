from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from analysis_engine import MarketAnalysis, TimeframeAnalysis, ema
from entry_strategy import (
    capital_action_line,
    capital_plan_line,
    entry_headline,
    evaluate_entry,
)
from ema_signals import EmaSignal
from formatter import _fmt_price
from indicator_summary import price_ema20_position, rsi_zone
from investor_guidance import build_investor_notices, investor_notice_line
from market_data import BinanceMarketData, Candle
from macro_context import MacroContext, get_macro_context
from pattern_education import pattern_one_line
from sentiment_context import FearGreedSnapshot, format_change, get_fear_greed
from storage import ChartTeacherStore
from wave_context import wave_summary


KST = timezone(timedelta(hours=9))
TIMEFRAME_LABELS = {"1d": "1D", "4h": "4H", "1h": "1H", "15m": "15m"}
TIMEFRAME_ORDER = {"1d": 4, "4h": 3, "1h": 2, "15m": 1}
FONT_REGULAR = Path(r"C:\Windows\Fonts\malgun.ttf")
FONT_BOLD = Path(r"C:\Windows\Fonts\malgunbd.ttf")

BACKGROUND = "#10151f"
PANEL = "#151c28"
GRID = "#2a3444"
TEXT = "#e8edf4"
MUTED = "#94a3b8"
UP = "#20c997"
DOWN = "#ff5c75"
EMA_COLORS = {20: "#ffd43b", 50: "#ff7f50", 200: "#4dabf7"}


async def build_ema_chart_image(
    symbol: str,
    signals: list[EmaSignal],
    *,
    analysis: MarketAnalysis | None = None,
    macro: MacroContext | None = None,
    sentiment: FearGreedSnapshot | None = None,
    provider: BinanceMarketData | None = None,
    store: ChartTeacherStore | None = None,
) -> bytes:
    provider = provider or BinanceMarketData()
    store = store or ChartTeacherStore()
    intervals = sorted(
        {signal.interval for signal in signals},
        key=lambda value: TIMEFRAME_ORDER.get(value, 0),
        reverse=True,
    )[:4]

    async def fetch_live(interval: str) -> tuple[str, Candle | None]:
        candles = await provider.fetch_klines(symbol, interval, limit=2)
        return interval, candles[-1] if candles else None

    live_results = await asyncio.gather(*(fetch_live(interval) for interval in intervals))
    live_candles = {interval: candle for interval, candle in live_results if candle is not None}
    datasets = {
        interval: store.load_candles(symbol, interval, limit=500)
        for interval in intervals
    }
    if analysis is not None:
        scenario_interval = "4h" if "4h" in analysis.timeframes else next(iter(analysis.timeframes))
        datasets[scenario_interval] = store.load_candles(symbol, scenario_interval, limit=10000)
        macro = macro or await get_macro_context()
        sentiment = sentiment or await get_fear_greed()
    return render_ema_chart_png(
        symbol,
        datasets,
        live_candles,
        signals,
        analysis=analysis,
        macro=macro,
        sentiment=sentiment,
    )


async def build_market_overview_image(
    analysis: MarketAnalysis,
    *,
    macro: MacroContext | None = None,
    sentiment: FearGreedSnapshot | None = None,
    provider: BinanceMarketData | None = None,
    store: ChartTeacherStore | None = None,
) -> bytes:
    provider = provider or BinanceMarketData()
    store = store or ChartTeacherStore()
    primary = analysis.important_patterns[0]["timeframe"] if analysis.important_patterns else "4h"
    if primary not in analysis.timeframes:
        primary = "4h" if "4h" in analysis.timeframes else next(iter(analysis.timeframes))
    recent = await provider.fetch_klines(analysis.symbol, primary, limit=2)
    live = recent[-1] if recent else None
    macro = macro or await get_macro_context()
    sentiment = sentiment or await get_fear_greed()
    datasets = {primary: store.load_candles(analysis.symbol, primary, limit=10000)}
    if primary != "4h" and "4h" in analysis.timeframes:
        datasets["4h"] = store.load_candles(analysis.symbol, "4h", limit=10000)
    return render_ema_chart_png(
        analysis.symbol,
        datasets,
        {primary: live} if live else {},
        [],
        analysis=analysis,
        macro=macro,
        sentiment=sentiment,
        intervals_override=[primary],
    )


async def build_hourly_market_board_image(analyses: list[MarketAnalysis]) -> bytes:
    if not analyses:
        raise ValueError("At least one market analysis is required")
    macro, sentiment = await asyncio.gather(get_macro_context(), get_fear_greed())
    images = [
        Image.open(BytesIO(image_bytes)).convert("RGB")
        for image_bytes in await asyncio.gather(
            *(
                build_market_overview_image(analysis, macro=macro, sentiment=sentiment)
                for analysis in analyses
            )
        )
    ]
    gap = 12
    width = max(image.width for image in images)
    height = sum(image.height for image in images) + gap * (len(images) - 1)
    board = Image.new("RGB", (width, height), BACKGROUND)
    y = 0
    for image in images:
        board.paste(image, (0, y))
        y += image.height + gap
    output = BytesIO()
    board.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_ema_chart_png(
    symbol: str,
    datasets: dict[str, list[Candle]],
    live_candles: dict[str, Candle],
    signals: list[EmaSignal],
    *,
    analysis: MarketAnalysis | None = None,
    macro: MacroContext | None = None,
    sentiment: FearGreedSnapshot | None = None,
    intervals_override: list[str] | None = None,
) -> bytes:
    grouped: dict[str, list[EmaSignal]] = defaultdict(list)
    for signal in signals:
        grouped[signal.interval].append(signal)
    intervals = intervals_override or sorted(
        grouped,
        key=lambda value: TIMEFRAME_ORDER.get(value, 0),
        reverse=True,
    )[:4]
    if not intervals:
        raise ValueError("At least one chart interval is required")

    width = 1200
    header_height = 750 if analysis is not None else 116
    panel_height = 345
    scenario_height = 440 if analysis is not None else 0
    footer_height = 72
    height = header_height + panel_height * len(intervals) + scenario_height + footer_height
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    small = _font(21)
    tiny = _font(18)
    title_font = _font(38, bold=True)
    panel_title = _font(27, bold=True)

    coin = symbol.upper().removesuffix("USDT")
    if analysis is not None:
        heading = entry_headline(symbol, evaluate_entry(analysis))
        # Windows' bundled Korean font has no color-emoji glyph. Telegram text
        # keeps the icon, while the rendered image uses the clean text title.
        heading = heading.split(" ", 1)[1] if " " in heading else heading
    else:
        heading = f"{coin}  EMA 터치 · 배열 차트"
    draw.text((36, 24), heading, font=title_font, fill=TEXT)
    generated = datetime.now(UTC).astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    draw.text((38, 74), f"Binance 현물 실제 캔들  ·  {generated}", font=small, fill=MUTED)
    if analysis is not None:
        _draw_analysis_summary(draw, analysis, macro, sentiment, small, tiny)

    for index, interval in enumerate(intervals):
        top = header_height + index * panel_height
        _draw_panel(
            draw,
            (24, top, width - 24, top + panel_height - 12),
            interval,
            datasets.get(interval, []),
            live_candles.get(interval),
            grouped[interval],
            analysis.timeframes.get(interval) if analysis is not None else None,
            small,
            tiny,
            panel_title,
        )

    if analysis is not None:
        scenario_top = header_height + panel_height * len(intervals)
        scenario_interval = "4h" if "4h" in analysis.timeframes else next(iter(analysis.timeframes))
        _draw_scenario_panel(
            draw,
            (24, scenario_top, width - 24, scenario_top + scenario_height - 12),
            analysis,
            datasets.get(scenario_interval, []),
            small,
            tiny,
        )

    footer_y = header_height + panel_height * len(intervals) + scenario_height + 4
    legend = [
        ("EMA20", EMA_COLORS[20]),
        ("EMA50", EMA_COLORS[50]),
        ("EMA200", EMA_COLORS[200]),
        ("흰 테두리: 진행 중 봉", TEXT),
        ("큰 원: 터치 위치", "#ffffff"),
    ]
    x = 38
    for label, color in legend:
        draw.line((x, footer_y + 22, x + 28, footer_y + 22), fill=color, width=5)
        draw.text((x + 38, footer_y + 7), label, font=tiny, fill=MUTED)
        x += 38 + int(draw.textlength(label, font=tiny)) + 48

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _draw_analysis_summary(
    draw: ImageDraw.ImageDraw,
    analysis: MarketAnalysis,
    macro: MacroContext | None,
    sentiment: FearGreedSnapshot | None,
    small: ImageFont.FreeTypeFont,
    tiny: ImageFont.FreeTypeFont,
) -> None:
    price_font = _font(31, bold=True)
    price = f"현재 {_fmt_price(analysis.current_price)}"
    draw.text((1160 - draw.textlength(price, font=price_font), 28), price, font=price_font, fill=TEXT)

    draw.text(
        (36, 105),
        "핵심 3종 · RSI / EMA 배열 / 가격의 EMA20 위치",
        font=tiny,
        fill=MUTED,
    )
    box_y = 130
    box_width = 270
    for index, interval in enumerate(("1d", "4h", "1h", "15m")):
        timeframe = analysis.timeframes.get(interval)
        if timeframe is None:
            continue
        state, state_color = _timeframe_state(timeframe)
        x = 36 + index * 286
        draw.rounded_rectangle(
            (x, box_y, x + box_width, box_y + 74),
            radius=10,
            fill="#172231",
            outline=state_color,
            width=3,
        )
        label = f"{TIMEFRAME_LABELS[interval]}  {state}"
        draw.text((x + 12, box_y + 7), label, font=small, fill=state_color)
        rsi_text = "RSI -" if timeframe.rsi is None else f"RSI {timeframe.rsi:.0f} {rsi_zone(timeframe.rsi)}"
        position_text = price_ema20_position(timeframe).replace("가격 ", "")
        draw.text(
            (x + 12, box_y + 42),
            f"{rsi_text} · {position_text}",
            font=tiny,
            fill=TEXT,
        )

    score_y = 220
    score_items = [
        ("방향", analysis.direction_score, UP if analysis.direction_score >= 55 else DOWN if analysis.direction_score <= 45 else "#ffd43b"),
        ("신호 강도", analysis.signal_strength, "#b197fc"),
        ("바닥 단서", analysis.bottom_score, "#4dabf7"),
    ]
    x = 36
    for label, value, color in score_items:
        draw.text((x, score_y), f"{label} {value}/100", font=small, fill=TEXT)
        bar_x = x + 168
        draw.rounded_rectangle((bar_x, score_y + 7, bar_x + 150, score_y + 21), radius=7, fill=GRID)
        draw.rounded_rectangle(
            (bar_x, score_y + 7, bar_x + 150 * max(0, min(value, 100)) / 100, score_y + 21),
            radius=7,
            fill=color,
        )
        x += 382

    detail = analysis.timeframes.get("4h") or next(iter(analysis.timeframes.values()))
    volume_text = "-" if detail.volume_ratio is None else f"{(detail.volume_ratio - 1) * 100:+.0f}%"
    buy_text = "-" if detail.taker_buy_ratio is None else f"{detail.taker_buy_ratio * 100:.0f}%"
    macd_text = "-"
    if detail.macd is not None and detail.macd_signal is not None:
        macd_text = "상승" if detail.macd > detail.macd_signal else "하락"
    metrics = (
        f"4H RSI {detail.rsi:.1f}  ·  거래량 {volume_text}  ·  체결매수 {buy_text}  ·  "
        f"MACD {macd_text}  ·  {_ema_order_text(detail)}"
    ) if detail.rsi is not None else (
        f"4H RSI -  ·  거래량 {volume_text}  ·  체결매수 {buy_text}  ·  MACD {macd_text}  ·  {_ema_order_text(detail)}"
    )
    draw.text((36, 263), metrics, font=small, fill=TEXT)
    support = _fmt_price(detail.support) if detail.support is not None else "-"
    resistance = _fmt_price(detail.resistance) if detail.resistance is not None else "-"
    draw.text(
        (36, 300),
        f"지지 {support}  ·  저항 {resistance}",
        font=small,
        fill="#cbd5e1",
    )
    pattern = analysis.important_patterns[0] if analysis.important_patterns else None
    if pattern:
        status = "확인" if pattern["status"] == "confirmed" else "후보"
        pattern_text = (
            f"패턴 {TIMEFRAME_LABELS.get(pattern['timeframe'], pattern['timeframe'])} "
            f"{pattern['name']} · {status} {pattern['confidence']}%"
        )
    else:
        pattern_text = "패턴 현재 뚜렷한 후보 없음"
    draw.text((400, 300), pattern_text, font=small, fill="#f8d477")
    pattern_guide = (
        "패턴 설명  " + pattern_one_line(pattern["name"], pattern["status"])
        if pattern
        else "패턴 설명  현재는 설명할 만한 뚜렷한 패턴이 없습니다."
    )
    _draw_wrapped_text(draw, pattern_guide, (36, 332), 1120, tiny, "#f8d477", max_lines=1)
    entry = evaluate_entry(analysis)
    entry_color = (
        UP if entry.action == "first_entry_review"
        else "#ffd43b" if entry.action == "watch"
        else DOWN if entry.action == "wait_pullback"
        else MUTED
    )
    entry_rule = (
        f"진입 판단  {entry.action_label} · 조건 {entry.score}/100(확률 아님) — "
        f"{entry.setup} · {entry.explanation}"
    )
    _draw_wrapped_text(draw, entry_rule, (36, 361), 1120, tiny, entry_color, max_lines=1)
    capital_text = "자금 계획  " + capital_action_line(entry)
    full_plan = capital_plan_line(entry)
    if full_plan:
        capital_text += " · " + full_plan
    _draw_wrapped_text(
        draw,
        capital_text,
        (36, 390),
        1120,
        tiny,
        "#ffd43b" if entry.action != "first_entry_review" else UP,
        max_lines=1,
    )
    dominance = analysis.dominance_context or {}
    if dominance.get("value") is not None:
        regime = dominance.get("regime")
        dominance_color = (
            UP if regime in {"risk_on", "supportive"}
            else DOWN if regime in {"risk_off", "cautious"}
            else MUTED
        )
        change = dominance.get("change_24h_pp")
        change_text = "수집 중" if change is None else f"{float(change):+.2f}%p"
        dominance_text = (
            f"USDT.D 시장 필터  {float(dominance['value']):.2f}% · "
            f"24h {change_text} · {dominance.get('label', '중립')} — "
            "시장 환경 필터이며 단독 매수·추격 근거 아님"
        )
        _draw_wrapped_text(
            draw,
            dominance_text,
            (36, 419),
            1120,
            tiny,
            dominance_color,
            max_lines=1,
        )
    else:
        draw.text((36, 419), "USDT.D 시장 필터 데이터 수집 중", font=tiny, fill=MUTED)
    conclusion = "한줄 결론  " + _analysis_one_line(analysis, detail)
    _draw_wrapped_text(draw, conclusion, (36, 448), 1120, tiny, TEXT, max_lines=2)
    notices = build_investor_notices(analysis, detail, limit=1)
    if notices:
        notice = notices[0]
        notice_color = DOWN if notice.level == "risk" else "#ffd43b" if notice.level == "caution" else "#4dabf7"
        draw.rounded_rectangle(
            (36, 503, 1164, 545),
            radius=8,
            fill="#1b2029",
            outline=notice_color,
            width=2,
        )
        _draw_wrapped_text(
            draw,
            "투자자 체크  " + investor_notice_line(notice),
            (50, 512),
            1095,
            tiny,
            notice_color,
            max_lines=1,
        )
    if sentiment is not None:
        sentiment_color = (
            "#4dabf7" if sentiment.value <= 44
            else "#ffd43b" if sentiment.value <= 55
            else "#ff922b" if sentiment.value <= 74
            else DOWN
        )
        draw.rounded_rectangle(
            (36, 558, 1164, 628),
            radius=10,
            fill="#161d29",
            outline=sentiment_color,
            width=2,
        )
        heading = (
            f"공포·탐욕 {sentiment.value}/100 · {sentiment.label} · "
            f"전일 {format_change(sentiment.change_1d)} · 7일 {format_change(sentiment.change_7d)}"
        )
        draw.text((50, 566), heading, font=small, fill=sentiment_color)
        attribution = "BTC 중심 · 출처 Alternative.me"
        draw.text(
            (1148 - draw.textlength(attribution, font=tiny), 569),
            attribution,
            font=tiny,
            fill=MUTED,
        )
        _draw_wrapped_text(
            draw,
            sentiment.guidance,
            (50, 598),
            1085,
            tiny,
            TEXT,
            max_lines=1,
        )
    else:
        draw.text(
            (36, 584),
            "공포·탐욕 지수 일시 미수신 · 기술적 구조만 사용",
            font=tiny,
            fill=MUTED,
        )

    if macro is not None:
        macro_color = UP if macro.score >= 65 else DOWN if macro.score <= 35 else "#ffd43b"
        draw.rounded_rectangle(
            (36, 641, 1164, 724),
            radius=10,
            fill="#121d29",
            outline=macro_color,
            width=2,
        )
        draw.text(
            (50, 650),
            f"거시환경 {macro.regime} {macro.score}/100  ·  FRED 최신 영업일 자료",
            font=small,
            fill=macro_color,
        )
        slots = (50, 325, 590, 855)
        short_labels = {"DGS10": "US10Y", "DTWEXBGS": "달러", "VIXCLS": "VIX", "NASDAQCOM": "나스닥"}
        for x, indicator in zip(slots, macro.indicators):
            change = (
                f"{indicator.change_5d:+.2f}%"
                if indicator.series_id != "DGS10"
                else f"{indicator.change_5d:+.2f}%p"
            )
            value = _fmt_price(indicator.value) + indicator.unit
            impact_color = UP if indicator.impact > 0 else DOWN if indicator.impact < 0 else MUTED
            text = f"{short_labels[indicator.series_id]} {value} · 5d {change}"
            draw.text((x, 687), text, font=tiny, fill=impact_color)
    else:
        draw.text((36, 674), "거시환경 데이터 일시 미수신 · 기술적 분석만 표시", font=tiny, fill=MUTED)


def _timeframe_state(timeframe: TimeframeAnalysis) -> tuple[str, str]:
    if timeframe.ema20 is not None and timeframe.ema50 is not None and timeframe.ema200 is not None:
        if timeframe.ema20 > timeframe.ema50 > timeframe.ema200:
            return "UP 정배열", UP
        if timeframe.ema20 < timeframe.ema50 < timeframe.ema200:
            return "DN 역배열", DOWN
    if timeframe.direction_score >= 55:
        return "상승 혼조", "#69db7c"
    if timeframe.direction_score <= 45:
        return "하락 혼조", "#ff8787"
    return "중립", "#ffd43b"


def _ema_order_text(timeframe: TimeframeAnalysis) -> str:
    available = [
        (period, value)
        for period, value in ((20, timeframe.ema20), (50, timeframe.ema50), (200, timeframe.ema200))
        if value is not None
    ]
    if len(available) < 2:
        return "EMA 데이터 부족"
    ordered = ">".join(str(period) for period, _ in sorted(available, key=lambda item: item[1], reverse=True))
    return f"EMA {ordered}"


def _analysis_one_line(analysis: MarketAnalysis, detail: TimeframeAnalysis) -> str:
    interval = TIMEFRAME_LABELS.get(detail.interval, detail.interval)
    if detail.ema20 is None:
        return "EMA 데이터가 부족해 다음 봉 확인이 필요합니다."
    above = analysis.current_price >= detail.ema20
    if analysis.direction_score >= 60:
        return (
            f"상승 우위; {interval} EMA20 위 유지 시 흐름 지속, 아래 마감 시 상승 힘 약화."
            if above
            else f"상승 점수 우위지만 {interval} EMA20 아래라 재회복 전까지 추격 주의."
        )
    if analysis.direction_score <= 40:
        return (
            f"하락 우위; {interval} EMA20 아래에서 저점을 낮추면 추가 하락 경계, 위 회복 시 약화."
            if not above
            else f"하락 점수 우위지만 {interval} EMA20 회복 상태라 반등 지속 여부 확인."
        )
    return f"혼조 구간; {interval} EMA20 위 마감은 개선, 아래 마감은 하락 압력 우위."


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    position: tuple[int, int],
    max_width: int,
    font: ImageFont.FreeTypeFont,
    fill: str,
    *,
    max_lines: int,
) -> None:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) == max_lines - 1:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    for index, line in enumerate(lines[:max_lines]):
        draw.text((position[0], position[1] + index * 25), line, font=font, fill=fill)


def _draw_panel(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    interval: str,
    closed: list[Candle],
    live: Candle | None,
    signals: list[EmaSignal],
    timeframe_analysis: TimeframeAnalysis | None,
    small: ImageFont.FreeTypeFont,
    tiny: ImageFont.FreeTypeFont,
    panel_title: ImageFont.FreeTypeFont,
) -> None:
    left, top, right, bottom = bounds
    draw.rounded_rectangle(bounds, radius=18, fill=PANEL, outline=GRID, width=2)
    chart_left, chart_top = left + 20, top + 66
    chart_right, chart_bottom = right - 112, bottom - 34

    data = list(closed)
    if live is not None and (not data or live.open_time != data[-1].open_time):
        data.append(live)
    if len(data) < 30:
        draw.text((left + 25, top + 85), "차트 데이터가 부족합니다.", font=small, fill=MUTED)
        return

    all_closes = [candle.close for candle in data]
    series = {period: ema(all_closes, period) for period in (20, 50, 200)}
    visible_count = min(72, len(data))
    start = len(data) - visible_count
    visible = data[start:]
    visible_series = {period: values[start:] for period, values in series.items()}

    values = [value for candle in visible for value in (candle.low, candle.high)]
    values.extend(
        value
        for period_values in visible_series.values()
        for value in period_values
        if value is not None
    )
    values.extend(signal.ema_value for signal in signals if signal.ema_value is not None)
    if timeframe_analysis is not None:
        values.extend(
            value
            for value in (timeframe_analysis.support, timeframe_analysis.resistance)
            if value is not None
        )
    low_value, high_value = min(values), max(values)
    padding = max((high_value - low_value) * 0.08, high_value * 0.001)
    low_value -= padding
    high_value += padding

    def y_of(value: float) -> float:
        return chart_bottom - (value - low_value) / (high_value - low_value) * (chart_bottom - chart_top)

    step = (chart_right - chart_left) / max(len(visible), 1)

    def x_of(position: int) -> float:
        return chart_left + step * (position + 0.5)

    for line_index in range(5):
        y = chart_top + (chart_bottom - chart_top) * line_index / 4
        value = high_value - (high_value - low_value) * line_index / 4
        draw.line((chart_left, y, chart_right, y), fill=GRID, width=1)
        draw.text((chart_right + 12, y - 11), _fmt_price(value), font=tiny, fill=MUTED)

    if timeframe_analysis is not None:
        for label, level, color in (
            ("지지", timeframe_analysis.support, "#20c997"),
            ("저항", timeframe_analysis.resistance, "#ff5c75"),
        ):
            if level is None or not low_value <= level <= high_value:
                continue
            level_y = y_of(level)
            _dashed_line(draw, (chart_left, level_y), (chart_right, level_y), color)
            level_text = f"{label} {_fmt_price(level)}"
            text_width = draw.textlength(level_text, font=tiny)
            draw.text((chart_right - text_width - 8, level_y - 22), level_text, font=tiny, fill=color)

    timeframe = TIMEFRAME_LABELS.get(interval, interval)
    current = live.close if live is not None else visible[-1].close
    signal_labels = []
    for signal in signals:
        if signal.kind == "touch":
            signal_labels.append(f"EMA{signal.ema_period} 터치")
        elif signal.alignment == "bullish":
            signal_labels.append("정배열 전환")
        elif signal.alignment == "bearish":
            signal_labels.append("역배열 전환")
    if not signal_labels and timeframe_analysis is not None:
        structure_label = {
            "bullish": "상승 구조",
            "bullish_transition": "상승 전환 시도",
            "bearish": "하락 구조",
            "bearish_transition": "하락 전환 경계",
            "mixed": "혼조 구조",
        }.get(timeframe_analysis.structure, "시장 구조")
        signal_labels.append(structure_label)
    draw.text((left + 22, top + 18), timeframe, font=panel_title, fill=TEXT)
    draw.text((left + 92, top + 23), " · ".join(dict.fromkeys(signal_labels)), font=small, fill=TEXT)
    price_text = f"현재 {_fmt_price(current)}"
    price_width = draw.textlength(price_text, font=small)
    draw.text((right - price_width - 22, top + 23), price_text, font=small, fill=TEXT)

    candle_width = max(3, min(10, int(step * 0.62)))
    for position, candle in enumerate(visible):
        x = x_of(position)
        color = UP if candle.close >= candle.open else DOWN
        draw.line((x, y_of(candle.high), x, y_of(candle.low)), fill=color, width=2)
        body_top = y_of(max(candle.open, candle.close))
        body_bottom = y_of(min(candle.open, candle.close))
        if body_bottom - body_top < 2:
            body_bottom = body_top + 2
        outline = TEXT if live is not None and candle.open_time == live.open_time else color
        draw.rectangle(
            (x - candle_width / 2, body_top, x + candle_width / 2, body_bottom),
            fill=color,
            outline=outline,
            width=2 if outline == TEXT else 1,
        )

    for period in (20, 50, 200):
        points = [
            (x_of(position), y_of(value))
            for position, value in enumerate(visible_series[period])
            if value is not None
        ]
        if len(points) >= 2:
            draw.line(points, fill=EMA_COLORS[period], width=3 if period != 200 else 4, joint="curve")

    touch_signals = [signal for signal in signals if signal.kind == "touch" and signal.ema_value]
    for signal_index, signal in enumerate(touch_signals):
        signal_position = next(
            (
                index
                for index, candle in enumerate(visible)
                if candle.open_time == signal.bar_open_time
            ),
            len(visible) - 1,
        )
        signal_x = x_of(signal_position)
        level_y = y_of(float(signal.ema_value))
        color = EMA_COLORS.get(signal.ema_period or 20, TEXT)
        radius = 10 + signal_index * 2
        # A red outer ring makes the exact touch point immediately visible.
        outer_radius = radius + 7
        draw.ellipse(
            (
                signal_x - outer_radius,
                level_y - outer_radius,
                signal_x + outer_radius,
                level_y + outer_radius,
            ),
            outline="#ff3b30",
            width=5,
        )
        draw.ellipse(
            (signal_x - radius, level_y - radius, signal_x + radius, level_y + radius),
            outline="#ffffff",
            fill=color,
            width=4,
        )
        guide_start = max(chart_left, signal_x - step * 12)
        _dashed_line(draw, (guide_start, level_y), (chart_right, level_y), color)
        label = f"EMA{signal.ema_period}  {_fmt_price(signal.ema_value)}"
        label_width = draw.textlength(label, font=tiny) + 18
        label_x = min(chart_right - label_width, max(chart_left, signal_x - label_width - 18))
        label_y = max(chart_top, min(chart_bottom - 28, level_y - 32 - signal_index * 28))
        draw.rounded_rectangle(
            (label_x, label_y, label_x + label_width, label_y + 25),
            radius=6,
            fill="#0b1018",
            outline=color,
            width=2,
        )
        draw.text((label_x + 8, label_y + 1), label, font=tiny, fill=color)

    if touch_signals:
        primary = max(touch_signals, key=lambda item: item.ema_period or 0)
        primary_level = float(primary.ema_value)
        primary_y = y_of(primary_level)
        primary_position = next(
            (
                index
                for index, candle in enumerate(visible)
                if candle.open_time == primary.bar_open_time
            ),
            len(visible) - 1,
        )
        primary_x = x_of(primary_position)
        above = primary.reaction == "closed_above"
        status = "지지 반응 확인" if above else "저항·이탈 반응"
        condition = "확정봉 선 위 마감" if above else "확정봉 선 아래 마감"
        callout_x = chart_left + 14
        callout_y = chart_top + 10
        callout_width = 322
        callout_height = 61
        draw.rounded_rectangle(
            (
                callout_x,
                callout_y,
                callout_x + callout_width,
                callout_y + callout_height,
            ),
            radius=10,
            fill="#2a1115",
            outline="#ff3b30",
            width=3,
        )
        draw.text(
            (callout_x + 13, callout_y + 6),
            f"EMA{primary.ema_period} 접촉 · {status}",
            font=small,
            fill="#ffb3ba",
        )
        draw.text(
            (callout_x + 13, callout_y + 34),
            condition,
            font=tiny,
            fill=TEXT,
        )
        arrow_start = (callout_x + callout_width, callout_y + callout_height / 2)
        arrow_end = (primary_x - 22, primary_y)
        draw.line((*arrow_start, *arrow_end), fill="#ff3b30", width=3)
        draw.polygon(
            [
                (arrow_end[0], arrow_end[1]),
                (arrow_end[0] - 10, arrow_end[1] - 6),
                (arrow_end[0] - 10, arrow_end[1] + 6),
            ],
            fill="#ff3b30",
        )

    first_time = datetime.fromtimestamp(visible[0].open_time / 1000, tz=UTC).astimezone(KST)
    last_time = datetime.fromtimestamp(visible[-1].open_time / 1000, tz=UTC).astimezone(KST)
    draw.text((chart_left, chart_bottom + 8), first_time.strftime("%m/%d %H:%M"), font=tiny, fill=MUTED)
    last_label = last_time.strftime("%m/%d %H:%M")
    draw.text(
        (chart_right - draw.textlength(last_label, font=tiny), chart_bottom + 8),
        last_label,
        font=tiny,
        fill=MUTED,
    )


def _draw_scenario_panel(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    analysis: MarketAnalysis,
    candles: list[Candle],
    small: ImageFont.FreeTypeFont,
    tiny: ImageFont.FreeTypeFont,
) -> None:
    left, top, right, bottom = bounds
    draw.rounded_rectangle(bounds, radius=18, fill=PANEL, outline=GRID, width=2)
    draw.text((left + 22, top + 16), "과거 유사 구간 · 조건부 가능 경로", font=_font(27, bold=True), fill=TEXT)
    draw.text(
        (left + 468, top + 23),
        "과거 실제 경로를 현재 가격 0%에 겹친 것 · 예측/목표가 아님",
        font=tiny,
        fill="#ffb3ba",
    )
    if len(candles) < 100 or not analysis.analogs:
        draw.text((left + 24, top + 92), "유사 구간 경로 데이터가 부족합니다.", font=small, fill=MUTED)
        return

    lookback = 24
    future = 30
    dates = [datetime.fromtimestamp(candle.open_time / 1000, tz=UTC).date().isoformat() for candle in candles]
    paths: list[tuple[str, list[float]]] = []
    for analog in analysis.analogs[:3]:
        candidates = [
            index
            for index, value in enumerate(dates)
            if value == analog.end_date and index >= lookback - 1 and index + future < len(candles)
        ]
        if not candidates:
            continue
        end_index = candidates[-1]
        base = candles[end_index].close
        values = [
            (candle.close / base - 1.0) * 100
            for candle in candles[end_index - lookback + 1:end_index + future + 1]
        ]
        paths.append((f"{analog.start_date}~{analog.end_date}", values))

    current_base = candles[-1].close
    current_values = [
        (candle.close / current_base - 1.0) * 100
        for candle in candles[-lookback:]
    ]
    if not paths:
        draw.text((left + 24, top + 92), "과거 유사 날짜의 경로를 찾지 못했습니다.", font=small, fill=MUTED)
        return

    chart_left, chart_top = left + 44, top + 72
    # Reserve a dedicated two-line footer inside the scenario panel so the
    # wave/Fibonacci note never sits on top of the chart axis labels.
    chart_right, chart_bottom = right - 260, bottom - 132
    all_values = list(current_values)
    for _, values in paths:
        all_values.extend(values)
    low_value = min(all_values + [-2.0])
    high_value = max(all_values + [2.0])
    padding = max((high_value - low_value) * 0.08, 1.0)
    low_value -= padding
    high_value += padding

    def x_of(step_index: int) -> float:
        return chart_left + (step_index + lookback - 1) / (lookback - 1 + future) * (chart_right - chart_left)

    def y_of(value: float) -> float:
        return chart_bottom - (value - low_value) / (high_value - low_value) * (chart_bottom - chart_top)

    for line_index in range(5):
        value = high_value - (high_value - low_value) * line_index / 4
        y = y_of(value)
        draw.line((chart_left, y, chart_right, y), fill=GRID, width=1)
        draw.text((chart_left + 4, y - 20), f"{value:+.1f}%", font=tiny, fill=MUTED)
    zero_x = x_of(0)
    draw.line((zero_x, chart_top, zero_x, chart_bottom), fill="#e8edf4", width=2)
    draw.text((zero_x - 46, chart_bottom + 8), "현재 0", font=tiny, fill=TEXT)
    draw.text((chart_left, chart_bottom + 8), "과거 모양", font=tiny, fill=MUTED)
    draw.text((chart_right - 150, chart_bottom + 8), "이후 30봉(약 5일)", font=tiny, fill=MUTED)

    current_points = [
        (x_of(index - lookback + 1), y_of(value))
        for index, value in enumerate(current_values)
    ]
    draw.line(current_points, fill="#ffffff", width=4, joint="curve")
    colors = ("#b197fc", "#4dabf7", "#ffa94d")
    legend_y = chart_top
    for path_index, (label, values) in enumerate(paths):
        color = colors[path_index % len(colors)]
        past_points = [
            (x_of(index - lookback + 1), y_of(value))
            for index, value in enumerate(values[:lookback])
        ]
        future_points = [
            (x_of(index), y_of(value))
            for index, value in enumerate(values[lookback - 1:])
        ]
        draw.line(past_points, fill=color, width=2, joint="curve")
        _dashed_polyline(draw, future_points, color, width=3)
        endpoint = values[-1]
        draw.ellipse(
            (x_of(future) - 5, y_of(endpoint) - 5, x_of(future) + 5, y_of(endpoint) + 5),
            fill=color,
        )
        draw.line((right - 234, legend_y + 10, right - 206, legend_y + 10), fill=color, width=4)
        draw.text((right - 196, legend_y - 3), label, font=tiny, fill=TEXT)
        draw.text((right - 196, legend_y + 21), f"이후 {endpoint:+.1f}%", font=small, fill=color)
        legend_y += 68

    detail = analysis.timeframes.get("4h") or next(iter(analysis.timeframes.values()))
    condition_y = bottom - 43
    support = _fmt_price(detail.support) if detail.support is not None else "-"
    resistance = _fmt_price(detail.resistance) if detail.resistance is not None else "-"
    condition = (
        f"상방 조건: 저항 {resistance} 종가 돌파+거래량  ·  "
        f"하방 위험: 지지 {support} 종가 이탈"
    )
    draw.text(
        (left + 24, condition_y - 36),
        "파동·피보나치  " + wave_summary(analysis.wave_context),
        font=tiny,
        fill="#f8d477",
    )
    draw.text((left + 24, condition_y), condition, font=tiny, fill="#cbd5e1")


def _dashed_polyline(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    color: str,
    *,
    width: int,
) -> None:
    for index in range(len(points) - 1):
        if index % 2 == 0:
            draw.line((*points[index], *points[index + 1]), fill=color, width=width)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REGULAR
    return ImageFont.truetype(str(path), size=size)


def _dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    color: str,
) -> None:
    x1, y1 = start
    x2, _ = end
    cursor = x1
    while cursor < x2:
        draw.line((cursor, y1, min(cursor + 9, x2), y1), fill=color, width=2)
        cursor += 16
