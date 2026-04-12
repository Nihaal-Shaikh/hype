"""Tests for scanner.py and the shared fetch_candles in hype_bot.py — Phase 4C."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from hype_bot import AssetClass, TradableMarket, fetch_candles
from scanner import ScanResult, scan_universe
from strategy import Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RAW_CANDLES = [
    {"t": 1700000000000, "T": 1700003600000, "o": "100.0", "h": "105.0", "l": "99.0", "c": "103.0", "v": "500.0", "n": 120},
    {"t": 1700003600000, "T": 1700007200000, "o": "103.0", "h": "108.0", "l": "102.0", "c": "106.0", "v": "600.0", "n": 130},
]


def _make_market(
    symbol: str = "xyz:CL",
    dex: str = "xyz",
    asset_class: AssetClass = AssetClass.COMMODITY,
    open_now: bool = True,
) -> TradableMarket:
    return TradableMarket(
        dex=dex,
        symbol=symbol,
        asset_class=asset_class,
        max_leverage=20,
        size_decimals=3,
        min_notional=10.0,
        current_mid=100.0,
        open_now=open_now,
    )


def _make_strategy(signal: Signal = Signal.HOLD) -> MagicMock:
    strategy = MagicMock()
    strategy.evaluate.return_value = signal
    strategy.describe.return_value = "mock_strategy(fast=9,slow=21)"
    return strategy


# ---------------------------------------------------------------------------
# fetch_candles tests
# ---------------------------------------------------------------------------

def test_fetch_candles_schema():
    """mock candles_snapshot returning raw data → DataFrame has correct columns and types."""
    info = MagicMock()
    info.candles_snapshot.return_value = _RAW_CANDLES

    df = fetch_candles(info, "xyz:CL", "1h", 48)

    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert pd.api.types.is_datetime64_any_dtype(df["time"])
    for col in ("open", "high", "low", "close", "volume"):
        assert pd.api.types.is_numeric_dtype(df[col]), f"{col} should be numeric"
    assert df["close"].iloc[0] == pytest.approx(103.0)
    assert df["close"].iloc[1] == pytest.approx(106.0)


def test_fetch_candles_empty():
    """mock returning [] → empty DataFrame."""
    info = MagicMock()
    info.candles_snapshot.return_value = []

    df = fetch_candles(info, "BTC", "1h", 48)

    assert isinstance(df, pd.DataFrame)
    assert df.empty


# ---------------------------------------------------------------------------
# scan_universe tests
# ---------------------------------------------------------------------------

def test_scan_universe_skips_closed_market():
    """Market with open_now=False must not appear in results."""
    info = MagicMock()
    closed = _make_market(open_now=False)
    strategy = _make_strategy()

    with patch("scanner.fetch_candles") as mock_fc:
        results = scan_universe(info, strategy, [closed])

    assert results == []
    mock_fc.assert_not_called()


def test_scan_universe_returns_hold_empty_candles():
    """When fetch_candles returns empty DataFrame, signal should be HOLD."""
    info = MagicMock()
    market = _make_market(open_now=True)
    strategy = _make_strategy(signal=Signal.BUY)

    with patch("scanner.fetch_candles", return_value=pd.DataFrame()):
        results = scan_universe(info, strategy, [market])

    assert len(results) == 1
    assert results[0].signal == Signal.HOLD
    strategy.evaluate.assert_not_called()


def test_scan_universe_returns_signal_from_strategy():
    """When candles are non-empty, the strategy signal passes through."""
    info = MagicMock()
    market = _make_market(open_now=True)
    strategy = _make_strategy(signal=Signal.BUY)

    fake_candles = pd.DataFrame({
        "time": [datetime(2026, 1, 1, tzinfo=timezone.utc)],
        "open": [100.0], "high": [105.0], "low": [99.0],
        "close": [103.0], "volume": [500.0],
    })

    with patch("scanner.fetch_candles", return_value=fake_candles):
        results = scan_universe(info, strategy, [market])

    assert len(results) == 1
    assert results[0].signal == Signal.BUY
    strategy.evaluate.assert_called_once_with(fake_candles)


def test_scan_universe_includes_strategy_name():
    """ScanResult.strategy_name must equal strategy.describe()."""
    info = MagicMock()
    market = _make_market(open_now=True)
    strategy = _make_strategy(signal=Signal.SELL)
    strategy.describe.return_value = "ema_crossover(fast=9,slow=21)"

    fake_candles = pd.DataFrame({
        "time": [datetime(2026, 1, 1, tzinfo=timezone.utc)],
        "open": [100.0], "high": [105.0], "low": [99.0],
        "close": [103.0], "volume": [500.0],
    })

    with patch("scanner.fetch_candles", return_value=fake_candles):
        results = scan_universe(info, strategy, [market])

    assert results[0].strategy_name == "ema_crossover(fast=9,slow=21)"


def test_scan_universe_multiple_markets():
    """2 open markets + 1 closed → 2 results, closed market absent."""
    info = MagicMock()
    open1 = _make_market(symbol="xyz:CL", open_now=True)
    open2 = _make_market(symbol="BTC", dex="", asset_class=AssetClass.CRYPTO, open_now=True)
    closed = _make_market(symbol="xyz:AAPL", open_now=False)
    strategy = _make_strategy(signal=Signal.HOLD)

    fake_candles = pd.DataFrame({
        "time": [datetime(2026, 1, 1, tzinfo=timezone.utc)],
        "open": [100.0], "high": [105.0], "low": [99.0],
        "close": [103.0], "volume": [500.0],
    })

    with patch("scanner.fetch_candles", return_value=fake_candles):
        results = scan_universe(info, strategy, [open1, open2, closed])

    assert len(results) == 2
    symbols = {r.market.symbol for r in results}
    assert symbols == {"xyz:CL", "BTC"}
    assert all(r.market.symbol != "xyz:AAPL" for r in results)


def test_scan_result_dataclass():
    """ScanResult must be a frozen dataclass — attribute assignment raises TypeError."""
    market = _make_market()
    now = datetime.now(timezone.utc)
    result = ScanResult(
        market=market,
        signal=Signal.HOLD,
        strategy_name="ema_crossover(fast=9,slow=21)",
        scanned_at=now,
    )

    assert result.signal == Signal.HOLD
    assert result.strategy_name == "ema_crossover(fast=9,slow=21)"
    assert result.scanned_at == now

    with pytest.raises((AttributeError, TypeError)):
        result.signal = Signal.BUY  # type: ignore[misc]
