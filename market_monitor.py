from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone

from analysis_engine import MarketAnalysis, analyze_timeframe
from analysis_report import (
    build_analysis_message,
    build_compact_analysis_caption,
    build_compact_ema_caption,
    build_ema_signal_message,
    build_hourly_summary_message,
    build_usdt_dominance_message,
)
from analysis_service import SUPPORTED_SYMBOLS, analyze_symbol
from dominance_context import UsdtDominanceSnapshot, get_usdt_dominance
from ema_chart import build_ema_chart_image, build_market_overview_image
from ema_signals import collect_ema_signals
from entry_strategy import capital_guidance_enabled, capital_plan_for_symbol, evaluate_entry
from entry_strategy import format_krw
from indicator_summary import core_indicator_line
from market_data import BinanceMarketData, closed_candles
from position_manager import evaluate_position, position_change_percent
from sentiment_context import get_fear_greed
from storage import ChartTeacherStore
from telegram_client import send_telegram, send_telegram_photo
from weekly_strategy import evaluate_weekly_cycle
from strategy_universe import ACTIVE_MONITOR_SYMBOLS, is_market_context_symbol, is_trade_symbol


log = logging.getLogger("chart-teacher-monitor")
DEFAULT_MONITOR_SYMBOLS = ACTIVE_MONITOR_SYMBOLS
KST = timezone(timedelta(hours=9))
_RECENT_ALERT_IMAGES: dict[str, int] = {}


def _position_price(value: float) -> str:
    if value >= 1_000:
        return f"{value / 1_000:.2f}".rstrip("0").rstrip(".") + "K"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _mark_recent_alert_image(symbol: str, now_ms: int | None = None) -> None:
    _RECENT_ALERT_IMAGES[symbol] = now_ms or int(datetime.now(UTC).timestamp() * 1000)


def _recent_alert_image_active(symbol: str, now_ms: int | None = None) -> bool:
    now_ms = now_ms or int(datetime.now(UTC).timestamp() * 1000)
    window_minutes = max(
        0.0,
        float(os.getenv("HOURLY_DUPLICATE_WINDOW_MINUTES", "10")),
    )
    last_sent = _RECENT_ALERT_IMAGES.get(symbol)
    return last_sent is not None and now_ms - last_sent < window_minutes * 60 * 1000


def quiet_hours_active(now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC).astimezone(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)
    start = max(0, min(int(os.getenv("QUIET_HOURS_START_KST", "0")), 23))
    end = max(0, min(int(os.getenv("QUIET_HOURS_END_KST", "7")), 23))
    if start == end:
        return False
    if start < end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end


def configured_symbols() -> list[str]:
    raw = os.getenv("MONITOR_SYMBOLS", ",".join(DEFAULT_MONITOR_SYMBOLS))
    requested = [item.strip().upper() for item in raw.split(",") if item.strip()]
    return [
        symbol
        for symbol in requested
        if symbol in SUPPORTED_SYMBOLS and symbol in ACTIVE_MONITOR_SYMBOLS
    ]


