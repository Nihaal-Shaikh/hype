"""Tests for run_matrix.py — Phase 5F."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from run_matrix import (
    MatrixRow,
    build_strategy,
    export_csv,
    run_matrix,
    sort_rows,
)
from strategies.ema_crossover import EmaCrossover
from strategies.rsi import Rsi


# --- build_strategy ------------------------------------------------------

def test_build_strategy_ema():
    s = build_strategy("ema", fast=12, slow=26)
    assert isinstance(s, EmaCrossover)
    assert s.config.fast_period == 12
    assert s.config.slow_period == 26


def test_build_strategy_rsi():
    s = build_strategy("rsi")
    assert isinstance(s, Rsi)


def test_build_strategy_case_insensitive():
    assert isinstance(build_strategy("EMA"), EmaCrossover)
    assert isinstance(build_strategy("  Rsi "), Rsi)


def test_build_strategy_unknown_raises():
    with pytest.raises(ValueError, match="Unknown strategy"):
        build_strategy("macd")


# --- sort_rows -----------------------------------------------------------

def _row(return_pct: float = 0.0, sharpe: float = 0.0, dd: float = 0.0,
         win: float = 0.0, trades: int = 0, coin: str = "BTC",
         strategy: str = "ema") -> MatrixRow:
    return MatrixRow(
        coin=coin, strategy=strategy, interval="1h", days=7,
        return_pct=return_pct, trades=trades, win_rate_pct=win,
        max_drawdown_pct=dd, sharpe=sharpe, fees=0.0,
    )


def test_sort_by_return_desc():
    rows = [_row(return_pct=1.0), _row(return_pct=5.0), _row(return_pct=-2.0)]
    sorted_ = sort_rows(rows, key="return")
    assert [r.return_pct for r in sorted_] == [5.0, 1.0, -2.0]


def test_sort_by_sharpe_desc():
    rows = [_row(sharpe=1.5), _row(sharpe=-0.3), _row(sharpe=2.8)]
    sorted_ = sort_rows(rows, key="sharpe")
    assert [r.sharpe for r in sorted_] == [2.8, 1.5, -0.3]


def test_sort_by_dd_prefers_smaller():
    rows = [_row(dd=10.0), _row(dd=3.0), _row(dd=20.0)]
    sorted_ = sort_rows(rows, key="dd")
    assert [r.max_drawdown_pct for r in sorted_] == [3.0, 10.0, 20.0]


def test_sort_unknown_key_raises():
    with pytest.raises(ValueError, match="Unknown sort key"):
        sort_rows([_row()], key="bogus")


# --- run_matrix ----------------------------------------------------------

def _fake_candles(n: int = 100, start_price: float = 100.0) -> pd.DataFrame:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    price = start_price
    for i in range(n):
        if i >= 30:
            price *= 1.003
        rows.append({
            "time": base + timedelta(hours=i),
            "open": price * 0.999, "high": price * 1.002,
            "low": price * 0.998, "close": price, "volume": 50.0,
        })
    return pd.DataFrame(rows)


def test_run_matrix_produces_cell_per_combo():
    info = MagicMock()
    coins = ["BTC", "ETH"]
    strategies = ["ema", "rsi"]
    days_list = [1, 3]
    with patch("run_matrix.fetch_candles", return_value=_fake_candles(100)):
        rows = run_matrix(info, coins, strategies, "1h", days_list)
    # 2 coins × 2 strategies × 2 windows = 8 cells
    assert len(rows) == 8


def test_run_matrix_skips_empty_candles():
    info = MagicMock()
    # BTC returns empty, ETH returns candles
    def fake(i, coin, *args, **kwargs):
        if coin == "BTC":
            return pd.DataFrame()
        return _fake_candles(100)
    with patch("run_matrix.fetch_candles", side_effect=fake):
        rows = run_matrix(info, ["BTC", "ETH"], ["ema"], "1h", [3])
    # Only ETH cells
    assert all(r.coin == "ETH" for r in rows)


def test_run_matrix_row_values():
    info = MagicMock()
    with patch("run_matrix.fetch_candles", return_value=_fake_candles(100)):
        rows = run_matrix(info, ["BTC"], ["ema"], "1h", [3])
    r = rows[0]
    assert r.coin == "BTC"
    assert r.strategy == "ema"
    assert r.interval == "1h"
    assert r.days == 3
    assert isinstance(r.return_pct, float)
    assert isinstance(r.trades, int)


# --- CSV export ----------------------------------------------------------

def test_export_csv_roundtrip(tmp_path: Path):
    rows = [
        _row(coin="BTC", strategy="ema", return_pct=1.23, trades=2, win=50.0),
        _row(coin="ETH", strategy="rsi", return_pct=-0.5, trades=1, win=0.0),
    ]
    path = tmp_path / "out.csv"
    export_csv(rows, str(path))

    with open(path) as fh:
        reader = csv.DictReader(fh)
        read_rows = list(reader)
    assert len(read_rows) == 2
    assert read_rows[0]["coin"] == "BTC"
    assert read_rows[0]["strategy"] == "ema"
    assert float(read_rows[0]["return_pct"]) == pytest.approx(1.23)
    assert read_rows[1]["coin"] == "ETH"
