from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from market_data import Candle


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = PROJECT_DIR / "data" / "chart_teacher.db"


def _signal_values(payload: dict) -> tuple[str, str]:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return fingerprint, canonical


class ChartTeacherStore:
    def __init__(self, path: Path | str = DEFAULT_DATABASE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.executescript(
                    """
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    close_time INTEGER NOT NULL,
                    taker_buy_volume REAL,
                    PRIMARY KEY (symbol, interval, open_time)
                );
                CREATE INDEX IF NOT EXISTS idx_candles_lookup
                    ON candles(symbol, interval, open_time DESC);

                CREATE TABLE IF NOT EXISTS sent_signals (
                    fingerprint TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signal_cooldowns (
                    cooldown_key TEXT PRIMARY KEY,
                    claimed_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dominance_snapshots (
                    observed_at INTEGER PRIMARY KEY,
                    usdt_dominance REAL NOT NULL,
                    usdt_market_cap_usd REAL NOT NULL,
                    total_market_cap_usd REAL NOT NULL,
                    dominance_change_24h_pp REAL,
                    total_market_change_24h REAL,
                    volume_change_24h REAL
                );
                CREATE INDEX IF NOT EXISTS idx_dominance_observed_at
                    ON dominance_snapshots(observed_at DESC);

                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    invested_krw INTEGER NOT NULL,
                    average_entry_price REAL NOT NULL,
                    stage INTEGER NOT NULL,
                    invalidation_price REAL,
                    status TEXT NOT NULL,
                    opened_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS position_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    amount_krw INTEGER NOT NULL,
                    price REAL NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_position_trades_symbol
                    ON position_trades(symbol, created_at DESC);
                CREATE TABLE IF NOT EXISTS bot_state (
                    state_key TEXT PRIMARY KEY,
                    state_value TEXT NOT NULL
                );
                    """
                )
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(candles)").fetchall()
                }
                if "taker_buy_volume" not in columns:
                    connection.execute(
                        "ALTER TABLE candles ADD COLUMN taker_buy_volume REAL"
                    )

    def upsert_candles(self, symbol: str, interval: str, candles: list[Candle]) -> int:
        if not candles:
            return 0
        rows = [
            (
                symbol.upper(),
                interval,
                candle.open_time,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
                candle.close_time,
                candle.taker_buy_volume,
            )
            for candle in candles
        ]
        with self._lock, closing(self._connect()) as connection:
            with connection:
                connection.executemany(
                    """
                INSERT INTO candles
                    (symbol, interval, open_time, open, high, low, close, volume, close_time, taker_buy_volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    close_time=excluded.close_time,
                    taker_buy_volume=COALESCE(excluded.taker_buy_volume, candles.taker_buy_volume)
                    """,
                    rows,
                )
        return len(rows)

    def load_candles(self, symbol: str, interval: str, limit: int = 5000) -> list[Candle]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT open_time, open, high, low, close, volume, close_time, taker_buy_volume
                FROM candles
                WHERE symbol=? AND interval=?
                ORDER BY open_time DESC
                LIMIT ?
                """,
                (symbol.upper(), interval, int(limit)),
            ).fetchall()
        return [Candle(**dict(row)) for row in reversed(rows)]

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT symbol || ':' || interval AS key, COUNT(*) AS count
                   FROM candles GROUP BY symbol, interval ORDER BY symbol, interval"""
            ).fetchall()
        return {row["key"]: int(row["count"]) for row in rows}

    def record_dominance_snapshot(
        self,
        *,
        observed_at: int,
        usdt_dominance: float,
        usdt_market_cap_usd: float,
        total_market_cap_usd: float,
        dominance_change_24h_pp: float | None,
        total_market_change_24h: float | None,
        volume_change_24h: float | None,
    ) -> None:
        with self._lock, closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO dominance_snapshots
                        (observed_at, usdt_dominance, usdt_market_cap_usd,
                         total_market_cap_usd, dominance_change_24h_pp,
                         total_market_change_24h, volume_change_24h)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(observed_at) DO UPDATE SET
                        usdt_dominance=excluded.usdt_dominance,
                        usdt_market_cap_usd=excluded.usdt_market_cap_usd,
                        total_market_cap_usd=excluded.total_market_cap_usd,
                        dominance_change_24h_pp=excluded.dominance_change_24h_pp,
                        total_market_change_24h=excluded.total_market_change_24h,
                        volume_change_24h=excluded.volume_change_24h
                    """,
                    (
                        int(observed_at),
                        float(usdt_dominance),
                        float(usdt_market_cap_usd),
                        float(total_market_cap_usd),
                        dominance_change_24h_pp,
                        total_market_change_24h,
                        volume_change_24h,
                    ),
                )

    def load_dominance_before(self, observed_at: int) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT observed_at, usdt_dominance, usdt_market_cap_usd,
                       total_market_cap_usd, dominance_change_24h_pp,
                       total_market_change_24h, volume_change_24h
                FROM dominance_snapshots
                WHERE observed_at <= ?
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (int(observed_at),),
            ).fetchone()
        return dict(row) if row is not None else None

    def claim_signal(self, symbol: str, payload: dict, now_ms: int) -> bool:
        fingerprint, canonical = _signal_values(payload)
        with self._lock, closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO sent_signals(fingerprint, symbol, created_at, payload) VALUES (?, ?, ?, ?)",
                    (fingerprint, symbol.upper(), int(now_ms), canonical),
                )
        return cursor.rowcount == 1

    def claim_cooldown(self, key: str, now_ms: int, cooldown_ms: int) -> bool:
        key = str(key).strip()
        if not key:
            raise ValueError("Cooldown key is required")
        now_ms = int(now_ms)
        cooldown_ms = max(0, int(cooldown_ms))
        with self._lock, closing(self._connect()) as connection:
            with connection:
                row = connection.execute(
                    "SELECT claimed_at FROM signal_cooldowns WHERE cooldown_key=?",
                    (key,),
                ).fetchone()
                if row is not None and now_ms - int(row["claimed_at"]) < cooldown_ms:
                    return False
                connection.execute(
                    """
                    INSERT INTO signal_cooldowns(cooldown_key, claimed_at)
                    VALUES (?, ?)
                    ON CONFLICT(cooldown_key) DO UPDATE SET claimed_at=excluded.claimed_at
                    """,
                    (key, now_ms),
                )
        return True

    def release_cooldown(self, key: str, claimed_at: int) -> None:
        with self._lock, closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM signal_cooldowns WHERE cooldown_key=? AND claimed_at=?",
                    (str(key), int(claimed_at)),
                )

    def release_signal(self, payload: dict) -> None:
        fingerprint, _ = _signal_values(payload)
        with self._lock, closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM sent_signals WHERE fingerprint=?",
                    (fingerprint,),
                )

    def record_buy(
        self,
        symbol: str,
        *,
        amount_krw: int,
        price: float,
        now_ms: int,
        invalidation_price: float | None = None,
    ) -> dict:
        symbol = symbol.upper()
        amount_krw = int(amount_krw)
        price = float(price)
        if amount_krw <= 0 or price <= 0:
            raise ValueError("Buy amount and price must be positive")
        with self._lock, closing(self._connect()) as connection:
            with connection:
                current = connection.execute(
                    "SELECT * FROM positions WHERE symbol=? AND status='open'",
                    (symbol,),
                ).fetchone()
                if current is None:
                    invested = amount_krw
                    average = price
                    stage = 1
                    opened_at = int(now_ms)
                    invalidation = invalidation_price
                else:
                    previous_invested = int(current["invested_krw"])
                    invested = previous_invested + amount_krw
                    average = (
                        float(current["average_entry_price"]) * previous_invested
                        + price * amount_krw
                    ) / invested
                    stage = min(3, int(current["stage"]) + 1)
                    opened_at = int(current["opened_at"])
                    invalidation = (
                        current["invalidation_price"]
                        if current["invalidation_price"] is not None
                        else invalidation_price
                    )
                connection.execute(
                    """
                    INSERT INTO positions
                        (symbol, invested_krw, average_entry_price, stage,
                         invalidation_price, status, opened_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        invested_krw=excluded.invested_krw,
                        average_entry_price=excluded.average_entry_price,
                        stage=excluded.stage,
                        invalidation_price=excluded.invalidation_price,
                        status='open',
                        opened_at=excluded.opened_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        symbol,
                        invested,
                        average,
                        stage,
                        invalidation,
                        opened_at,
                        int(now_ms),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO position_trades
                        (symbol, side, amount_krw, price, created_at)
                    VALUES (?, 'buy', ?, ?, ?)
                    """,
                    (symbol, amount_krw, price, int(now_ms)),
                )
                row = connection.execute(
                    "SELECT * FROM positions WHERE symbol=?",
                    (symbol,),
                ).fetchone()
        return dict(row)

    def load_open_position(self, symbol: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM positions WHERE symbol=? AND status='open'",
                (symbol.upper(),),
            ).fetchone()
        return dict(row) if row is not None else None

    def load_open_positions(self) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM positions WHERE status='open' ORDER BY symbol"
            ).fetchall()
        return [dict(row) for row in rows]

    def close_position(self, symbol: str, *, price: float, now_ms: int) -> dict | None:
        symbol = symbol.upper()
        price = float(price)
        if price <= 0:
            raise ValueError("Close price must be positive")
        with self._lock, closing(self._connect()) as connection:
            with connection:
                current = connection.execute(
                    "SELECT * FROM positions WHERE symbol=? AND status='open'",
                    (symbol,),
                ).fetchone()
                if current is None:
                    return None
                connection.execute(
                    "UPDATE positions SET status='closed', updated_at=? WHERE symbol=?",
                    (int(now_ms), symbol),
                )
                connection.execute(
                    """
                    INSERT INTO position_trades
                        (symbol, side, amount_krw, price, created_at)
                    VALUES (?, 'close', ?, ?, ?)
                    """,
                    (symbol, int(current["invested_krw"]), price, int(now_ms)),
                )
        result = dict(current)
        result["close_price"] = price
        return result

    def undo_last_buy(self, symbol: str, *, now_ms: int) -> dict | None:
        symbol = symbol.upper()
        with self._lock, closing(self._connect()) as connection:
            with connection:
                current = connection.execute(
                    "SELECT * FROM positions WHERE symbol=? AND status='open'",
                    (symbol,),
                ).fetchone()
                if current is None:
                    return None
                trade = connection.execute(
                    """
                    SELECT * FROM position_trades
                    WHERE symbol=? AND side='buy' AND created_at>=?
                    ORDER BY created_at DESC, id DESC LIMIT 1
                    """,
                    (symbol, int(current["opened_at"])),
                ).fetchone()
                if trade is None:
                    return None
                connection.execute("DELETE FROM position_trades WHERE id=?", (trade["id"],))
                remaining = connection.execute(
                    """
                    SELECT amount_krw, price FROM position_trades
                    WHERE symbol=? AND side='buy' AND created_at>=?
                    ORDER BY created_at, id
                    """,
                    (symbol, int(current["opened_at"])),
                ).fetchall()
                if not remaining:
                    connection.execute(
                        "UPDATE positions SET status='closed', updated_at=? WHERE symbol=?",
                        (int(now_ms), symbol),
                    )
                    position = None
                else:
                    invested = sum(int(row["amount_krw"]) for row in remaining)
                    average = sum(
                        int(row["amount_krw"]) * float(row["price"])
                        for row in remaining
                    ) / invested
                    stage = min(3, len(remaining))
                    connection.execute(
                        """
                        UPDATE positions
                        SET invested_krw=?, average_entry_price=?, stage=?, updated_at=?
                        WHERE symbol=?
                        """,
                        (invested, average, stage, int(now_ms), symbol),
                    )
                    row = connection.execute(
                        "SELECT * FROM positions WHERE symbol=?",
                        (symbol,),
                    ).fetchone()
                    position = dict(row)
        return {"undone": dict(trade), "position": position}

    def get_state(self, key: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT state_value FROM bot_state WHERE state_key=?",
                (str(key),),
            ).fetchone()
        return str(row["state_value"]) if row is not None else None

    def set_state(self, key: str, value: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO bot_state(state_key, state_value) VALUES (?, ?)
                    ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value
                    """,
                    (str(key), str(value)),
                )