async def scan_dominance_and_notify(snapshot: UsdtDominanceSnapshot | None) -> bool:
    enabled = os.getenv("USDT_DOMINANCE_ALERTS_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not enabled or snapshot is None or snapshot.change_24h_pp is None:
        return False
    if snapshot.regime not in {"risk_on", "risk_off"}:
        return False
    threshold = max(
        0.05,
        float(os.getenv("USDT_DOMINANCE_ALERT_THRESHOLD_PP", "0.10")),
    )
    if abs(snapshot.change_24h_pp) < threshold:
        return False
    critical = snapshot.regime == "risk_off"
    if quiet_hours_active() and not critical:
        log.info("USDT dominance risk-on alert suppressed during quiet hours")
        return False

    store = ChartTeacherStore()
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    event = f"usdt_dominance:{snapshot.regime}"
    payload = {
        "event": event,
        "dominance_band": round(snapshot.value * 10),
        "change_band": round(snapshot.change_24h_pp * 10),
        "snapshot_bucket": snapshot.timestamp // (12 * 60 * 60),
    }
    if not store.claim_signal("USDT.D", payload, now_ms):
        return False
    cooldown_hours = max(
        1.0,
        float(os.getenv("USDT_DOMINANCE_COOLDOWN_HOURS", "12")),
    )
    cooldown_key = f"dominance:{snapshot.regime}"
    if not store.claim_cooldown(
        cooldown_key,
        now_ms,
        int(cooldown_hours * 60 * 60 * 1000),
    ):
        log.info("USDT dominance alert suppressed by cooldown: regime=%s", snapshot.regime)
        return False
    try:
        await send_telegram(build_usdt_dominance_message(snapshot))
    except Exception:
        store.release_signal(payload)
        store.release_cooldown(cooldown_key, now_ms)
        raise
    log.info(
        "USDT dominance alert sent: value=%.4f regime=%s change24h_pp=%+.4f",
        snapshot.value,
        snapshot.regime,
        snapshot.change_24h_pp,
    )
    return True


async def scan_weekly_cycle_and_notify(symbol: str) -> bool:
    if not is_trade_symbol(symbol):
        return False
    provider = BinanceMarketData()
    candles = closed_candles(await provider.fetch_klines(symbol, "1w", limit=300))
    if len(candles) < 30:
        return False
    store = ChartTeacherStore()
    store.upsert_candles(symbol, "1w", candles)
    detail = analyze_timeframe("1w", candles)
    decision = evaluate_weekly_cycle(detail)
    if decision.action == "wait":
        return False
    critical = decision.action == "sell_candidate"
    if quiet_hours_active() and not critical:
        return False
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    payload = {
        "symbol": symbol,
        "event": f"weekly:{decision.action}",
        "bar": candles[-1].open_time,
    }
    if not store.claim_signal(symbol, payload, now_ms):
        return False
    coin = symbol.removesuffix("USDT")
    color = "🟢" if decision.action == "buy_candidate" else "🔴"
    plan = capital_plan_for_symbol(symbol)
    action = (
        (
            f"주봉은 큰 매집 후보 구간입니다. 전액 진입하지 말고 일봉 회복과 상대강도 확인 뒤 "
            f"1차 예산 상한 {format_krw(plan.first_krw)}을 검토합니다."
            if capital_guidance_enabled()
            else "주봉은 관찰 후보입니다. 사이클 조건과 손실 한도 검증 전에는 실행 금액 안내를 잠급니다."
        )
        if decision.action == "buy_candidate"
        else "주봉 추세가 약해졌습니다. 신규매수보다 보유분 축소·보호 기준을 먼저 확인합니다."
    )
    message = "\n".join(
        [
            f"{color} <b>{coin} · {decision.label}</b>",
            f"확정 주봉 조건 {decision.score}/100(확률 아님)",
            "",
            "<b>근거</b>",
            *[f"• {reason}" for reason in decision.reasons[:4]],
            "",
            f"<b>행동</b>\n{action}",
            "",
            "진행 중인 주봉은 사용하지 않으며 다음 주봉 마감 전에는 이 신호가 바뀌지 않습니다.",
        ]
    )
    try:
        await send_telegram(message)
    except Exception:
        store.release_signal(payload)
        raise
    log.info("Weekly cycle alert sent: symbol=%s action=%s", symbol, decision.action)
    return True


async def scan_and_notify(symbol: str, analysis: MarketAnalysis | None = None) -> bool:
    if not is_trade_symbol(symbol):
        return False
    analysis = analysis or await analyze_symbol(symbol)
    significant = _significant_signal(analysis)
    if significant is None:
        log.info(
            "No important change: symbol=%s direction=%s strength=%s bottom=%s",
            symbol,
            analysis.direction_score,
            analysis.signal_strength,
            analysis.bottom_score,
        )
        return False
    if quiet_hours_active() and not significant.get("critical", False):
        log.info(
            "Non-critical signal suppressed during quiet hours: symbol=%s event=%s",
            symbol,
            significant["event"],
        )
        return False

    interval = significant.get("timeframe", "4h")
    store = ChartTeacherStore()
    candles = store.load_candles(symbol, interval, limit=1)
    if not candles:
        return False
    fingerprint_payload = {
        "symbol": symbol,
        "interval": interval,
        "bar": candles[-1].open_time,
        "event": significant["event"],
        "direction_band": analysis.direction_score // 10,
        "bottom_band": analysis.bottom_score // 10,
    }
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    if not store.claim_signal(symbol, fingerprint_payload, now_ms):
        return False
    cooldown_hours = max(
        1.0,
        float(os.getenv("IMPORTANT_SIGNAL_COOLDOWN_HOURS", "4")),
    )
    if significant["event"] == "entry:wait_pullback":
        cooldown_hours = max(
            cooldown_hours,
            float(os.getenv("FOMO_WARNING_COOLDOWN_HOURS", "12")),
        )
    cooldown_key = (
        f"important:{symbol}:{interval}:{significant['event']}"
    )
    if not store.claim_cooldown(
        cooldown_key,
        now_ms,
        int(cooldown_hours * 60 * 60 * 1000),
    ):
        log.info(
            "Important signal suppressed by cooldown: symbol=%s event=%s",
            symbol,
            significant["event"],
        )
        return False

    sentiment = await get_fear_greed()
    delivered = False
    try:
        image = await build_market_overview_image(analysis, sentiment=sentiment)
        await send_telegram_photo(
            image,
            build_compact_analysis_caption(analysis, significant),
            filename=f"{symbol.lower()}_market_overview.png",
        )
        delivered = True
        _mark_recent_alert_image(symbol, now_ms)
    except Exception:
        log.exception("Market overview image delivery failed: symbol=%s", symbol)
    if not delivered:
        message = build_analysis_message(
            analysis,
            trigger=significant["description"],
            sentiment=sentiment,
        )
        try:
            await send_telegram(message)
        except Exception:
            store.release_signal(fingerprint_payload)
            store.release_cooldown(cooldown_key, now_ms)
            raise
    log.info("Important analysis sent: symbol=%s event=%s", symbol, significant["event"])
    return True


async def scan_position_and_notify(symbol: str, analysis: MarketAnalysis) -> bool:
    if not is_trade_symbol(symbol):
        return False
    store = ChartTeacherStore()
    position = store.load_open_position(symbol)
    if position is None:
        return False
    decision = evaluate_position(analysis, position)
    if decision.action == "hold":
        return False
    if quiet_hours_active() and not decision.critical:
        log.info("Position alert suppressed during quiet hours: symbol=%s", symbol)
        return False

    interval = "15m" if decision.action == "second_entry_review" else "4h"
    candles = store.load_candles(symbol, interval, limit=1)
    if not candles:
        return False
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    fingerprint_payload = {
        "symbol": symbol,
        "event": f"position:{decision.action}",
        "stage": int(position["stage"]),
        "bar": candles[-1].open_time,
    }
    if not store.claim_signal(symbol, fingerprint_payload, now_ms):
        return False
    cooldown_hours = 1.0 if decision.critical else 4.0
    cooldown_key = f"position:{symbol}:{decision.action}:stage{position['stage']}"
    if not store.claim_cooldown(
        cooldown_key,
        now_ms,
        int(cooldown_hours * 60 * 60 * 1000),
    ):
        return False

    coin = symbol.removesuffix("USDT")
    current = analysis.current_price
    average = float(position["average_entry_price"])
    change = position_change_percent(position, current)
    invalidation = position.get("invalidation_price")
    lines = [
        f"🧭 <b>{coin} 보유관리 · {decision.label}</b>",
        f"현재 {_position_price(current)} · 평균 {_position_price(average)} · 가격 변화 {change:+.2f}%",
        f"누적 {format_krw(position['invested_krw'])} · {position['stage']}차 기록",
    ]
    if invalidation is not None:
        lines.append(f"보호 기준 약 {_position_price(float(invalidation))}")
    lines.extend(["", f"<b>판단 이유</b>\n{decision.reason}"])
    if decision.amount_krw > 0:
        lines.append(
            f"\n실제 추가매수 후 <code>{coin} 추가매수 "
            f"{format_krw(decision.amount_krw)} 가격</code>으로 기록하세요."
        )
    lines.append("\n자동 주문은 실행하지 않습니다.")
    try:
        await send_telegram("\n".join(lines))
    except Exception:
        store.release_signal(fingerprint_payload)
        store.release_cooldown(cooldown_key, now_ms)
        raise
    log.info("Position alert sent: symbol=%s action=%s", symbol, decision.action)
    return True


async def send_hourly_summary(
    analyses: list[MarketAnalysis],
    *,
    skip_image_symbols: set[str] | None = None,
) -> bool:
    if not analyses:
        return False
    if quiet_hours_active():
        log.info("Periodic summary suppressed during quiet hours")
        return False
    now = datetime.now(UTC)
    interval_hours = max(1, int(os.getenv("SUMMARY_INTERVAL_HOURS", "24")))
    period_bucket = int(now.timestamp()) // (interval_hours * 60 * 60)
    fingerprint_payload = {
        "event": "periodic_summary",
        "period_bucket": period_bucket,
        "interval_hours": interval_hours,
        "symbols": sorted(analysis.symbol for analysis in analyses),
    }
    store = ChartTeacherStore()
    now_ms = int(now.timestamp() * 1000)
    if not store.claim_signal("MARKET", fingerprint_payload, now_ms):
        return False

    sentiment = await get_fear_greed()
    skip_images = set(skip_image_symbols or ())
    skip_images.update(
        analysis.symbol for analysis in analyses if is_market_context_symbol(analysis.symbol)
    )
    try:
        for analysis in analyses:
            if is_market_context_symbol(analysis.symbol):
                log.info(
                    "Market-context image skipped; text summary retained: symbol=%s",
                    analysis.symbol,
                )
                continue
            if analysis.symbol in skip_images or _recent_alert_image_active(
                analysis.symbol,
                now_ms,
            ):
                log.info(
                    "Periodic duplicate image skipped: symbol=%s",
                    analysis.symbol,
                )
                continue
            try:
                image = await build_market_overview_image(analysis, sentiment=sentiment)
                coin = analysis.symbol.removesuffix("USDT")
                detail = analysis.timeframes.get("1h") or analysis.timeframes.get("4h")
                core = core_indicator_line(detail, "1H") if detail else "핵심 지표 데이터 부족"
                await send_telegram_photo(
                    image,
                    (
                        f"🗓 <b>{coin} 정기 트레이더 보드</b>\n"
                        f"{core}"
                    ),
                    filename=f"{analysis.symbol.lower()}_periodic_board.png",
                )
            except Exception:
                log.exception(
                    "Periodic market image delivery failed: symbol=%s",
                    analysis.symbol,
                )
        await send_telegram(build_hourly_summary_message(analyses, sentiment=sentiment))
    except Exception:
        # Allow the next five-minute scan to retry this hour if Telegram was unavailable.
        store.release_signal(fingerprint_payload)
        raise
    log.info("Periodic summary sent: symbols=%s", ",".join(item.symbol for item in analyses))
    return True


async def scan_ema_and_notify(symbol: str, analysis: MarketAnalysis) -> int:
    if not is_trade_symbol(symbol):
        return 0
    enabled = os.getenv("EMA_SIGNAL_ALERTS_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not enabled:
        return 0
    if quiet_hours_active():
        log.info("EMA alert suppressed during quiet hours: symbol=%s", symbol)
        return 0
    tolerance_percent = max(
        0.0,
        min(float(os.getenv("EMA_TOUCH_TOLERANCE_PERCENT", "0.05")), 1.0),
    )
    signals = await collect_ema_signals(
        symbol,
        tolerance_percent=tolerance_percent,
    )
    if not signals:
        return 0

    store = ChartTeacherStore()
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    cooldown_hours = max(
        1.0,
        float(os.getenv("EMA_ALERT_COOLDOWN_HOURS", "4")),
    )
    cooldown_ms = int(cooldown_hours * 60 * 60 * 1000)
    claimed: list[tuple[object, dict, str]] = []
    for signal in signals:
        fingerprint_payload = {
            "symbol": symbol,
            "event": signal.event,
            "interval": signal.interval,
            "bar": signal.bar_open_time,
        }
        if not store.claim_signal(symbol, fingerprint_payload, now_ms):
            continue
        cooldown_subject = (
            f"ema{signal.ema_period}"
            if signal.kind == "touch"
            else f"alignment:{signal.alignment}"
        )
        cooldown_key = f"ema:{symbol}:{signal.interval}:{cooldown_subject}"
        if store.claim_cooldown(cooldown_key, now_ms, cooldown_ms):
            claimed.append((signal, fingerprint_payload, cooldown_key))
        else:
            log.info(
                "EMA signal suppressed by cooldown: symbol=%s interval=%s event=%s",
                symbol,
                signal.interval,
                signal.event,
            )

    if not claimed:
        return 0
    claimed_signals = [signal for signal, _, _ in claimed]
    sentiment = await get_fear_greed()
    delivered = False
    try:
        try:
            chart_image = await build_ema_chart_image(
                symbol,
                claimed_signals,
                analysis=analysis,
                sentiment=sentiment,
            )
            await send_telegram_photo(
                chart_image,
                build_compact_ema_caption(analysis, claimed_signals),
                filename=f"{symbol.lower()}_ema_signal.png",
            )
            delivered = True
            _mark_recent_alert_image(symbol, now_ms)
        except Exception:
            # Keep the important text alert even if rendering or photo upload fails.
            log.exception("EMA chart image delivery failed: symbol=%s", symbol)
        if not delivered:
            await send_telegram(
                build_ema_signal_message(
                    analysis,
                    claimed_signals,
                    tolerance_percent=tolerance_percent,
                    sentiment=sentiment,
                )
            )
    except Exception:
        for _, fingerprint_payload, cooldown_key in claimed:
            store.release_signal(fingerprint_payload)
            store.release_cooldown(cooldown_key, now_ms)
        raise
    log.info(
        "EMA signal sent: symbol=%s events=%s",
        symbol,
        ",".join(signal.event for signal in claimed_signals),
    )
    return len(claimed_signals)


def _significant_signal(analysis) -> dict | None:
    confirmed = [
        item
        for item in analysis.important_patterns
        if (
            item["status"] == "confirmed"
            and item["confidence"] >= 78
            and item["timeframe"] in {"1d", "4h", "1h"}
        )
    ]
    entry = evaluate_entry(analysis)
    if entry.action == "first_entry_review":
        return {
            # A concrete first-tranche decision is more actionable than the
            # pattern that helped produce it, so it owns the alert headline.
            "event": "entry:first_entry_review",
            "timeframe": "15m",
            "description": (
                f"1차 분할 진입 검토 · {entry.setup} · "
                f"조건 충족 {entry.score}/100(확률 아님)"
            ),
            "critical": entry.score >= 75,
        }
    critical_patterns = [
        item
        for item in confirmed
        if any(keyword in item["name"] for keyword in ("돌파", "이탈", "장악형"))
    ]
    if critical_patterns:
        item = critical_patterns[0]
        return {
            "event": f"pattern:{item['name']}",
            "timeframe": item["timeframe"],
            "description": f"{item['timeframe']} {item['name']} 확인",
            "critical": True,
        }
    if entry.action == "wait_pullback":
        return {
            "event": "entry:wait_pullback",
            "timeframe": "15m",
            "description": (
                f"급등 후 추격 금지 · 눌림 대기 · "
                f"조건 충족 {entry.score}/100(확률 아님)"
            ),
            "critical": False,
        }
    for interval in ("4h", "1h"):
        detail = analysis.timeframes.get(interval)
        if not detail or detail.volume_ratio is None or detail.taker_buy_ratio is None:
            continue
        if detail.volume_ratio >= 1.5 and detail.taker_buy_ratio >= 0.62:
            return {
                "event": "high_volume_buy_pressure",
                "timeframe": interval,
                "description": (
                    f"{interval} 거래량 {detail.volume_ratio:.2f}배 · "
                    f"시장가 매수 체결 {detail.taker_buy_ratio * 100:.0f}%"
                ),
                "critical": True,
            }
        if detail.volume_ratio >= 1.5 and detail.taker_buy_ratio <= 0.38:
            return {
                "event": "high_volume_sell_pressure",
                "timeframe": interval,
                "description": (
                    f"{interval} 거래량 {detail.volume_ratio:.2f}배 · "
                    f"시장가 매도 체결 {(1 - detail.taker_buy_ratio) * 100:.0f}%"
                ),
                "critical": True,
            }
    rsi_signal = _rsi_transition_signal(analysis)
    if rsi_signal:
        return rsi_signal
    if confirmed:
        item = confirmed[0]
        return {
            "event": f"pattern:{item['name']}",
            "timeframe": item["timeframe"],
            "description": f"{item['timeframe']} {item['name']} 확인",
            "critical": False,
        }
    if analysis.bottom_score >= 75:
        return {
            "event": "bottom_candidate",
            "timeframe": "4h",
            "description": "바닥 단서 점수 75 이상",
            "critical": False,
        }
    if analysis.signal_strength >= 70 and (analysis.direction_score >= 68 or analysis.direction_score <= 32):
        return {
            "event": "timeframe_confluence",
            "timeframe": "4h",
            "description": "다중 시간대 방향 일치",
            "critical": analysis.signal_strength >= 80,
        }
    return None


def _rsi_transition_signal(analysis: MarketAnalysis) -> dict | None:
    for interval in ("1d", "4h", "1h"):
        detail = analysis.timeframes.get(interval)
        if detail is None or detail.rsi is None or detail.rsi_previous is None:
            continue
        previous = detail.rsi_previous
        current = detail.rsi
        if previous < 70 <= current:
            event, label = "rsi_overbought_entry", "RSI 70 상향 진입 · 과매수 주의"
        elif previous >= 70 > current:
            event, label = "rsi_overbought_exit", "RSI 70 하향 이탈 · 과열 완화"
        elif previous > 30 >= current:
            event, label = "rsi_oversold_entry", "RSI 30 하향 진입 · 과매도 주의"
        elif previous <= 30 < current:
            event, label = "rsi_oversold_exit", "RSI 30 상향 회복 · 매도 압력 완화"
        else:
            continue
        return {
            "event": event,
            "timeframe": interval,
            "description": f"{interval} {label} ({previous:.1f}→{current:.1f})",
            "critical": False,
        }
    return None


async def monitor_loop() -> None:
    interval_seconds = max(60, int(os.getenv("MONITOR_INTERVAL_SECONDS", "300")))
    hourly_summary_enabled = os.getenv("HOURLY_SUMMARY_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    await asyncio.sleep(10)
    last_weekly_scan_hour: str | None = None
    while True:
        symbols = configured_symbols()
        analyses: list[MarketAnalysis] = []
        alerted_symbols: set[str] = set()
        dominance = None
        weekly_scan_hour = datetime.now(UTC).strftime("%Y-%m-%dT%H")
        if weekly_scan_hour != last_weekly_scan_hour:
            for symbol in symbols:
                if not is_trade_symbol(symbol):
                    continue
                try:
                    await scan_weekly_cycle_and_notify(symbol)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Weekly cycle scan failed: symbol=%s", symbol)
            last_weekly_scan_hour = weekly_scan_hour
        try:
            dominance = await get_usdt_dominance()
            await scan_dominance_and_notify(dominance)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("USDT dominance scan failed; coin monitoring continues")
        for symbol in symbols:
            try:
                analysis = await analyze_symbol(symbol)
                analysis.dominance_context = asdict(dominance) if dominance else None
                analyses.append(analysis)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Market monitor scan failed: symbol=%s", symbol)
                continue
            try:
                if await scan_position_and_notify(symbol, analysis):
                    alerted_symbols.add(symbol)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Position monitoring failed: symbol=%s", symbol)
            try:
                if await scan_and_notify(symbol, analysis=analysis):
                    alerted_symbols.add(symbol)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Important signal delivery failed: symbol=%s", symbol)
            try:
                if await scan_ema_and_notify(symbol, analysis):
                    alerted_symbols.add(symbol)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("EMA signal delivery failed: symbol=%s", symbol)

        if hourly_summary_enabled and len(analyses) == len(symbols) and analyses:
            try:
                await send_hourly_summary(
                    analyses,
                    skip_image_symbols=alerted_symbols,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Hourly summary delivery failed")
        await asyncio.sleep(interval_seconds)
