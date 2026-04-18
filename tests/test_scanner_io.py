"""Tests for scanner_io.py — Phase 5D."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hype_bot import AssetClass, TradableMarket
from live_state import SessionState
from scanner import ScanResult
from scanner_io import (
    build_state,
    read_scanner_state,
    state_age_seconds,
    write_scanner_state,
)
from strategy import Signal


def _mkt(symbol: str = "BTC") -> TradableMarket:
    return TradableMarket(
        dex="", symbol=symbol, asset_class=AssetClass.CRYPTO,
        max_leverage=20, size_decimals=5, min_notional=10.0,
        current_mid=100.0, open_now=True,
    )


def _result(symbol: str = "BTC", signal: Signal = Signal.HOLD) -> ScanResult:
    return ScanResult(
        market=_mkt(symbol), signal=signal,
        strategy_name="ema(9/21)",
        scanned_at=datetime.now(timezone.utc),
    )


# --- build_state ---------------------------------------------------------

def test_build_state_flat_session():
    state = SessionState(coin="")
    snap = build_state("dry-run", "ema(9/21)", state, [_result("BTC", Signal.HOLD)], 5)

    assert snap["mode"] == "dry-run"
    assert snap["strategy"] == "ema(9/21)"
    assert snap["universe_size"] == 5
    assert snap["session"]["is_holding"] is False
    assert snap["session"]["position"] is None
    assert snap["session"]["trade_count"] == 0
    assert snap["session"]["session_pnl"] == 0.0
    assert len(snap["last_signals"]) == 1
    assert snap["last_signals"][0]["symbol"] == "BTC"
    assert snap["last_signals"][0]["signal"] == "hold"


def test_build_state_with_position():
    state = SessionState(coin="BTC")
    state.open_position("BTC", entry_price=100.0, size=0.1, notional=10.0)

    snap = build_state("dry-run", "ema", state, [], 9)
    assert snap["session"]["is_holding"] is True
    assert snap["session"]["position"]["coin"] == "BTC"
    assert snap["session"]["position"]["entry_price"] == 100.0
    assert snap["session"]["trade_count"] == 1


def test_build_state_is_json_serializable():
    state = SessionState(coin="BTC")
    state.open_position("BTC", 100.0, 0.1, 10.0)
    snap = build_state("live", "rsi", state, [_result("ETH", Signal.BUY)], 3)
    # Must round-trip through json
    s = json.dumps(snap, default=str)
    assert json.loads(s) is not None


def test_build_state_keeps_last_20_trades():
    state = SessionState(coin="BTC")
    for i in range(30):
        state.trades.append({"side": "BUY", "index": i})
    snap = build_state("dry-run", "ema", state, [], 1)
    assert len(snap["session"]["trades"]) == 20
    # Should be the LAST 20
    assert snap["session"]["trades"][0]["index"] == 10
    assert snap["session"]["trades"][-1]["index"] == 29


# --- write/read roundtrip ------------------------------------------------

def test_write_and_read_roundtrip(tmp_path: Path):
    path = tmp_path / ".scanner-state.json"
    state = SessionState(coin="")
    snap = build_state("dry-run", "ema", state, [_result("BTC")], 1)
    write_scanner_state(snap, path=path)

    assert path.exists()
    loaded = read_scanner_state(path=path)
    assert loaded is not None
    assert loaded["mode"] == "dry-run"
    assert loaded["strategy"] == "ema"


def test_read_missing_returns_none(tmp_path: Path):
    assert read_scanner_state(path=tmp_path / "nope.json") is None


def test_read_invalid_json_returns_none(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json")
    assert read_scanner_state(path=path) is None


def test_write_is_atomic_leaves_no_tmp(tmp_path: Path):
    path = tmp_path / ".scanner-state.json"
    snap = build_state("dry-run", "ema", SessionState(coin=""), [], 1)
    write_scanner_state(snap, path=path)
    # No .tmp leftovers in dir
    tmps = list(tmp_path.glob(".scanner-state.*.tmp"))
    assert tmps == []


# --- state_age_seconds ---------------------------------------------------

def test_state_age_recent():
    now = datetime.now(timezone.utc)
    snap = {"tick_at": now.isoformat()}
    age = state_age_seconds(snap)
    assert age is not None
    assert 0 <= age < 5


def test_state_age_old():
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    snap = {"tick_at": old.isoformat()}
    age = state_age_seconds(snap)
    assert age is not None
    assert age > 3600


def test_state_age_missing_returns_none():
    assert state_age_seconds({}) is None


def test_state_age_bad_format_returns_none():
    assert state_age_seconds({"tick_at": "not-a-date"}) is None
