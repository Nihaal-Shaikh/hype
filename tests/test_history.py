"""Tests for history.py — Phase 5G."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import history
from live_state import SessionState
from scanner import ScanResult
from scanner_io import build_state


def _fake_snapshot(pnl: float = 0.0, holding: bool = False,
                   trade_count: int = 0, mode: str = "dry-run") -> dict:
    state = SessionState(coin="")
    if holding:
        state.open_position("BTC", 100.0, 0.1, 10.0)
    state.session_pnl = pnl
    state.trade_count = trade_count
    return build_state(mode, "ema(9/21)", state, [], universe_size=4)


# --- init_db ------------------------------------------------------------

def test_init_db_creates_tables(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    assert db.exists()
    with history.connect(db) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    names = {t[0] for t in tables}
    assert "ticks" in names
    assert "trades" in names


def test_init_db_is_idempotent(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    history.init_db(db)  # second call must not fail
    with history.connect(db) as conn:
        assert history.tick_count(conn) == 0


# --- log_tick -----------------------------------------------------------

def test_log_tick_inserts_row(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    snap = _fake_snapshot(pnl=1.23)

    with history.connect(db) as conn:
        history.log_tick(conn, snap)
        assert history.tick_count(conn) == 1
        rows = history.read_ticks(conn)

    assert rows[0]["session_pnl"] == pytest.approx(1.23)
    assert rows[0]["mode"] == "dry-run"
    assert rows[0]["strategy"] == "ema(9/21)"
    assert rows[0]["is_holding"] == 0
    assert rows[0]["position_coin"] is None


def test_log_tick_with_position(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    snap = _fake_snapshot(holding=True, trade_count=1)

    with history.connect(db) as conn:
        history.log_tick(conn, snap)
        rows = history.read_ticks(conn)

    assert rows[0]["is_holding"] == 1
    assert rows[0]["position_coin"] == "BTC"
    assert rows[0]["trade_count"] == 1


def test_log_multiple_ticks_ordered(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)

    with history.connect(db) as conn:
        for i in range(5):
            snap = _fake_snapshot(pnl=float(i))
            history.log_tick(conn, snap)
        rows = history.read_ticks(conn)

    assert len(rows) == 5
    pnls = [r["session_pnl"] for r in rows]
    assert pnls == [0.0, 1.0, 2.0, 3.0, 4.0]


# --- sync_trades --------------------------------------------------------

def _trade(ts: str, side: str = "BUY", coin: str = "BTC",
           price: float = 100.0, size: float = 0.1,
           notional: float = 10.0, pnl: float | None = None) -> dict:
    return {
        "timestamp": ts, "side": side, "coin": coin,
        "price": price, "size": size, "notional": notional, "pnl": pnl,
    }


def test_sync_trades_inserts_new(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    trades = [
        _trade("2026-04-18T10:00:00Z", "BUY", "BTC"),
        _trade("2026-04-18T11:00:00Z", "SELL", "BTC", pnl=1.5),
    ]

    with history.connect(db) as conn:
        inserted = history.sync_trades(conn, trades)
        assert inserted == 2
        assert history.trade_count(conn) == 2


def test_sync_trades_idempotent(tmp_path: Path):
    """Second sync of same trades must not duplicate."""
    db = tmp_path / "x.db"
    history.init_db(db)
    trades = [_trade("2026-04-18T10:00:00Z", "BUY", "BTC")]

    with history.connect(db) as conn:
        history.sync_trades(conn, trades)
        second = history.sync_trades(conn, trades)
        assert second == 0
        assert history.trade_count(conn) == 1


def test_sync_trades_appends_new_only(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)

    first = [_trade("2026-04-18T10:00:00Z", "BUY", "BTC")]
    second = first + [_trade("2026-04-18T11:00:00Z", "SELL", "BTC", pnl=2.0)]

    with history.connect(db) as conn:
        history.sync_trades(conn, first)
        new = history.sync_trades(conn, second)
        assert new == 1
        assert history.trade_count(conn) == 2


def test_sync_trades_distinguishes_coins(tmp_path: Path):
    """Same timestamp + side on different coins must all be stored."""
    db = tmp_path / "x.db"
    history.init_db(db)
    ts = "2026-04-18T10:00:00Z"
    trades = [
        _trade(ts, "BUY", "BTC"),
        _trade(ts, "BUY", "ETH"),
    ]
    with history.connect(db) as conn:
        inserted = history.sync_trades(conn, trades)
        assert inserted == 2


# --- WAL mode ------------------------------------------------------------

def test_wal_mode_enabled(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    with history.connect(db) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
