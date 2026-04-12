"""Tests for live_state.py — Phase 5A-1 live state tracker."""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

import pandas as pd
import pytest

from execution import ExecutionConfig
from live_state import (
    LivePosition,
    SessionState,
    check_candle_freshness,
    compute_order_params,
    is_duplicate_signal,
)
from strategy import Signal

from conftest import make_candles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(coin: str = "BTC") -> SessionState:
    return SessionState(coin=coin)


def _open_session(coin: str = "BTC", entry: float = 100.0, size: float = 0.1, notional: float = 10.0) -> SessionState:
    s = _make_session(coin)
    s.open_position(coin, entry, size, notional)
    return s


# ---------------------------------------------------------------------------
# LivePosition
# ---------------------------------------------------------------------------

def test_live_position_frozen() -> None:
    pos = LivePosition(
        coin="BTC",
        entry_price=100.0,
        size=0.1,
        notional=10.0,
        opened_at=datetime.now(timezone.utc),
    )
    with pytest.raises((AttributeError, TypeError)):
        pos.coin = "ETH"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SessionState — initial state
# ---------------------------------------------------------------------------

def test_session_state_initial() -> None:
    s = _make_session()
    assert s.is_holding is False
    assert s.position is None
    assert s.trades == []
    assert s.session_pnl == 0.0
    assert s.trade_count == 0
    assert s.leverage_set is False
    assert s.last_signal_candle_time is None
    assert s.started_at is not None


# ---------------------------------------------------------------------------
# open_position
# ---------------------------------------------------------------------------

def test_open_position() -> None:
    s = _make_session()
    s.open_position("BTC", entry_price=50_000.0, size=0.0002, notional=10.0)
    assert s.is_holding is True
    assert s.position is not None
    assert s.position.coin == "BTC"
    assert s.position.entry_price == 50_000.0
    assert s.position.size == 0.0002
    assert s.position.notional == 10.0
    assert s.trade_count == 1


def test_open_position_records_trade() -> None:
    s = _make_session()
    s.open_position("BTC", entry_price=50_000.0, size=0.0002, notional=10.0)
    assert len(s.trades) == 1
    trade = s.trades[0]
    assert trade["side"] == "BUY"
    assert trade["price"] == 50_000.0
    assert trade["size"] == 0.0002
    assert trade["notional"] == 10.0
    assert "timestamp" in trade


# ---------------------------------------------------------------------------
# close_position
# ---------------------------------------------------------------------------

def test_close_position_returns_pnl() -> None:
    s = _open_session(entry=100.0, notional=10.0)
    pnl = s.close_position(exit_price=110.0, proceeds=11.0)
    assert pnl == pytest.approx(1.0)


def test_close_position_negative_pnl() -> None:
    s = _open_session(entry=100.0, notional=10.0)
    pnl = s.close_position(exit_price=90.0, proceeds=9.0)
    assert pnl == pytest.approx(-1.0)


def test_close_position_updates_session_pnl() -> None:
    s = _open_session(entry=100.0, notional=10.0)
    s.close_position(exit_price=110.0, proceeds=11.0)
    # Open and close again
    s.open_position("BTC", entry_price=110.0, size=0.1, notional=11.0)
    s.close_position(exit_price=121.0, proceeds=12.1)
    assert s.session_pnl == pytest.approx(2.1)


def test_close_position_transitions_to_flat() -> None:
    s = _open_session()
    s.close_position(exit_price=105.0, proceeds=10.5)
    assert s.is_holding is False
    assert s.position is None


def test_close_position_while_flat() -> None:
    s = _make_session()
    result = s.close_position(exit_price=105.0, proceeds=10.5)
    assert result == 0.0
    # No crash, state unchanged
    assert s.is_holding is False
    assert s.position is None


# ---------------------------------------------------------------------------
# sync_from_exchange
# ---------------------------------------------------------------------------

def _make_user_state(coin: str, szi: str, entry_px: str) -> dict:
    """Build a minimal user_state dict with one open position."""
    return {
        "assetPositions": [
            {"position": {"coin": coin, "szi": szi, "entryPx": entry_px}}
        ]
    }


def _make_flat_user_state() -> dict:
    return {"assetPositions": []}


def test_sync_opened_externally() -> None:
    s = _make_session(coin="BTC")
    user_state = _make_user_state("BTC", szi="0.0002", entry_px="50000")
    result = s.sync_from_exchange(user_state, "BTC")
    assert result == "opened_externally"
    assert s.is_holding is True
    assert s.position is not None
    assert s.position.entry_price == 50_000.0
    assert s.position.size == pytest.approx(0.0002)


def test_sync_closed_externally() -> None:
    s = _open_session(coin="BTC")
    user_state = _make_flat_user_state()
    result = s.sync_from_exchange(user_state, "BTC")
    assert result == "closed_externally"
    assert s.is_holding is False
    assert s.position is None


def test_sync_both_holding() -> None:
    s = _open_session(coin="BTC", entry=50_000.0, size=0.0002, notional=10.0)
    # Exchange reports slightly different entry (e.g., after partial fill)
    user_state = _make_user_state("BTC", szi="0.0002", entry_px="50100")
    result = s.sync_from_exchange(user_state, "BTC")
    assert result == "synced"
    assert s.is_holding is True
    assert s.position is not None
    assert s.position.entry_price == pytest.approx(50_100.0)


