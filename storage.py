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
                    """
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
            )
            for candle in candles
        ]
        with self._lock, closing(self._connect()) as connection:
            with connection:
                connection.executemany(
                    """
                INSERT INTO candles
                    (symbol, interval, open_time, open, high, low, close, volume, close_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    close_time=excluded.close_time
                    """,
                    rows,
                )
        return len(rows)

    def load_candles(self, symbol: str, interval: str, limit: int = 5000) -> list[Candle]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT open_time, open, high, low, close, volume, close_time
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

    def claim_signal(self, symbol: str, payload: dict, now_ms: int) -> bool:
        fingerprint, canonical = _signal_values(payload)
        with self._lock, closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO sent_signals(fingerprint, symbol, created_at, payload) VALUES (?, ?, ?, ?)",
                    (fingerprint, symbol.upper(), int(now_ms), canonical),
                )
        return cursor.rowcount == 1

    def release_signal(self, payload: dict) -> None:
        fingerprint, _ = _signal_values(payload)
        with self._lock, closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM sent_signals WHERE fingerprint=?",
                    (fingerprint,),
                )
