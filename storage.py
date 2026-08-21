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