def test_sync_both_flat() -> None:
    s = _make_session(coin="BTC")
    user_state = _make_flat_user_state()
    result = s.sync_from_exchange(user_state, "BTC")
    assert result == "no_change"
    assert s.is_holding is False
    assert s.position is None


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def test_summary_output() -> None:
    s = _open_session(coin="BTC", entry=50_000.0, size=0.0002, notional=10.0)
    s.session_pnl = 1.23
    text = s.summary()
    assert "Trades" in text
    assert "PnL" in text
    assert "HOLDING" in text
    assert "BTC" in text
    assert "50,000.00" in text


def test_summary_flat() -> None:
    s = _make_session()
    text = s.summary()
    assert "FLAT" in text
    assert "Trades: 0" in text


# ---------------------------------------------------------------------------
# check_candle_freshness
# ---------------------------------------------------------------------------

def _make_candles_at_time(last_time: datetime, n: int = 5) -> pd.DataFrame:
    """Build a minimal candles DataFrame whose last row has the given time."""
    times = [last_time - timedelta(hours=i) for i in range(n - 1, -1, -1)]
    rows = [
        {"time": t, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0}
        for t in times
    ]
    return pd.DataFrame(rows)


def test_candle_freshness_fresh() -> None:
    # Last candle 100 seconds ago; interval=3600s → max_age=5400s
    last_time = datetime.now(timezone.utc) - timedelta(seconds=100)
    candles = _make_candles_at_time(last_time)
    is_fresh, reason = check_candle_freshness(candles, interval_seconds=3600)
    assert is_fresh is True
    assert "Fresh" in reason


def test_candle_freshness_stale() -> None:
    # Last candle 6000 seconds ago; interval=3600s → max_age=5400s
    last_time = datetime.now(timezone.utc) - timedelta(seconds=6000)
    candles = _make_candles_at_time(last_time)
    is_fresh, reason = check_candle_freshness(candles, interval_seconds=3600)
    assert is_fresh is False
    assert "stale" in reason


def test_candle_freshness_empty() -> None:
    candles = pd.DataFrame()
    is_fresh, reason = check_candle_freshness(candles, interval_seconds=3600)
    assert is_fresh is False
    assert "No candle data" in reason


# ---------------------------------------------------------------------------
# compute_order_params
# ---------------------------------------------------------------------------

def test_compute_order_params_caps_notional() -> None:
    # capital=1000, 25%*3 = $750 but capped at max_notional_usd=$15
    config = ExecutionConfig(max_leverage=3, max_notional_usd=15.0, min_notional_usd=10.0)
    notional, size = compute_order_params(
        capital=1000.0, mid_price=100.0, sz_decimals=2, config=config
    )
    assert notional == 15.0


def test_compute_order_params_floors_notional() -> None:
    # capital=5, 25%*3 = $3.75 but floored at min_notional_usd=$10
    config = ExecutionConfig(max_leverage=3, max_notional_usd=15.0, min_notional_usd=10.0)
    notional, size = compute_order_params(
        capital=5.0, mid_price=100.0, sz_decimals=2, config=config
    )
    assert notional == 10.0


def test_compute_order_params_rounds_size() -> None:
    # notional=$10, mid=$3, raw_size=3.333..., sz_decimals=2 → floor to 3.33
    config = ExecutionConfig(max_leverage=3, max_notional_usd=15.0, min_notional_usd=10.0)
    notional, size = compute_order_params(
        capital=5.0, mid_price=3.0, sz_decimals=2, config=config
    )
    expected_size = math.floor((10.0 / 3.0) * 100) / 100
    assert size == pytest.approx(expected_size)
    # Verify it's rounded DOWN (not rounded to nearest)
    assert size <= 10.0 / 3.0


def test_compute_order_params_normal() -> None:
    # capital=20, 25%*3=$15, not capped, mid=$50_000, sz_decimals=5
    config = ExecutionConfig(max_leverage=3, max_notional_usd=15.0, min_notional_usd=10.0)
    notional, size = compute_order_params(
        capital=20.0, mid_price=50_000.0, sz_decimals=5, config=config
    )
    assert notional == 15.0
    assert size == pytest.approx(0.0003, abs=1e-5)


# ---------------------------------------------------------------------------
# is_duplicate_signal
# ---------------------------------------------------------------------------

def test_is_duplicate_signal_same_candle() -> None:
    t = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert is_duplicate_signal(Signal.BUY, t, last_signal_candle_time=t) is True


def test_is_duplicate_signal_different_candle() -> None:
    t1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    assert is_duplicate_signal(Signal.BUY, t2, last_signal_candle_time=t1) is False


def test_is_duplicate_signal_hold_never_duplicate() -> None:
    t = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    # HOLD on same candle time must still return False
    assert is_duplicate_signal(Signal.HOLD, t, last_signal_candle_time=t) is False


def test_is_duplicate_signal_no_prior() -> None:
    t = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert is_duplicate_signal(Signal.BUY, t, last_signal_candle_time=None) is False


def test_is_duplicate_signal_sell_same_candle() -> None:
    t = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert is_duplicate_signal(Signal.SELL, t, last_signal_candle_time=t) is True
