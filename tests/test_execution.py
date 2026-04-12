"""Tests for execution.py — Phase 4D safety guards."""

from __future__ import annotations

from unittest.mock import MagicMock

from hype_bot import AssetClass, TradableMarket
from strategy import Signal
from execution import ExecutionConfig, validate_trade, set_leverage_if_needed


def _make_market(open_now: bool = True) -> TradableMarket:
    return TradableMarket(
        dex="",
        symbol="BTC",
        asset_class=AssetClass.CRYPTO,
        max_leverage=50,
        size_decimals=5,
        min_notional=10.0,
        current_mid=84000.0,
        open_now=open_now,
    )


def test_validate_rejects_hold() -> None:
    market = _make_market()
    valid, reason = validate_trade(Signal.HOLD, market, capital=50.0)
    assert valid is False
    assert "HOLD" in reason


def test_validate_rejects_closed_market() -> None:
    market = _make_market(open_now=False)
    valid, reason = validate_trade(Signal.BUY, market, capital=50.0)
    assert valid is False
    assert "closed" in reason


def test_validate_rejects_insufficient_capital() -> None:
    # capital=1.0 -> notional = 1.0 * 0.25 * 3 = 0.75, below min_notional=10.0
    market = _make_market()
    valid, reason = validate_trade(Signal.BUY, market, capital=1.0)
    assert valid is False
    assert "below minimum" in reason


def test_validate_accepts_valid_buy() -> None:
    market = _make_market()
    valid, reason = validate_trade(Signal.BUY, market, capital=50.0)
    assert valid is True
    assert "buy" in reason
    assert "BTC" in reason


def test_validate_accepts_valid_sell() -> None:
    market = _make_market()
    valid, reason = validate_trade(Signal.SELL, market, capital=50.0)
    assert valid is True
    assert "sell" in reason
    assert "BTC" in reason


def test_config_defaults() -> None:
    config = ExecutionConfig()
    assert config.max_leverage == 3
    assert config.max_notional_usd == 15.0
    assert config.min_notional_usd == 10.0
    assert config.is_cross is True


def test_set_leverage_calls_exchange() -> None:
    exchange = MagicMock()
    exchange.update_leverage.return_value = {"status": "ok"}

    result = set_leverage_if_needed(exchange, coin="BTC", target_leverage=3, is_cross=True)

    exchange.update_leverage.assert_called_once_with(3, "BTC", True)
    assert result == {"status": "ok"}
