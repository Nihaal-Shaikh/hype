"""Tests for strategy.py and strategies/ema_crossover.py — Phase 4A."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from hype_bot import AssetClass
from strategy import Signal, Strategy, StrategyConfig
from strategies.ema_crossover import EmaCrossover, EmaCrossoverConfig
from conftest import make_candles


def _make_step_candles(direction: str = "up", flat_bars: int = 30) -> pd.DataFrame:
    """Build candles: flat at 100 for flat_bars, then one bar that steps.

    The step on the last bar causes fast EMA to cross slow EMA because:
    - After flat bars, both EMAs converge to 100 (fast=slow=100)
    - The step bar moves the fast EMA more than the slow (shorter span)
    - So on the last bar: fast crosses above (up) or below (down) slow

    Returns flat_bars + 1 rows total. Use with fast_period < slow_period < flat_bars.
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


# --- Signal enum ---

def test_signal_members():
    assert Signal.BUY.value == "buy"
    assert Signal.SELL.value == "sell"
    assert Signal.HOLD.value == "hold"
    assert len(Signal) == 3


# --- StrategyConfig ---

def test_strategy_config_defaults():
    cfg = StrategyConfig()
    assert cfg.name == "unnamed"
    assert cfg.asset_class is None


# --- Protocol compliance ---

def test_ema_crossover_satisfies_protocol():
    ema = EmaCrossover()
    assert isinstance(ema, Strategy)


# --- EMA crossover: golden cross (BUY) ---

def test_evaluate_buy_on_golden_cross():
    """Flat at 100 then one big step up → fast EMA crosses above slow on last bar."""
    candles = _make_step_candles("up", flat_bars=30)
    ema = EmaCrossover(EmaCrossoverConfig(fast_period=5, slow_period=20))
    # After 30 flat bars both EMAs = 100. The step to 150 on bar 30 causes
    # fast EMA to jump more than slow → golden cross on the last bar.
    assert ema.evaluate(candles) == Signal.BUY


# --- EMA crossover: death cross (SELL) ---

def test_evaluate_sell_on_death_cross():
    """Flat at 100 then one big step down → fast EMA crosses below slow on last bar."""
    candles = _make_step_candles("down", flat_bars=30)
    ema = EmaCrossover(EmaCrossoverConfig(fast_period=5, slow_period=20))
    assert ema.evaluate(candles) == Signal.SELL


# --- EMA crossover: HOLD on flat data ---

def test_evaluate_hold_on_flat_data():
    """Perfectly flat prices: both EMAs stay equal, no crossover."""
    n = 50
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        rows.append({
            "time": base + timedelta(hours=i),
            "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0,
        })
    candles = pd.DataFrame(rows)
    ema = EmaCrossover()
    assert ema.evaluate(candles) == Signal.HOLD


# --- EMA crossover: HOLD on insufficient data ---

def test_evaluate_hold_insufficient_data():
    candles = make_candles(trend="up", n=10)  # fewer than default slow_period=21
    ema = EmaCrossover()
    assert ema.evaluate(candles) == Signal.HOLD


# --- EmaCrossoverConfig asset-class presets ---

def test_config_defaults_and_asset_class():
    default_cfg = EmaCrossoverConfig()
    assert default_cfg.name == "ema_crossover"
    assert default_cfg.fast_period == 9
    assert default_cfg.slow_period == 21
    assert default_cfg.asset_class is None

    crypto = EmaCrossover.CRYPTO
    assert crypto.fast_period == 9
    assert crypto.slow_period == 21
    assert crypto.asset_class == AssetClass.CRYPTO

    commodity = EmaCrossover.COMMODITY
    assert commodity.fast_period == 12
    assert commodity.slow_period == 26
    assert commodity.asset_class == AssetClass.COMMODITY

    stock = EmaCrossover.STOCK
    assert stock.fast_period == 10
    assert stock.slow_period == 30
    assert stock.asset_class == AssetClass.STOCK


# --- describe() ---

def test_describe_output():
    ema = EmaCrossover()
    desc = ema.describe()
    assert "ema_crossover" in desc
    assert "9" in desc
    assert "21" in desc

    custom = EmaCrossover(EmaCrossoverConfig(fast_period=12, slow_period=26))
    desc2 = custom.describe()
    assert "12" in desc2
    assert "26" in desc2
