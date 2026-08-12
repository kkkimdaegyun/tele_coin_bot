from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from analysis_engine import MarketAnalysis
from analysis_report import build_analysis_message, build_ema_signal_message, build_hourly_summary_message
from analysis_service import SUPPORTED_SYMBOLS, analyze_symbol
from ema_chart import build_ema_chart_image, build_market_overview_image
from ema_signals import collect_ema_signals
from storage import ChartTeacherStore
from telegram_client import send_telegram, send_telegram_photo


log = logging.getLogger("chart-teacher-monitor")
DEFAULT_MONITOR_SYMBOLS = ("BTCUSDT", "ETHUSDT")


def configured_symbols() -> list[str]:
    raw = os.getenv("MONITOR_SYMBOLS", ",".join(DEFAULT_MONITOR_SYMBOLS))
    requested = [item.strip().upper() for item in raw.split(",") if item.strip()]
    return [symbol for symbol in requested if symbol in SUPPORTED_SYMBOLS]


async def scan_and_notify(symbol: str, analysis: MarketAnalysis | None = None) -> bool:
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

    try:
        image = await build_market_overview_image(analysis)
        await send_telegram_photo(
            image,
            (
                f"📊 <b>{symbol.removesuffix('USDT')} 트레이더 한눈 요약</b>\n"
                "시간대·EMA·지표·지지저항·패턴·과거 유사 경로·거시환경"
            ),
            filename=f"{symbol.lower()}_market_overview.png",
        )
    except Exception:
        log.exception("Market overview image delivery failed: symbol=%s", symbol)
    message = build_analysis_message(analysis, trigger=significant["description"])
    await send_telegram(message)
    log.info("Important analysis sent: symbol=%s event=%s", symbol, significant["event"])
    return True


async def send_hourly_summary(analyses: list[MarketAnalysis]) -> bool:
    if not analyses:
        return False
    now = datetime.now(UTC)
    fingerprint_payload = {
        "event": "hourly_summary",
        "hour_utc": now.strftime("%Y-%m-%dT%H"),
        "symbols": sorted(analysis.symbol for analysis in analyses),
    }
    store = ChartTeacherStore()
    now_ms = int(now.timestamp() * 1000)
    if not store.claim_signal("MARKET", fingerprint_payload, now_ms):
        return False

    try:
        for analysis in analyses:
            try:
                image = await build_market_overview_image(analysis)
                coin = analysis.symbol.removesuffix("USDT")
                await send_telegram_photo(
                    image,
                    (
                        f"🕐 <b>{coin} 1시간 트레이더 보드</b>\n"
                        "실제 가격·시간대·지표·패턴·과거 유사 경로·거시환경"
                    ),
                    filename=f"{analysis.symbol.lower()}_hourly_board.png",
                )
            except Exception:
                log.exception(
                    "Hourly market image delivery failed: symbol=%s",
                    analysis.symbol,
                )
        await send_telegram(build_hourly_summary_message(analyses))
    except Exception:
        # Allow the next five-minute scan to retry this hour if Telegram was unavailable.
        store.release_signal(fingerprint_payload)
        raise
    log.info("Hourly summary sent: symbols=%s", ",".join(item.symbol for item in analyses))
    return True


async def scan_ema_and_notify(symbol: str, analysis: MarketAnalysis) -> int:
    enabled = os.getenv("EMA_SIGNAL_ALERTS_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not enabled:
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
    claimed: list[tuple[object, dict]] = []
    for signal in signals:
        fingerprint_payload = {
            "symbol": symbol,
            "event": signal.event,
            "interval": signal.interval,
            "bar": signal.bar_open_time,
        }
        if store.claim_signal(symbol, fingerprint_payload, now_ms):
            claimed.append((signal, fingerprint_payload))

    if not claimed:
        return 0
    claimed_signals = [signal for signal, _ in claimed]
    try:
        try:
            chart_image = await build_ema_chart_image(symbol, claimed_signals, analysis=analysis)
            coin = symbol.removesuffix("USDT")
            timeframes = ", ".join(
                dict.fromkeys(signal.interval.upper() for signal in claimed_signals)
            )
            await send_telegram_photo(
                chart_image,
                (
                    f"📈 <b>{coin} 이평선 신호 차트</b> · {timeframes}\n"
                    "노랑 EMA20 · 주황 EMA50 · 파랑 EMA200\n"
                    "흰 테두리=진행 중 봉 · 큰 원=터치 위치"
                ),
                filename=f"{symbol.lower()}_ema_signal.png",
            )
        except Exception:
            # Keep the important text alert even if rendering or photo upload fails.
            log.exception("EMA chart image delivery failed: symbol=%s", symbol)
        await send_telegram(
            build_ema_signal_message(
                analysis,
                claimed_signals,
                tolerance_percent=tolerance_percent,
            )
        )
    except Exception:
        for _, fingerprint_payload in claimed:
            store.release_signal(fingerprint_payload)
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
        if item["status"] == "confirmed" and item["confidence"] >= 75
    ]
    if confirmed:
        item = confirmed[0]
        return {
            "event": item["name"],
            "timeframe": item["timeframe"],
            "description": f"{item['timeframe']} {item['name']} 확인",
        }
    if analysis.bottom_score >= 70:
        return {"event": "bottom_candidate", "timeframe": "4h", "description": "바닥 후보 점수 70 이상"}
    if analysis.signal_strength >= 70 and (analysis.direction_score >= 68 or analysis.direction_score <= 32):
        return {"event": "timeframe_confluence", "timeframe": "4h", "description": "다중 시간대 방향 일치"}
    return None


async def monitor_loop() -> None:
    interval_seconds = max(60, int(os.getenv("MONITOR_INTERVAL_SECONDS", "300")))
    hourly_summary_enabled = os.getenv("HOURLY_SUMMARY_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    await asyncio.sleep(10)
    while True:
        symbols = configured_symbols()
        analyses: list[MarketAnalysis] = []
        for symbol in symbols:
            try:
                analysis = await analyze_symbol(symbol)
                analyses.append(analysis)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Market monitor scan failed: symbol=%s", symbol)
                continue
            try:
                await scan_and_notify(symbol, analysis=analysis)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Important signal delivery failed: symbol=%s", symbol)
            try:
                await scan_ema_and_notify(symbol, analysis)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("EMA signal delivery failed: symbol=%s", symbol)

        if hourly_summary_enabled and len(analyses) == len(symbols) and analyses:
            try:
                await send_hourly_summary(analyses)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Hourly summary delivery failed")
        await asyncio.sleep(interval_seconds)
