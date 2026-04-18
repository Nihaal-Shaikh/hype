"""Scanner state IO — JSON state file shared between scanner and dashboard.

The scanner process writes `.scanner-state.json` at the end of every tick.
The dashboard reads it and renders a live panel.

Atomic write via tmp+rename so the dashboard never sees partial JSON.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from live_state import SessionState
from scanner import ScanResult


DEFAULT_STATE_PATH = Path(".scanner-state.json")


def _position_to_dict(session: SessionState) -> dict[str, Any] | None:
    if session.position is None:
        return None
    return {
        "coin": session.position.coin,
        "entry_price": session.position.entry_price,
        "size": session.position.size,
        "notional": session.position.notional,
        "opened_at": session.position.opened_at.isoformat(),
    }


def _result_to_dict(r: ScanResult) -> dict[str, Any]:
    return {
        "symbol": r.market.symbol,
        "dex": r.market.dex,
        "asset_class": r.market.asset_class.value,
        "signal": r.signal.value,
        "open_now": r.market.open_now,
        "current_mid": r.market.current_mid,
    }


def build_state(
    mode: str,
    strategy_name: str,
    session: SessionState,
    last_results: list[ScanResult],
    universe_size: int,
) -> dict[str, Any]:
    """Serialize current scanner + session state to a JSON-friendly dict."""
    now = datetime.now(timezone.utc)
    return {
        "tick_at": now.isoformat(),
        "mode": mode,
        "strategy": strategy_name,
        "universe_size": universe_size,
        "session": {
            "started_at": session.started_at.isoformat(),
            "trade_count": session.trade_count,
            "session_pnl": session.session_pnl,
            "is_holding": session.is_holding,
            "position": _position_to_dict(session),
            "trades": session.trades[-20:],  # keep last 20 trades
        },
        "last_signals": [_result_to_dict(r) for r in last_results],
    }


def write_scanner_state(state: dict[str, Any], path: Path = DEFAULT_STATE_PATH) -> None:
    """Atomic write: tmp file in same dir, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".scanner-state.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_scanner_state(path: Path = DEFAULT_STATE_PATH) -> dict[str, Any] | None:
    """Return parsed state dict, or None if file missing/invalid."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def state_age_seconds(state: dict[str, Any]) -> float | None:
    """Seconds since last tick, or None if tick_at missing/invalid."""
    tick_at = state.get("tick_at")
    if not tick_at:
        return None
    try:
        t = datetime.fromisoformat(tick_at)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds()
    except ValueError:
        return None
