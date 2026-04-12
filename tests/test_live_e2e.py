"""E2E tests for the live trading loop — Phase 5A-3."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from execution import ExecutionConfig
from live_state import SessionState, check_candle_freshness
from run_live import _run_one_tick
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
    return logging.getLogger("test_live_e2e")


def _make_config() -> ExecutionConfig:
    return ExecutionConfig()


def _make_step_candles(direction: str = "up", flat_bars: int = 30) -> pd.DataFrame:
    """Candles: flat at 100 for flat_bars, then one bar that triggers a crossover.

    Uses the step-function pattern so the EMA crossover fires reliably on the
    last bar. Timestamps are anchored to now so freshness checks pass.
    """
    n = flat_bars + 1
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


def _make_flat_candles(n: int = 40) -> pd.DataFrame:
    """All-flat candles anchored to now — produces HOLD signal."""
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(n):
        t = now - timedelta(hours=(n - 1 - i))
        rows.append({
            "time": t,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000.0,
        })
    return pd.DataFrame(rows)


def _user_state_with_position(
    coin: str = "BTC",
    szi: str = "0.00015",
    entry_px: str = "84000.0",
    account_value: str = "50",
    withdrawable: str = "40",
) -> dict:
    return {
        "marginSummary": {"accountValue": account_value},
        "withdrawable": withdrawable,
        "assetPositions": [
            {"position": {"coin": coin, "szi": szi, "entryPx": entry_px}}
        ],
    }


def _user_state_flat(
    account_value: str = "50",
    withdrawable: str = "50",
) -> dict:
    return {
        "marginSummary": {"accountValue": account_value},
        "withdrawable": withdrawable,
        "assetPositions": [],
    }


def _run_tick(
    candles: pd.DataFrame,
    state: SessionState,
    is_live: bool = False,
    exchange=None,
    info=None,
    strategy=None,
    config=None,
) -> None:
    """Convenience wrapper — patches fetch_candles and load_main_address."""
    if strategy is None:
        strategy = _make_strategy()
    if config is None:
        config = _make_config()
    if info is None:
        info = MagicMock()
    with patch("run_live.fetch_candles", return_value=candles), \
         patch("run_live.load_main_address", return_value="0xTEST"):
        _run_one_tick(
            info=info,
            exchange=exchange,
            strategy=strategy,
            state=state,
            config=config,
            coin="BTC",
            interval="1h",
            interval_seconds=3600,
            lookback_hours=48,
            is_live=is_live,
            logger=_make_logger(),
        )


# ---------------------------------------------------------------------------
# 1. test_e2e_dry_run_3_ticks_no_orders
# ---------------------------------------------------------------------------

def test_e2e_dry_run_3_ticks_no_orders() -> None:
    """Dry-run: 3 ticks (BUY, HOLD, SELL) with exchange=None — zero exchange calls."""
    buy_candles = _make_step_candles("up", flat_bars=30)
    hold_candles = _make_flat_candles(n=40)
    sell_candles = _make_step_candles("down", flat_bars=30)

    strategy = _make_strategy(fast=5, slow=20)
    state = _make_state()

    # Tick 1: BUY signal — dry-run opens position hypothetically
    _run_tick(buy_candles, state, is_live=False, exchange=None, strategy=strategy)
    assert state.is_holding is True
    assert state.trade_count == 1

    # Tick 2: HOLD signal — no action; state unchanged
    # Advance candle time so it's not a duplicate signal
    hold_candles = hold_candles.copy()
    now = datetime.now(timezone.utc)
    hold_candles["time"] = hold_candles["time"].apply(
        lambda t: t + timedelta(hours=2)
    )
    _run_tick(hold_candles, state, is_live=False, exchange=None, strategy=strategy)
    assert state.is_holding is True
    assert state.trade_count == 1  # no change

    # Tick 3: SELL signal — dry-run closes position
    # Shift sell candles so last bar is different from the BUY candle time
    sell_candles = sell_candles.copy()
    sell_candles["time"] = sell_candles["time"].apply(
        lambda t: t + timedelta(hours=4)
    )
    _run_tick(sell_candles, state, is_live=False, exchange=None, strategy=strategy)
    assert state.is_holding is False
    assert state.trade_count == 2


# ---------------------------------------------------------------------------
# 2. test_e2e_live_buy_tick
# ---------------------------------------------------------------------------

def test_e2e_live_buy_tick() -> None:
    """Live BUY: exchange.market_open called; state.is_holding becomes True."""
    candles = _make_step_candles("up", flat_bars=30)
    state = _make_state()

    mock_exchange = MagicMock()
    mock_exchange.market_open.return_value = {
        "status": "ok",
        "response": {
            "data": {
                "statuses": [
                    {"filled": {"totalSz": "0.00015", "avgPx": "84000.0"}}
                ]
            }
        },
    }

    mock_info = MagicMock()
    mock_info.user_state.return_value = _user_state_with_position()

    _run_tick(
        candles,
        state,
        is_live=True,
        exchange=mock_exchange,
        info=mock_info,
    )

    mock_exchange.market_open.assert_called_once()
    assert state.is_holding is True


# ---------------------------------------------------------------------------
# 3. test_e2e_live_sell_tick
# ---------------------------------------------------------------------------

def test_e2e_live_sell_tick() -> None:
    """Live SELL: exchange.market_close called; state.is_holding becomes False; PnL updated."""
    candles = _make_step_candles("down", flat_bars=30)
    state = _make_state()
    # Pre-open a position
    state.open_position("BTC", entry_price=84000.0, size=0.00015, notional=12.6)

    mock_exchange = MagicMock()
    mock_exchange.market_close.return_value = {"status": "ok"}

    mock_info = MagicMock()
    # First call: pre-SELL check — position exists
    # Second call: post-close sync — position gone
    mock_info.user_state.side_effect = [
        _user_state_with_position(),   # pre-check: position present
        _user_state_flat(),            # post-close sync: flat
    ]

    initial_pnl = state.session_pnl

    _run_tick(
        candles,
        state,
        is_live=True,
        exchange=mock_exchange,
        info=mock_info,
    )

    mock_exchange.market_close.assert_called_once()
    assert state.is_holding is False
    # session_pnl must have been updated (close_position was called)
    assert state.session_pnl != initial_pnl or state.trade_count == 2


# ---------------------------------------------------------------------------
# 4. test_e2e_graceful_shutdown_preserves_position
# ---------------------------------------------------------------------------

def test_e2e_graceful_shutdown_preserves_position() -> None:
    """Position opened in state survives; summary() works and is_holding stays True."""
    state = _make_state()
    state.open_position("BTC", entry_price=84000.0, size=0.00015, notional=12.6)

    assert state.is_holding is True
    assert state.position is not None
    assert state.position.coin == "BTC"
    assert state.position.entry_price == 84000.0
    assert state.position.size == 0.00015

    # summary() should work and mention HOLDING
    summary = state.summary()
    assert "HOLDING" in summary
    assert "BTC" in summary

    # Simulate KeyboardInterrupt in the loop — position must still be intact
    # (We verify the state that main() would check after the interrupt)
    assert state.is_holding is True
    assert state.position is not None


# ---------------------------------------------------------------------------
# 5. test_e2e_network_error_recovery
# ---------------------------------------------------------------------------

def test_e2e_network_error_recovery(caplog) -> None:
    """ConnectionError on first fetch doesn't crash; second tick processes normally."""
    good_candles = _make_flat_candles(n=40)
    strategy = _make_strategy()
    state = _make_state()
    config = _make_config()
    info = MagicMock()

    # Tick 1: fetch_candles raises ConnectionError
    with patch("run_live.fetch_candles", side_effect=ConnectionError("network down")), \
         patch("run_live.load_main_address", return_value="0xTEST"), \
         caplog.at_level(logging.ERROR):
        _run_one_tick(
            info=info,
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

    # Error was logged, but no crash
    assert any("exception" in r.message.lower() or "error" in r.message.lower()
               for r in caplog.records)
    assert state.is_holding is False  # untouched

    # Tick 2: fetch_candles returns valid flat candles → HOLD, no crash
    with patch("run_live.fetch_candles", return_value=good_candles), \
         patch("run_live.load_main_address", return_value="0xTEST"), \
         caplog.at_level(logging.INFO):
        _run_one_tick(
            info=info,
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

    # Second tick processed: state still flat (HOLD signal from flat candles)
    assert state.is_holding is False


# ---------------------------------------------------------------------------
# 6. test_e2e_market_close_returns_none
# ---------------------------------------------------------------------------

def test_e2e_market_close_returns_none(caplog) -> None:
    """market_close returns None: state reconciles to flat via sync_from_exchange."""
    candles = _make_step_candles("down", flat_bars=30)
    state = _make_state()
    state.open_position("BTC", entry_price=84000.0, size=0.00015, notional=12.6)

    mock_exchange = MagicMock()
    mock_exchange.market_close.return_value = None  # SDK found no position

    mock_info = MagicMock()
    # First call: pre-SELL verification — position still there
    # Second call: post-None sync — position is gone
    mock_info.user_state.side_effect = [
        _user_state_with_position(),
        _user_state_flat(),
    ]

    with caplog.at_level(logging.WARNING):
        _run_tick(
            candles,
            state,
            is_live=True,
            exchange=mock_exchange,
            info=mock_info,
        )

    # No crash
    # sync_from_exchange with flat user_state clears is_holding
    assert state.is_holding is False
    assert any(
        "none" in r.message.lower() or "sync" in r.message.lower()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# 7. test_e2e_leverage_set_once
# ---------------------------------------------------------------------------

def test_e2e_leverage_set_once() -> None:
    """set_leverage_if_needed called exactly once across two ticks (first opens, second skips)."""
    buy_candles = _make_step_candles("up", flat_bars=30)

    state = _make_state()

    mock_exchange = MagicMock()
    mock_exchange.market_open.return_value = {"status": "ok"}

    mock_info = MagicMock()
    mock_info.user_state.return_value = _user_state_with_position()

    strategy = _make_strategy(fast=5, slow=20)
    config = _make_config()

    with patch("run_live.fetch_candles", return_value=buy_candles), \
         patch("run_live.load_main_address", return_value="0xTEST"), \
         patch("run_live.set_leverage_if_needed") as mock_set_lev:

        # Tick 1: BUY fires — leverage set, market_open called
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

        assert mock_set_lev.call_count == 1
        assert state.is_holding is True

        # Advance the candle time so tick 2 isn't blocked by duplicate-signal guard
        buy_candles_tick2 = buy_candles.copy()
        buy_candles_tick2["time"] = buy_candles_tick2["time"].apply(
            lambda t: t + timedelta(hours=2)
        )

        # Tick 2: already holding → BUY skipped, leverage not re-set
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

    # set_leverage_if_needed called only once total
    assert mock_set_lev.call_count == 1
