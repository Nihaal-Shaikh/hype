"""Tests for per-asset-class strategy routing in scanner.scan_universe — Phase 5E."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from hype_bot import AssetClass, TradableMarket
from run_scanner import build_default_strategies
from scanner import _resolve_strategy, scan_universe
from strategy import Signal


def _mkt(symbol: str, ac: AssetClass, open_now: bool = True) -> TradableMarket:
    return TradableMarket(
        dex="", symbol=symbol, asset_class=ac,
        max_leverage=20, size_decimals=5, min_notional=10.0,
        current_mid=100.0, open_now=open_now,
    )


def _fake_strategy(name: str, signal: Signal = Signal.HOLD) -> MagicMock:
    s = MagicMock()
    s.evaluate.return_value = signal
    s.describe.return_value = name
    return s


def _fake_candles() -> pd.DataFrame:
    return pd.DataFrame({
        "time": [datetime(2026, 1, 1, tzinfo=timezone.utc)],
        "open": [100.0], "high": [101.0], "low": [99.0],
        "close": [100.0], "volume": [1000.0],
    })


# --- _resolve_strategy ---------------------------------------------------

def test_resolve_single_strategy_returns_it():
    s = _fake_strategy("only")
    m = _mkt("BTC", AssetClass.CRYPTO)
    assert _resolve_strategy(s, m) is s


def test_resolve_dict_matches_class():
    crypto_s = _fake_strategy("crypto_strat")
    commodity_s = _fake_strategy("commodity_strat")
    spec = {AssetClass.CRYPTO: crypto_s, AssetClass.COMMODITY: commodity_s}

    assert _resolve_strategy(spec, _mkt("BTC", AssetClass.CRYPTO)) is crypto_s
    assert _resolve_strategy(spec, _mkt("xyz:CL", AssetClass.COMMODITY)) is commodity_s


def test_resolve_dict_missing_class_falls_back_to_first():
    crypto_s = _fake_strategy("crypto_strat")
    spec = {AssetClass.CRYPTO: crypto_s}
    # STOCK not in map → first value (crypto_s)
    assert _resolve_strategy(spec, _mkt("TSLA", AssetClass.STOCK)) is crypto_s


# --- scan_universe with dict spec ----------------------------------------

def test_scan_universe_dispatches_per_class():
    ema = _fake_strategy("ema(9/21)", Signal.BUY)
    rsi = _fake_strategy("rsi(14,30/70)", Signal.SELL)
    spec = {AssetClass.CRYPTO: ema, AssetClass.COMMODITY: rsi}

    markets = [
        _mkt("BTC", AssetClass.CRYPTO),
        _mkt("xyz:CL", AssetClass.COMMODITY),
    ]

    with patch("scanner.fetch_candles", return_value=_fake_candles()):
        results = scan_universe(MagicMock(), spec, markets)

    assert len(results) == 2
    by_sym = {r.market.symbol: r for r in results}

    # BTC routed to EMA → BUY
    assert by_sym["BTC"].signal == Signal.BUY
    assert "ema" in by_sym["BTC"].strategy_name

    # xyz:CL routed to RSI → SELL
    assert by_sym["xyz:CL"].signal == Signal.SELL
    assert "rsi" in by_sym["xyz:CL"].strategy_name

    ema.evaluate.assert_called_once()
    rsi.evaluate.assert_called_once()


def test_scan_universe_single_strategy_still_works():
    """Backwards-compat: passing a single Strategy applies to every market."""
    s = _fake_strategy("only", Signal.HOLD)
    markets = [
        _mkt("BTC", AssetClass.CRYPTO),
        _mkt("xyz:CL", AssetClass.COMMODITY),
    ]

    with patch("scanner.fetch_candles", return_value=_fake_candles()):
        results = scan_universe(MagicMock(), s, markets)

    assert len(results) == 2
    assert all(r.strategy_name == "only" for r in results)
    assert s.evaluate.call_count == 2


# --- build_default_strategies --------------------------------------------

def test_default_strategies_cover_all_classes():
    spec = build_default_strategies()
    # Trend-following for trending markets
    assert "ema" in spec[AssetClass.CRYPTO].describe()
    assert "ema" in spec[AssetClass.INDEX].describe()
    # Mean-reversion for oscillating markets
    assert "rsi" in spec[AssetClass.COMMODITY].describe()
    assert "rsi" in spec[AssetClass.STOCK].describe()
    assert "rsi" in spec[AssetClass.FOREX].describe()


def test_default_strategies_respects_fast_slow_for_ema():
    spec = build_default_strategies(fast=12, slow=30)
    ema_desc = spec[AssetClass.CRYPTO].describe()
    assert "12" in ema_desc
    assert "30" in ema_desc
