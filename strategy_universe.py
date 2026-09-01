from __future__ import annotations


# BTC is retained only as market context for the ETH/SOL cycle strategy.
# XRP remains analyzable for old local data, but is not part of live monitoring.
MARKET_CONTEXT_SYMBOLS = ("BTCUSDT",)
TRADE_SYMBOLS = ("ETHUSDT", "SOLUSDT")
ACTIVE_MONITOR_SYMBOLS = MARKET_CONTEXT_SYMBOLS + TRADE_SYMBOLS


def is_trade_symbol(symbol: str) -> bool:
    return symbol.upper() in TRADE_SYMBOLS


def is_market_context_symbol(symbol: str) -> bool:
    return symbol.upper() in MARKET_CONTEXT_SYMBOLS


def is_active_monitor_symbol(symbol: str) -> bool:
    return symbol.upper() in ACTIVE_MONITOR_SYMBOLS
