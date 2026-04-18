"""SQLite history log — persistent tick + trade records for real equity curves.

Schema v1:
  ticks(id, ts, mode, strategy, session_pnl, is_holding, trade_count, universe_size)
  trades(ts, side, coin, price, size, notional, pnl)  -- PRIMARY KEY (ts, side, coin)

WAL journal mode so the dashboard can read concurrently with the scanner
writer.

No migrations: if the schema ever changes, delete the DB and restart.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DEFAULT_DB_PATH = Path(".hype.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    mode            TEXT    NOT NULL,
    strategy        TEXT    NOT NULL,
    session_pnl     REAL    NOT NULL,
    is_holding      INTEGER NOT NULL,
    trade_count     INTEGER NOT NULL,
    universe_size   INTEGER NOT NULL,
    position_coin   TEXT
);

CREATE INDEX IF NOT EXISTS idx_ticks_ts ON ticks(ts);

CREATE TABLE IF NOT EXISTS trades (
    ts              TEXT    NOT NULL,
    side            TEXT    NOT NULL,
    coin            TEXT    NOT NULL,
    price           REAL    NOT NULL,
    size            REAL    NOT NULL,
    notional        REAL    NOT NULL,
    pnl             REAL,
    PRIMARY KEY (ts, side, coin)
);

CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);
"""


@contextmanager
def connect(path: Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context-managed connection with WAL mode enabled."""
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db(path: Path = DEFAULT_DB_PATH) -> None:
    """Create tables if not exist. Idempotent."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(_SCHEMA)


def log_tick(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
    """Insert one row into ticks from a scanner_io.build_state snapshot."""
    session = snapshot.get("session", {}) or {}
    position = session.get("position")
    conn.execute(
        "INSERT INTO ticks "
        "(ts, mode, strategy, session_pnl, is_holding, trade_count, "
        " universe_size, position_coin) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot.get("tick_at", datetime.now(timezone.utc).isoformat()),
            snapshot.get("mode", "?"),
            snapshot.get("strategy", "?"),
            float(session.get("session_pnl", 0.0)),
            1 if session.get("is_holding") else 0,
            int(session.get("trade_count", 0)),
            int(snapshot.get("universe_size", 0)),
            position.get("coin") if position else None,
        ),
    )


def sync_trades(conn: sqlite3.Connection, trades: list[dict[str, Any]]) -> int:
    """Upsert all trades idempotently. Returns count of NEW rows inserted."""
    before = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    for t in trades:
        conn.execute(
            "INSERT OR IGNORE INTO trades "
            "(ts, side, coin, price, size, notional, pnl) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                t.get("timestamp") or t.get("ts"),
                t.get("side"),
                t.get("coin", "?"),
                float(t.get("price", 0.0)),
                float(t.get("size", 0.0)),
                float(t.get("notional", 0.0)),
                float(t["pnl"]) if t.get("pnl") is not None else None,
            ),
        )
    after = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    return after - before


def read_ticks(conn: sqlite3.Connection, limit: int | None = None) -> list[dict[str, Any]]:
    """Return tick rows as list of dicts, oldest first."""
    q = "SELECT ts, mode, strategy, session_pnl, is_holding, trade_count, " \
        "universe_size, position_coin FROM ticks ORDER BY ts ASC"
    if limit is not None:
        q += f" LIMIT {int(limit)}"
    cursor = conn.execute(q)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def read_trades(conn: sqlite3.Connection, limit: int | None = None) -> list[dict[str, Any]]:
    """Return trade rows as list of dicts, oldest first."""
    q = "SELECT ts, side, coin, price, size, notional, pnl FROM trades ORDER BY ts ASC"
    if limit is not None:
        q += f" LIMIT {int(limit)}"
    cursor = conn.execute(q)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def tick_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]


def trade_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
