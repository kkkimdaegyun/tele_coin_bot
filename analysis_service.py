from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from analysis_engine import MarketAnalysis, analyze_market
from analysis_report import build_analysis_message
from market_data import BinanceMarketData, closed_candles
from storage import ChartTeacherStore
from telegram_client import send_telegram


SUPPORTED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAMES = ("15m", "1h", "4h", "1d")


def normalize_symbol(symbol: str) -> str:
    symbol = symbol.upper().strip().replace("BINANCE:", "")
    if symbol not in SUPPORTED_SYMBOLS:
        raise ValueError(f"Unsupported symbol: {symbol}")
    return symbol


async def refresh_symbol(
    symbol: str,
    *,
    provider: BinanceMarketData | None = None,
    store: ChartTeacherStore | None = None,
    recent_limit: int = 1000,
) -> dict[str, int]:
    symbol = normalize_symbol(symbol)
    provider = provider or BinanceMarketData()
    store = store or ChartTeacherStore()

    async def fetch(interval: str):
        candles = closed_candles(await provider.fetch_klines(symbol, interval, limit=recent_limit))
        return interval, candles

    results = await asyncio.gather(*(fetch(interval) for interval in TIMEFRAMES))
    counts = {}
    for interval, candles in results:
        store.upsert_candles(symbol, interval, candles)
        counts[interval] = len(candles)
    return counts


async def backfill_symbol(
    symbol: str,
    years: float = 3,
    *,
    provider: BinanceMarketData | None = None,
    store: ChartTeacherStore | None = None,
) -> dict[str, int]:
    symbol = normalize_symbol(symbol)
    provider = provider or BinanceMarketData()
    store = store or ChartTeacherStore()
    start = datetime.now(UTC) - timedelta(days=365.25 * years)
    start_ms = int(start.timestamp() * 1000)
    counts = {}
    for interval in TIMEFRAMES:
        candles = closed_candles(await provider.fetch_history(symbol, interval, start_ms))
        counts[interval] = store.upsert_candles(symbol, interval, candles)
    return counts


async def analyze_symbol(
    symbol: str,
    refresh: bool = True,
    *,
    provider: BinanceMarketData | None = None,
    store: ChartTeacherStore | None = None,
    live_price: bool = True,
) -> MarketAnalysis:
    symbol = normalize_symbol(symbol)
    provider = provider or BinanceMarketData()
    store = store or ChartTeacherStore()
    if refresh:
        await refresh_symbol(symbol, provider=provider, store=store)
    datasets = {
        interval: store.load_candles(symbol, interval, limit=10000)
        for interval in TIMEFRAMES
    }
    datasets = {interval: candles for interval, candles in datasets.items() if len(candles) >= 30}
    analysis = analyze_market(symbol, datasets)
    if live_price:
        # Indicators use confirmed candles, while the displayed current price is
        # fetched at send time from Binance spot ticker data.
        analysis.current_price = await provider.fetch_price(symbol)
        analysis.generated_at = datetime.now(UTC).isoformat()
    return analysis


async def build_live_report(symbol: str, trigger: str | None = None) -> tuple[MarketAnalysis, str]:
    analysis = await analyze_symbol(symbol)
    return analysis, build_analysis_message(analysis, trigger=trigger)


async def send_live_report(symbol: str, trigger: str | None = None) -> MarketAnalysis:
    analysis, message = await build_live_report(symbol, trigger=trigger)
    await send_telegram(message)
    return analysis
