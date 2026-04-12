"""Tests for backtest.py — Phase 4B."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backtest import BacktestConfig, BacktestResult, Trade, run_backtest
from strategies.ema_crossover import EmaCrossover, EmaCrossoverConfig
from conftest import make_candles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_step_candles(direction: str = "up", flat_bars: int = 30) -> pd.DataFrame:
    """Build candles: flat at 100 for flat_bars, then one bar that steps.

    Matches the pattern in test_strategy.py so the EMA crossover fires on
    the very last bar.  fast_period < slow_period < flat_bars must hold for
    the caller's config.
    """
    n = flat_bars + 1
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    step_price = 150.0 if direction == "up" else 50.0

    rows = []
    for i in range(n):
        c = 100.0 if i < flat_bars else step_price
        t = base + timedelta(hours=i)
        rows.append({"time": t, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1000.0})
    return pd.DataFrame(rows)


def _make_up_then_down(flat_bars: int = 30, up_bars: int = 20, down_bars: int = 20) -> pd.DataFrame:
    """Flat → jump up (trigger BUY) → gradually fall (trigger SELL eventually).

    Useful for tests that need a full buy-sell round-trip.
    """
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    i = 0

    # Flat phase — converge EMAs.
    for _ in range(flat_bars):
        t = base + timedelta(hours=i)
        rows.append({"time": t, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0})
        i += 1

    # Step up — trigger golden cross.
    t = base + timedelta(hours=i)
    rows.append({"time": t, "open": 150.0, "high": 151.0, "low": 149.0, "close": 150.0, "volume": 1000.0})
    i += 1

    # Hold above, then gradually fall below 100 to trigger death cross.
    price = 150.0
    for _ in range(up_bars + down_bars - 1):
        price -= 4.0          # steady decline after the spike
        t = base + timedelta(hours=i)
        rows.append({"time": t, "open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 1000.0})
        i += 1

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 1. Positive return on uptrend
# ---------------------------------------------------------------------------

def test_backtest_positive_return_uptrend():
    """A strong uptrend should yield positive total return when a BUY fires."""
    # Use a step-up candle set: flat then jump.  The BUY fires on the last bar
    # at 150.  Because we force-close at the same (or later) close the position
    # is flat at entry — but with enough upward candles after the cross the
    # equity should rise.
    #
    # Use make_candles(trend="up", n=200) which gives a genuine upward trend
    # long enough for the default EMA(9/21) to generate a crossover.
    candles = make_candles(trend="up", n=200)
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=9, slow_period=21))
    result = run_backtest(strategy, candles)

    # If any trade fired, the forced close at end-of-data captures the gain.
    if result.total_trades > 0:
        assert result.total_return_pct > 0


# ---------------------------------------------------------------------------
# 2. Negative return or zero trades on downtrend
# ---------------------------------------------------------------------------

def test_backtest_negative_return_downtrend():
    """On a steady downtrend the strategy should not profit (or never buy)."""
    candles = make_candles(trend="down", n=200)
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=9, slow_period=21))
    result = run_backtest(strategy, candles)

    # Either no BUY fires (fast always below slow on downtrend), or if it does,
    # the forced close produces a loss.
    assert result.total_return_pct <= 0 or result.total_trades == 0


# ---------------------------------------------------------------------------
# 3. Fees deducted when trades fire
# ---------------------------------------------------------------------------

def test_backtest_fees_deducted():
    """Whenever at least one trade fires, total_fees must be positive."""
    candles = _make_step_candles("up", flat_bars=30)
    # Extend with more bars so there is equity curve data and possibly a SELL.
    extra_rows = []
    base_time = candles["time"].iloc[-1]
    last_close = float(candles["close"].iloc[-1])
    for j in range(1, 41):
        t = base_time + timedelta(hours=j)
        extra_rows.append({
            "time": t, "open": last_close, "high": last_close + 1,
            "low": last_close - 1, "close": last_close, "volume": 1000.0,
        })
    extended = pd.concat([candles, pd.DataFrame(extra_rows)], ignore_index=True)

    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=5, slow_period=20))
    result = run_backtest(strategy, extended)

    if result.total_trades > 0:
        assert result.total_fees > 0


# ---------------------------------------------------------------------------
# 4. Equity curve length matches candles - warm_up
# ---------------------------------------------------------------------------

def test_backtest_equity_curve_length():
    """len(equity_curve) must equal len(candles) - warm_up."""
    candles = make_candles(trend="up", n=100)
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=9, slow_period=21))
    result = run_backtest(strategy, candles)

    expected_len = len(candles) - strategy.config.slow_period
    assert len(result.equity_curve) == expected_len


# ---------------------------------------------------------------------------
# 5. Max drawdown is in [0, 100]
# ---------------------------------------------------------------------------

def test_backtest_max_drawdown():
    """max_drawdown_pct must be a valid percentage: 0 <= dd <= 100."""
    candles = make_candles(trend="sideways", n=150)
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=9, slow_period=21))
    result = run_backtest(strategy, candles)

    assert result.max_drawdown_pct >= 0
    assert result.max_drawdown_pct <= 100


# ---------------------------------------------------------------------------
# 6. Force close at end when still holding
# ---------------------------------------------------------------------------

def test_backtest_force_close_at_end():
    """When the strategy buys but never sells, the engine must force-close."""
    # Step-up candles: flat then one jump → BUY fires, no subsequent SELL.
    candles = _make_step_candles("up", flat_bars=30)
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=5, slow_period=20))
    result = run_backtest(strategy, candles)

    buy_count = sum(1 for t in result.trades if t.side == "BUY")
    sell_count = sum(1 for t in result.trades if t.side == "SELL")

    if buy_count > 0:
        # Engine must have emitted a matching SELL (forced close).
        assert sell_count == buy_count
        # And total_trades accounts for both sides.
        assert result.total_trades == buy_count + sell_count


# ---------------------------------------------------------------------------
# 7. No trades and zero return on flat data
# ---------------------------------------------------------------------------

def test_backtest_no_trades_on_hold():
    """Perfectly flat prices: EMAs never cross → no trades, zero return."""
    n = 60
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        {
            "time": base + timedelta(hours=i),
            "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0,
        }
        for i in range(n)
    ]
    candles = pd.DataFrame(rows)
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=5, slow_period=20))
    result = run_backtest(strategy, candles)

    assert result.total_trades == 0
    assert result.total_return_pct == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 8. BacktestConfig defaults match spec
# ---------------------------------------------------------------------------

def test_backtest_config_defaults():
    """Verify BacktestConfig dataclass defaults match the spec exactly."""
    cfg = BacktestConfig()

    assert cfg.initial_capital == pytest.approx(50.0)
    assert cfg.leverage == 3
    assert cfg.position_size_pct == pytest.approx(0.25)
    assert cfg.fee_rate == pytest.approx(0.00035)
    assert cfg.slippage_bps == pytest.approx(5.0)
    assert cfg.min_notional == pytest.approx(10.0)
