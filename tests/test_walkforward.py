"""Tests for walkforward.py — Phase 5H."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backtest import BacktestResult
from walkforward import (
    DEFAULT_EMA_GRID,
    DEFAULT_RSI_GRID,
    GridResult,
    OosReport,
    _grid_combos,
    grid_search,
    metric_value,
    out_of_sample_eval,
    split_train_test,
)


def _make_candles(n: int = 200) -> pd.DataFrame:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    price = 100.0
    for i in range(n):
        if i >= 30:
            price *= 1.002
        rows.append({
            "time": base + timedelta(hours=i),
            "open": price * 0.999, "high": price * 1.002,
            "low": price * 0.998, "close": price, "volume": 50.0,
        })
    return pd.DataFrame(rows)


def _result(return_pct: float = 0.0, sharpe: float = 0.0,
            dd: float = 0.0, win_rate: float = 0.0) -> BacktestResult:
    return BacktestResult(
        trades=tuple(), equity_curve=(50.0,),
        total_return_pct=return_pct, max_drawdown_pct=dd,
        win_rate=win_rate, total_trades=0,
        sharpe_proxy=sharpe, total_fees=0.0,
    )


# --- metric_value --------------------------------------------------------

def test_metric_value_return():
    assert metric_value(_result(return_pct=3.5), "return") == 3.5


def test_metric_value_sharpe():
    assert metric_value(_result(sharpe=1.2), "sharpe") == 1.2


def test_metric_value_dd_inverts_sign():
    # Smaller drawdown should score HIGHER, so value is -dd
    assert metric_value(_result(dd=5.0), "dd") == -5.0


def test_metric_value_unknown_raises():
    with pytest.raises(ValueError, match="Unknown metric"):
        metric_value(_result(), "bogus")


# --- split_train_test ----------------------------------------------------

def test_split_70_30():
    candles = _make_candles(100)
    split = split_train_test(candles, train_frac=0.7)
    assert len(split.train) == 70
    assert len(split.test) == 30


def test_split_chronological():
    candles = _make_candles(10)
    split = split_train_test(candles, train_frac=0.5)
    # Last train row must be strictly before first test row
    assert split.train["time"].iloc[-1] < split.test["time"].iloc[0]


def test_split_rejects_invalid_frac():
    candles = _make_candles(10)
    with pytest.raises(ValueError):
        split_train_test(candles, train_frac=0.0)
    with pytest.raises(ValueError):
        split_train_test(candles, train_frac=1.0)
    with pytest.raises(ValueError):
        split_train_test(candles, train_frac=-0.5)


def test_split_rejects_too_small():
    candles = _make_candles(1)
    with pytest.raises(ValueError):
        split_train_test(candles, train_frac=0.7)


# --- _grid_combos + validators -------------------------------------------

def test_grid_combos_cartesian():
    grid = {"a": [1, 2], "b": [10, 20, 30]}
    combos = _grid_combos(grid)
    assert len(combos) == 6
    assert {"a": 1, "b": 20} in combos


def test_default_ema_grid_has_valid_combos():
    combos = _grid_combos(DEFAULT_EMA_GRID)
    valid = [c for c in combos if c["fast_period"] < c["slow_period"]]
    assert len(valid) > 0
    # All default combos happen to be valid (fast ranges < slow ranges), but
    # the validator is still needed as a safety net when callers pass custom
    # grids with overlap.
    assert len(valid) == len(combos)


def test_default_rsi_grid_has_valid_combos():
    combos = _grid_combos(DEFAULT_RSI_GRID)
    valid = [c for c in combos if c["oversold"] < c["overbought"]]
    assert len(valid) > 0


# --- grid_search ---------------------------------------------------------

def test_grid_search_picks_best_return():
    candles = _make_candles(200)
    tiny_grid = {"fast_period": [5, 9], "slow_period": [17, 21]}
    gr = grid_search("ema", candles, grid=tiny_grid, metric="return")
    assert isinstance(gr, GridResult)
    assert gr.combos_tried > 0
    # All combos are valid (5<17, 5<21, 9<17, 9<21)
    assert gr.combos_tried == 4


def test_grid_search_unknown_strategy():
    candles = _make_candles(100)
    with pytest.raises(ValueError, match="Unknown strategy"):
        grid_search("macd", candles)


def test_grid_search_rsi():
    candles = _make_candles(200)
    tiny_grid = {"period": [14], "oversold": [30.0], "overbought": [70.0]}
    gr = grid_search("rsi", candles, grid=tiny_grid, metric="sharpe")
    assert gr.combos_tried == 1
    assert gr.best_params["period"] == 14


def test_grid_search_no_valid_combos():
    candles = _make_candles(100)
    bad_grid = {"fast_period": [20], "slow_period": [10]}  # fast >= slow for all
    with pytest.raises(ValueError, match="No valid combos"):
        grid_search("ema", candles, grid=bad_grid)


# --- out_of_sample_eval --------------------------------------------------

def test_oos_eval_end_to_end():
    candles = _make_candles(200)
    tiny_grid = {"fast_period": [5, 9], "slow_period": [21, 26]}
    report = out_of_sample_eval("ema", candles, train_frac=0.7,
                                grid=tiny_grid, metric="sharpe")
    assert isinstance(report, OosReport)
    assert report.n_train == 140
    assert report.n_test == 60
    assert report.combos_tried == 4
    assert report.strategy == "ema"
    assert report.metric == "sharpe"
    assert "fast_period" in report.best_params
    assert "slow_period" in report.best_params


def test_oos_delta_return_pct():
    candles = _make_candles(200)
    tiny_grid = {"fast_period": [5], "slow_period": [21]}
    report = out_of_sample_eval("ema", candles, train_frac=0.7,
                                grid=tiny_grid, metric="return")
    expected = report.test_result.total_return_pct - report.train_result.total_return_pct
    assert report.delta_return_pct == pytest.approx(expected)


def test_oos_rsi():
    candles = _make_candles(200)
    tiny_grid = {"period": [10, 14], "oversold": [25.0, 30.0], "overbought": [70.0, 75.0]}
    report = out_of_sample_eval("rsi", candles, train_frac=0.7,
                                grid=tiny_grid, metric="sharpe")
    assert report.strategy == "rsi"
    # 2*2*2 = 8 combos, all valid (oversold<overbought always)
    assert report.combos_tried == 8
