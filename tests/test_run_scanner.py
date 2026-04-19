"""Tests for run_scanner.py — Phase 5B."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from execution import ExecutionConfig
from hype_bot import AssetClass, TradableMarket
from live_state import SessionState
from run_scanner import (
    CURATED_UNIVERSE,
    _execute_buy,
    _execute_sell,
    _find_sell_for,
    _pick_buy,
    _run_one_tick,
    build_universe,
)
from scanner import ScanResult
from strategy import Signal


def _mkt(symbol: str = "BTC", dex: str = "", ac: AssetClass = AssetClass.CRYPTO,
         mid: float = 100.0, open_now: bool = True) -> TradableMarket:
    return TradableMarket(
        dex=dex, symbol=symbol, asset_class=ac,
        max_leverage=20, size_decimals=5, min_notional=10.0,
        current_mid=mid, open_now=open_now,
    )


def _result(market: TradableMarket, signal: Signal) -> ScanResult:
    return ScanResult(market=market, signal=signal,
                      strategy_name="ema(9/21)",
                      scanned_at=datetime.now(timezone.utc))


# --- picker helpers ------------------------------------------------------

def test_pick_buy_returns_first():
    a = _result(_mkt("A"), Signal.HOLD)
    b = _result(_mkt("B"), Signal.BUY)
    c = _result(_mkt("C"), Signal.BUY)
    assert _pick_buy([a, b, c]).market.symbol == "B"


def test_pick_buy_none_when_no_buys():
    a = _result(_mkt("A"), Signal.HOLD)
    b = _result(_mkt("B"), Signal.SELL)
    assert _pick_buy([a, b]) is None


def test_find_sell_for_matches_coin():
    a = _result(_mkt("BTC"), Signal.SELL)
    b = _result(_mkt("ETH"), Signal.SELL)
    assert _find_sell_for([a, b], "ETH").market.symbol == "ETH"
    assert _find_sell_for([a, b], "SOL") is None


def test_find_sell_for_ignores_non_sell():
    a = _result(_mkt("BTC"), Signal.BUY)
    assert _find_sell_for([a], "BTC") is None


# --- build_universe ------------------------------------------------------

def test_curated_universe_has_20_tickers():
    """Phase 6A R2: universe expanded to 20 tickers for news-bot coverage."""
    assert len(CURATED_UNIVERSE) == 20
    # No duplicates
    assert len(set(CURATED_UNIVERSE)) == 20
    # Every entry is a (dex, symbol) tuple of strings
    for entry in CURATED_UNIVERSE:
        assert isinstance(entry, tuple)
        assert len(entry) == 2
        dex, sym = entry
        assert isinstance(dex, str)
        assert isinstance(sym, str) and sym


def test_build_universe_curated_default():
    info = MagicMock()
    calls = []

    def fake_gtm(i, dex, sym):
        calls.append((dex, sym))
        return _mkt(symbol=sym, dex=dex)

    with patch("run_scanner.get_tradable_market", side_effect=fake_gtm):
        markets = build_universe(info)
    assert len(markets) == len(CURATED_UNIVERSE)
    assert [(m.dex, m.symbol) for m in markets] == list(CURATED_UNIVERSE)


def test_build_universe_skips_failures():
    info = MagicMock()

    def fake_gtm(i, dex, sym):
        if sym == "xyz:CL":
            raise ValueError("meta missing")
        return _mkt(symbol=sym, dex=dex)

    with patch("run_scanner.get_tradable_market", side_effect=fake_gtm):
        markets = build_universe(info, symbols=[("", "BTC"), ("xyz", "xyz:CL"), ("", "ETH")])
    assert [m.symbol for m in markets] == ["BTC", "ETH"]


# --- _execute_buy (dry-run) ----------------------------------------------

def test_execute_buy_dry_run_opens_position():
    state = SessionState(coin="")
    config = ExecutionConfig()
    logger = logging.getLogger("test")
    result = _result(_mkt("BTC", mid=100.0), Signal.BUY)

    _execute_buy(MagicMock(), None, result, state, config, is_live=False, logger=logger)

    assert state.is_holding is True
    assert state.position is not None
    assert state.position.coin == "BTC"
    assert state.coin == "BTC"


def test_execute_buy_skips_when_no_mid():
    state = SessionState(coin="")
    config = ExecutionConfig()
    logger = logging.getLogger("test")
    market = _mkt("BTC")
    market_no_mid = TradableMarket(
        dex=market.dex, symbol=market.symbol, asset_class=market.asset_class,
        max_leverage=market.max_leverage, size_decimals=market.size_decimals,
        min_notional=market.min_notional, current_mid=None, open_now=market.open_now,
    )
    result = _result(market_no_mid, Signal.BUY)

    _execute_buy(MagicMock(), None, result, state, config, is_live=False, logger=logger)

    assert state.is_holding is False


def test_execute_buy_skips_when_market_closed():
    state = SessionState(coin="")
    config = ExecutionConfig()
    logger = logging.getLogger("test")
    result = _result(_mkt("BTC", open_now=False), Signal.BUY)

    _execute_buy(MagicMock(), None, result, state, config, is_live=False, logger=logger)

    assert state.is_holding is False


# --- _execute_sell (dry-run) ---------------------------------------------

def test_execute_sell_dry_run_closes_position():
    state = SessionState(coin="BTC")
    state.open_position("BTC", entry_price=100.0, size=0.1, notional=10.0)
    assert state.is_holding is True

    logger = logging.getLogger("test")
    result = _result(_mkt("BTC", mid=110.0), Signal.SELL)

    _execute_sell(MagicMock(), None, result, state, is_live=False, logger=logger)

    assert state.is_holding is False
    assert state.session_pnl > 0  # sold 110 vs bought 100 = profit


# --- _run_one_tick integration (dry-run) ---------------------------------

def test_tick_flat_picks_first_buy():
    state = SessionState(coin="")
    config = ExecutionConfig()
    logger = logging.getLogger("test")
    markets = [_mkt("BTC"), _mkt("ETH")]

    fake_results = [
        _result(markets[0], Signal.HOLD),
        _result(markets[1], Signal.BUY),
    ]

    with patch("run_scanner.scan_universe", return_value=fake_results):
        _run_one_tick(
            info=MagicMock(), exchange=None, strategy=MagicMock(),
            state=state, config=config, markets=markets,
            interval="1h", lookback_hours=48, is_live=False, logger=logger,
        )

    assert state.is_holding is True
    assert state.position.coin == "ETH"


def test_tick_holding_ignores_new_buys():
    state = SessionState(coin="BTC")
    state.open_position("BTC", entry_price=100.0, size=0.1, notional=10.0)
    config = ExecutionConfig()
    logger = logging.getLogger("test")
    markets = [_mkt("ETH")]

    fake_results = [_result(markets[0], Signal.BUY)]

    with patch("run_scanner.scan_universe", return_value=fake_results):
        _run_one_tick(
            info=MagicMock(), exchange=None, strategy=MagicMock(),
            state=state, config=config, markets=markets,
            interval="1h", lookback_hours=48, is_live=False, logger=logger,
        )

    # Still holding BTC, did NOT open ETH
    assert state.position.coin == "BTC"


def test_tick_holding_sells_on_sell_signal():
    state = SessionState(coin="BTC")
    state.open_position("BTC", entry_price=100.0, size=0.1, notional=10.0)
    config = ExecutionConfig()
    logger = logging.getLogger("test")
    markets = [_mkt("BTC", mid=110.0)]

    fake_results = [_result(markets[0], Signal.SELL)]

    with patch("run_scanner.scan_universe", return_value=fake_results):
        _run_one_tick(
            info=MagicMock(), exchange=None, strategy=MagicMock(),
            state=state, config=config, markets=markets,
            interval="1h", lookback_hours=48, is_live=False, logger=logger,
        )

    assert state.is_holding is False
    assert state.session_pnl > 0


def test_tick_holds_when_no_sell_for_held_coin():
    state = SessionState(coin="BTC")
    state.open_position("BTC", entry_price=100.0, size=0.1, notional=10.0)
    config = ExecutionConfig()
    logger = logging.getLogger("test")
    markets = [_mkt("BTC"), _mkt("ETH")]

    # SELL on ETH (which we don't hold), HOLD on BTC
    fake_results = [
        _result(markets[0], Signal.HOLD),
        _result(markets[1], Signal.SELL),
    ]

    with patch("run_scanner.scan_universe", return_value=fake_results):
        _run_one_tick(
            info=MagicMock(), exchange=None, strategy=MagicMock(),
            state=state, config=config, markets=markets,
            interval="1h", lookback_hours=48, is_live=False, logger=logger,
        )

    assert state.is_holding is True
    assert state.position.coin == "BTC"


def test_tick_handles_no_signals():
    state = SessionState(coin="")
    config = ExecutionConfig()
    logger = logging.getLogger("test")
    markets = [_mkt("BTC")]

    fake_results = [_result(markets[0], Signal.HOLD)]

    with patch("run_scanner.scan_universe", return_value=fake_results):
        _run_one_tick(
            info=MagicMock(), exchange=None, strategy=MagicMock(),
            state=state, config=config, markets=markets,
            interval="1h", lookback_hours=48, is_live=False, logger=logger,
        )

    assert state.is_holding is False


def test_tick_catches_exceptions():
    state = SessionState(coin="")
    config = ExecutionConfig()
    logger = logging.getLogger("test")
    markets = [_mkt("BTC")]

    with patch("run_scanner.scan_universe", side_effect=RuntimeError("boom")):
        # Must not raise
        _run_one_tick(
            info=MagicMock(), exchange=None, strategy=MagicMock(),
            state=state, config=config, markets=markets,
            interval="1h", lookback_hours=48, is_live=False, logger=logger,
        )

    assert state.is_holding is False
