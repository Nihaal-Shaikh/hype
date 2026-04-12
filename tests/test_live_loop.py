"""Integration tests for run_live.py — Phase 5A-2 live trading loop."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from conftest import make_candles
from execution import ExecutionConfig
from live_state import SessionState
from run_live import _interval_to_seconds, _parse_args, _run_one_tick
from strategies.ema_crossover import EmaCrossover, EmaCrossoverConfig
from strategy import Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_strategy(fast: int = 5, slow: int = 20) -> EmaCrossover:
    return EmaCrossover(EmaCrossoverConfig(fast_period=fast, slow_period=slow))


def _make_state(coin: str = "BTC") -> SessionState:
    return SessionState(coin=coin)


def _make_logger() -> logging.Logger:
    return logging.getLogger("test_live_loop")


def _make_config() -> ExecutionConfig:
    return ExecutionConfig()


def _make_step_candles(direction: str = "up", flat_bars: int = 30) -> pd.DataFrame:
    """Candles: flat at 100 for flat_bars, then one bar that triggers a crossover.

    Uses the same construction as test_strategy.py so the EMA crossover fires
    reliably on the last bar. Timestamps are anchored to now so freshness
    checks pass for any reasonable interval.
    """
    n = flat_bars + 1
    # Anchor last bar at now so the freshness check always passes
    now = datetime.now(timezone.utc)
    step_price = 150.0 if direction == "up" else 50.0
    rows = []
    for i in range(n):
        c = 100.0 if i < flat_bars else step_price
        t = now - timedelta(hours=(n - 1 - i))
        rows.append({
            "time": t,
            "open": c,
            "high": c + 1,
            "low": c - 1,
            "close": c,
            "volume": 1000.0,
        })
    return pd.DataFrame(rows)


def _fresh_candles(n: int = 40) -> pd.DataFrame:
    """Return candles whose last timestamp is 'now' (fresh for any reasonable interval)."""
    candles = make_candles(trend="sideways", n=n)
    now = datetime.now(timezone.utc)
    # Shift times so last candle is at 'now'
    last = candles["time"].iloc[-1]
    if hasattr(last, "to_pydatetime"):
        last = last.to_pydatetime()
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    delta = now - last
    candles = candles.copy()
    candles["time"] = candles["time"].apply(
        lambda t: (t.to_pydatetime() if hasattr(t, "to_pydatetime") else t) + delta
    )
    return candles


# ---------------------------------------------------------------------------
# 1. test_parse_args_defaults
# ---------------------------------------------------------------------------

def test_parse_args_defaults() -> None:
    args = _parse_args([])
    assert args.live is False
    assert args.coin == "BTC"
    assert args.interval == "1h"
    assert args.fast == 9
    assert args.slow == 21
    assert args.interval_seconds == 3600
    assert args.lookback_hours == 48


# ---------------------------------------------------------------------------
# 2. test_parse_args_live_flag
# ---------------------------------------------------------------------------

def test_parse_args_live_flag() -> None:
    args = _parse_args(["--live"])
    assert args.live is True


# ---------------------------------------------------------------------------
# 3. test_interval_to_seconds
# ---------------------------------------------------------------------------

def test_interval_to_seconds() -> None:
    assert _interval_to_seconds("1h") == 3600
    assert _interval_to_seconds("4h") == 14400
    assert _interval_to_seconds("15m") == 900
    assert _interval_to_seconds("1d") == 86400
    assert _interval_to_seconds("30m") == 1800


# ---------------------------------------------------------------------------
# 4. test_tick_dry_run_buy_logs_no_order
# ---------------------------------------------------------------------------

def test_tick_dry_run_buy_logs_no_order(caplog) -> None:
    """Dry-run BUY: log contains 'DRY-RUN', no exchange call made."""
    candles = _make_step_candles("up", flat_bars=30)
    strategy = _make_strategy(fast=5, slow=20)
    state = _make_state()
    config = _make_config()
    mock_exchange = MagicMock()

    with patch("run_live.fetch_candles", return_value=candles), \
         caplog.at_level(logging.INFO):
        _run_one_tick(
            info=MagicMock(),
            exchange=None,  # dry-run: exchange is None
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=False,
            logger=_make_logger(),
        )

    mock_exchange.market_open.assert_not_called()
    assert any("DRY-RUN" in r.message for r in caplog.records)
    # State should now reflect the hypothetical buy
    assert state.is_holding is True


# ---------------------------------------------------------------------------
# 5. test_tick_dry_run_sell_logs_no_order
# ---------------------------------------------------------------------------

def test_tick_dry_run_sell_logs_no_order(caplog) -> None:
    """Dry-run SELL: log contains 'DRY-RUN', no exchange call made."""
    candles = _make_step_candles("down", flat_bars=30)
    strategy = _make_strategy(fast=5, slow=20)
    # Pre-populate a holding state so the SELL path fires
    state = _make_state()
    state.open_position("BTC", entry_price=100.0, size=0.1, notional=10.0)
    config = _make_config()
    mock_exchange = MagicMock()

    with patch("run_live.fetch_candles", return_value=candles), \
         caplog.at_level(logging.INFO):
        _run_one_tick(
            info=MagicMock(),
            exchange=None,
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=False,
            logger=_make_logger(),
        )

    mock_exchange.market_close.assert_not_called()
    assert any("DRY-RUN" in r.message for r in caplog.records)
    assert state.is_holding is False


# ---------------------------------------------------------------------------
# 6. test_tick_buy_while_holding_skipped
# ---------------------------------------------------------------------------

def test_tick_buy_while_holding_skipped(caplog) -> None:
    """BUY signal while already holding: tick skips the trade."""
    candles = _make_step_candles("up", flat_bars=30)
    strategy = _make_strategy(fast=5, slow=20)
    state = _make_state()
    state.open_position("BTC", entry_price=100.0, size=0.1, notional=10.0)
    assert state.is_holding is True
    config = _make_config()

    with patch("run_live.fetch_candles", return_value=candles), \
         caplog.at_level(logging.INFO):
        _run_one_tick(
            info=MagicMock(),
            exchange=None,
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=False,
            logger=_make_logger(),
        )

    # Still holding — no second open
    assert state.is_holding is True
    assert state.trade_count == 1  # only the initial open_position call
    assert any("skipping BUY" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 7. test_tick_sell_while_flat_skipped
# ---------------------------------------------------------------------------

def test_tick_sell_while_flat_skipped(caplog) -> None:
    """SELL signal while flat: tick skips the trade."""
    candles = _make_step_candles("down", flat_bars=30)
    strategy = _make_strategy(fast=5, slow=20)
    state = _make_state()  # flat — not holding
    config = _make_config()

    with patch("run_live.fetch_candles", return_value=candles), \
         caplog.at_level(logging.INFO):
        _run_one_tick(
            info=MagicMock(),
            exchange=None,
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=False,
            logger=_make_logger(),
        )

    assert state.is_holding is False
    assert any("skipping SELL" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 8. test_tick_stale_candles_skipped
# ---------------------------------------------------------------------------

def test_tick_stale_candles_skipped(caplog) -> None:
    """Stale candles: tick returns without trading."""
    # Build candles with a timestamp well in the past (> 1.5 * interval_seconds ago)
    old_candles = make_candles(trend="up", n=30)
    # Force last candle time to be very old
    old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
    old_candles = old_candles.copy()
    old_candles["time"] = old_candles["time"].apply(
        lambda _: old_time
    )

    strategy = _make_strategy(fast=5, slow=20)
    state = _make_state()
    config = _make_config()

    with patch("run_live.fetch_candles", return_value=old_candles), \
         caplog.at_level(logging.WARNING):
        _run_one_tick(
            info=MagicMock(),
            exchange=None,
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=False,
            logger=_make_logger(),
        )

    assert state.is_holding is False
    assert any("freshness" in r.message.lower() or "stale" in r.message.lower()
               for r in caplog.records)


# ---------------------------------------------------------------------------
# 9. test_tick_hold_signal_no_action
# ---------------------------------------------------------------------------

def test_tick_hold_signal_no_action(caplog) -> None:
    """HOLD signal: tick logs and takes no action."""
    # Flat candles → HOLD
    candles = _fresh_candles(n=50)
    # Make all closes identical so EMAs never cross
    candles = candles.copy()
    candles["close"] = 100.0

    strategy = _make_strategy(fast=5, slow=20)
    state = _make_state()
    config = _make_config()

    with patch("run_live.fetch_candles", return_value=candles), \
         caplog.at_level(logging.INFO):
        _run_one_tick(
            info=MagicMock(),
            exchange=None,
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=False,
            logger=_make_logger(),
        )

    assert state.is_holding is False
    assert any("HOLD" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 10. test_tick_duplicate_signal_skipped
# ---------------------------------------------------------------------------

def test_tick_duplicate_signal_skipped(caplog) -> None:
    """Same candle timestamp as last signal: tick skips."""
    candles = _make_step_candles("up", flat_bars=30)
    strategy = _make_strategy(fast=5, slow=20)
    state = _make_state()
    config = _make_config()

    # Set last_signal_candle_time to the last candle's time
    last_time = candles["time"].iloc[-1]
    if hasattr(last_time, "to_pydatetime"):
        last_time = last_time.to_pydatetime()
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=timezone.utc)
    state.last_signal_candle_time = last_time

    with patch("run_live.fetch_candles", return_value=candles), \
         caplog.at_level(logging.INFO):
        _run_one_tick(
            info=MagicMock(),
            exchange=None,
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=False,
            logger=_make_logger(),
        )

    assert state.is_holding is False
    assert any("Duplicate" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 11. test_tick_validation_failure_skipped
# ---------------------------------------------------------------------------

def test_tick_validation_failure_skipped(caplog) -> None:
    """validate_trade returns (False, reason): no order placed."""
    candles = _make_step_candles("up", flat_bars=30)
    strategy = _make_strategy(fast=5, slow=20)
    state = _make_state()
    config = _make_config()

    with patch("run_live.fetch_candles", return_value=candles), \
         patch("run_live.validate_trade", return_value=(False, "Mock validation failure")), \
         caplog.at_level(logging.INFO):
        _run_one_tick(
            info=MagicMock(),
            exchange=None,
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=False,
            logger=_make_logger(),
        )

    assert state.is_holding is False
    assert any("validation failed" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# 12. test_tick_api_error_caught
# ---------------------------------------------------------------------------

def test_tick_api_error_caught(caplog) -> None:
    """fetch_candles raises Exception: tick catches it and does not crash."""
    strategy = _make_strategy()
    state = _make_state()
    config = _make_config()

    with patch("run_live.fetch_candles", side_effect=Exception("Network error")), \
         caplog.at_level(logging.ERROR):
        # Should not raise
        _run_one_tick(
            info=MagicMock(),
            exchange=None,
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=False,
            logger=_make_logger(),
        )

    assert any("Unhandled exception" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 13. test_tick_insufficient_candles_skipped
# ---------------------------------------------------------------------------

def test_tick_insufficient_candles_skipped(caplog) -> None:
    """Fewer candles than slow_period: tick skips without trading."""
    # Only 5 candles — well below slow_period=20
    candles = _fresh_candles(n=5)
    strategy = _make_strategy(fast=5, slow=20)
    state = _make_state()
    config = _make_config()

    with patch("run_live.fetch_candles", return_value=candles), \
         caplog.at_level(logging.INFO):
        _run_one_tick(
            info=MagicMock(),
            exchange=None,
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=False,
            logger=_make_logger(),
        )

    assert state.is_holding is False
    assert any("Insufficient" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 14. test_tick_live_buy_calls_market_open
# ---------------------------------------------------------------------------

def test_tick_live_buy_calls_market_open() -> None:
    """Live BUY: exchange.market_open is called once."""
    candles = _make_step_candles("up", flat_bars=30)
    strategy = _make_strategy(fast=5, slow=20)
    state = _make_state()
    config = _make_config()

    mock_exchange = MagicMock()
    mock_exchange.market_open.return_value = {"status": "ok"}

    mock_info = MagicMock()
    mock_info.user_state.return_value = {
        "withdrawable": "50.0",
        "assetPositions": [],
    }
    mock_info.meta.return_value = {
        "universe": [{"name": "BTC", "maxLeverage": 3, "szDecimals": 5}]
    }
    mock_info.all_mids.return_value = {"BTC": "100.0"}

    with patch("run_live.fetch_candles", return_value=candles), \
         patch("run_live.load_main_address", return_value="0xABCD"):
        _run_one_tick(
            info=mock_info,
            exchange=mock_exchange,
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=True,
            logger=_make_logger(),
        )

    mock_exchange.market_open.assert_called_once()
    call_kwargs = mock_exchange.market_open.call_args
    assert call_kwargs is not None
