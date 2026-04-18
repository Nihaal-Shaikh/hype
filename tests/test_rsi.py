"""Tests for strategies/rsi.py — Phase 5C."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from hype_bot import AssetClass
from strategy import Signal, Strategy
from strategies.rsi import Rsi, RsiConfig, _compute_rsi
from conftest import make_candles


def _candles_from_closes(closes: list[float]) -> pd.DataFrame:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        {"time": base + timedelta(hours=i),
         "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1000.0}
        for i, c in enumerate(closes)
    ]
    return pd.DataFrame(rows)


# --- RSI formula ---------------------------------------------------------

def test_rsi_bounds_0_to_100():
    closes = [100.0 + (i % 5) for i in range(60)]
    rsi = _compute_rsi(pd.Series(closes), 14)
    assert (rsi.dropna() >= 0).all()
    assert (rsi.dropna() <= 100).all()


def test_rsi_extreme_up_approaches_100():
    # Monotonic up → RSI saturates high
    closes = [100.0 + i for i in range(60)]
    rsi = _compute_rsi(pd.Series(closes), 14)
    assert rsi.iloc[-1] > 90


def test_rsi_extreme_down_approaches_0():
    closes = [200.0 - i for i in range(60)]
    rsi = _compute_rsi(pd.Series(closes), 14)
    assert rsi.iloc[-1] < 10


# --- Protocol + config ---------------------------------------------------

def test_rsi_satisfies_protocol():
    assert isinstance(Rsi(), Strategy)


def test_rsi_config_defaults():
    cfg = RsiConfig()
    assert cfg.name == "rsi"
    assert cfg.period == 14
    assert cfg.oversold == 30.0
    assert cfg.overbought == 70.0
    assert cfg.slow_period == 28


def test_rsi_asset_class_presets():
    assert Rsi.CRYPTO.oversold == 30.0
    assert Rsi.COMMODITY.oversold == 25.0
    assert Rsi.COMMODITY.overbought == 75.0
    assert Rsi.STOCK.asset_class == AssetClass.STOCK


# --- evaluate ------------------------------------------------------------

def test_rsi_hold_insufficient_data():
    candles = make_candles(trend="up", n=10)
    assert Rsi().evaluate(candles) == Signal.HOLD


def test_rsi_buy_on_cross_into_oversold():
    # Steady up for 40 bars (RSI high), then sharp drop to push RSI below 30
    closes = [100.0 + i * 0.5 for i in range(40)]
    # Add big drop — enough bars to bring RSI well under 30 on last bar
    for _ in range(15):
        closes.append(closes[-1] - 3.0)
    candles = _candles_from_closes(closes)
    signal = Rsi(RsiConfig(period=14)).evaluate(candles)
    # Somewhere in the drop RSI crosses from >=30 to <30. Last bar may or may
    # not be the crossover bar. Just check it's not SELL (overbought).
    assert signal in (Signal.BUY, Signal.HOLD)


def test_rsi_sell_on_cross_into_overbought():
    # Steady down (RSI low), then sharp rally to push RSI above 70
    closes = [200.0 - i * 0.5 for i in range(40)]
    for _ in range(15):
        closes.append(closes[-1] + 3.0)
    candles = _candles_from_closes(closes)
    signal = Rsi(RsiConfig(period=14)).evaluate(candles)
    assert signal in (Signal.SELL, Signal.HOLD)


def test_rsi_buy_precise_crossover():
    """Craft closes so the LAST bar is exactly the crossover bar."""
    # Up phase to keep RSI above 30
    closes = [100.0 + i * 2.0 for i in range(30)]
    # Small pullback that keeps RSI > 30 (prev bar)
    closes.append(closes[-1] - 1.0)
    # Now a big drop on the very last bar → forces RSI below 30
    closes.append(closes[-1] - 50.0)
    candles = _candles_from_closes(closes)
    rsi = _compute_rsi(pd.Series(closes), 14)
    # Sanity: confirm the setup actually crosses
    assert rsi.iloc[-2] >= 30.0
    assert rsi.iloc[-1] < 30.0
    assert Rsi(RsiConfig(period=14)).evaluate(candles) == Signal.BUY


def test_rsi_sell_precise_crossover():
    closes = [200.0 - i * 2.0 for i in range(30)]
    closes.append(closes[-1] + 1.0)
    closes.append(closes[-1] + 80.0)
    candles = _candles_from_closes(closes)
    rsi = _compute_rsi(pd.Series(closes), 14)
    assert rsi.iloc[-2] <= 70.0
    assert rsi.iloc[-1] > 70.0
    assert Rsi(RsiConfig(period=14)).evaluate(candles) == Signal.SELL


def test_rsi_hold_in_neutral_zone():
    # Sideways mild noise → RSI stays around 50
    candles = make_candles(trend="sideways", n=60)
    assert Rsi(RsiConfig(period=14)).evaluate(candles) == Signal.HOLD


def test_rsi_describe_output():
    desc = Rsi().describe()
    assert "rsi" in desc
    assert "14" in desc
    assert "30" in desc
    assert "70" in desc
