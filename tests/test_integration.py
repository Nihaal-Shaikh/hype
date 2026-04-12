"""Integration tests for run_backtest.py pipeline — Phase 4E.

All tests use synthetic candle data. No live API calls are made.
"""

from __future__ import annotations

import csv
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from backtest import BacktestConfig, BacktestResult, run_backtest
from strategies.ema_crossover import EmaCrossover, EmaCrossoverConfig
from run_backtest import export_csv, _CSV_COLUMNS


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _make_synthetic_candles(n: int = 100, start_price: float = 84000.0) -> pd.DataFrame:
    """Build synthetic BTC-like candles: flat warm-up then steady upward trend."""
    base = datetime(2026, 4, 5, tzinfo=timezone.utc)
    rows = []
    price = start_price
    for i in range(n):
        # flat for first 30 bars, then trend up
        if i >= 30:
            price *= 1.003
        t = base + timedelta(hours=i)
        rows.append({
            "time": t,
            "open": price * 0.999,
            "high": price * 1.002,
            "low": price * 0.998,
            "close": price,
            "volume": 50.0 + i * 0.5,
        })
    return pd.DataFrame(rows)


def _make_flat_candles(n: int = 60, price: float = 100.0) -> pd.DataFrame:
    """Perfectly flat candles — EMAs never cross → no trades."""
    base = datetime(2026, 4, 5, tzinfo=timezone.utc)
    rows = [
        {
            "time": base + timedelta(hours=i),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1000.0,
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 1. Full pipeline — synthetic candles, verify BacktestResult shape
# ---------------------------------------------------------------------------

def test_full_pipeline_synthetic():
    """Full pipeline: synthetic candles → EmaCrossover → run_backtest.

    Verifies that BacktestResult has the expected shape and type contract:
    - trades is a tuple
    - equity_curve is a tuple
    - all summary metrics are floats
    - total_trades == len(trades)
    - equity_curve length == len(candles) - slow_period
    """
    candles = _make_synthetic_candles(n=120)
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=9, slow_period=21))
    config = BacktestConfig()

    result = run_backtest(strategy, candles, config)

    # Shape contract
    assert isinstance(result, BacktestResult)
    assert isinstance(result.trades, tuple)
    assert isinstance(result.equity_curve, tuple)

    # Metric types
    assert isinstance(result.total_return_pct, float)
    assert isinstance(result.max_drawdown_pct, float)
    assert isinstance(result.win_rate, float)
    assert isinstance(result.sharpe_proxy, float)
    assert isinstance(result.total_fees, float)
    assert isinstance(result.total_trades, int)

    # Consistency
    assert result.total_trades == len(result.trades)
    expected_equity_len = len(candles) - strategy.config.slow_period
    assert len(result.equity_curve) == expected_equity_len

    # Metric bounds
    assert result.max_drawdown_pct >= 0.0
    assert 0.0 <= result.win_rate <= 1.0
    assert result.total_fees >= 0.0


# ---------------------------------------------------------------------------
# 2. BacktestConfig overrides propagate correctly
# ---------------------------------------------------------------------------

def test_pipeline_with_custom_config():
    """Custom BacktestConfig values propagate into the result.

    Uses a higher initial_capital and verifies fees scale with notional
    (larger capital → larger notional → larger absolute fees when trades fire).
    """
    candles = _make_synthetic_candles(n=120)
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=9, slow_period=21))

    config_small = BacktestConfig(initial_capital=50.0, position_size_pct=0.25, leverage=3)
    config_large = BacktestConfig(initial_capital=500.0, position_size_pct=0.25, leverage=3)

    result_small = run_backtest(strategy, candles, config_small)
    result_large = run_backtest(strategy, candles, config_large)

    # Both should have same trade count (same signals, same candles)
    assert result_small.total_trades == result_large.total_trades

    # With 10x capital the large config should have ~10x fees when trades fire
    if result_small.total_trades > 0:
        ratio = result_large.total_fees / result_small.total_fees
        assert 9.0 <= ratio <= 11.0, (
            f"Expected ~10x fee ratio, got {ratio:.2f}"
        )

    # Return percentage should be the same regardless of capital size
    assert result_small.total_return_pct == pytest.approx(
        result_large.total_return_pct, abs=0.01
    )


# ---------------------------------------------------------------------------
# 3. Flat data → 0 trades and 0% return
# ---------------------------------------------------------------------------

def test_pipeline_no_trades_flat_data():
    """Perfectly flat candles produce zero trades and zero percent return."""
    candles = _make_flat_candles(n=60, price=100.0)
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=5, slow_period=20))
    config = BacktestConfig()

    result = run_backtest(strategy, candles, config)

    assert result.total_trades == 0
    assert result.total_return_pct == pytest.approx(0.0, abs=1e-9)
    assert result.total_fees == pytest.approx(0.0, abs=1e-9)
    assert result.win_rate == pytest.approx(0.0, abs=1e-9)
    # equity_curve should be all initial_capital (no trades, no position movement)
    for eq in result.equity_curve:
        assert eq == pytest.approx(config.initial_capital, abs=1e-6)


# ---------------------------------------------------------------------------
# 4. CSV export — correct columns and row count
# ---------------------------------------------------------------------------

def test_csv_export(tmp_path: Path):
    """Pipeline result exports correctly to CSV with expected columns and rows."""
    candles = _make_synthetic_candles(n=120)
    strategy = EmaCrossover(EmaCrossoverConfig(fast_period=9, slow_period=21))
    config = BacktestConfig()

    result = run_backtest(strategy, candles, config)

    csv_path = tmp_path / "trades.csv"
    export_csv(result.trades, str(csv_path))

    assert csv_path.exists(), "CSV file was not created"

    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    # Column contract
    assert reader.fieldnames == _CSV_COLUMNS, (
        f"Expected columns {_CSV_COLUMNS}, got {reader.fieldnames}"
    )

    # Row count matches trade count
    assert len(rows) == len(result.trades), (
        f"Expected {len(result.trades)} rows, got {len(rows)}"
    )

    # Spot-check that side values are BUY or SELL and price/size are numeric
    for row in rows:
        assert row["side"] in ("BUY", "SELL")
        float(row["price"])   # raises ValueError if not numeric
        float(row["size"])
        float(row["fee"])
        float(row["notional"])
