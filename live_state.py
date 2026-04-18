"""Live trading session state — position tracking, candle freshness, order sizing."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from execution import ExecutionConfig
from strategy import Signal


@dataclass(frozen=True)
class LivePosition:
    """Immutable snapshot of current position."""
    coin: str
    entry_price: float
    size: float          # positive = long
    notional: float
    opened_at: datetime


@dataclass
class SessionState:
    """Mutable session tracking. One instance per run_live invocation.

    Intentionally mutable (not frozen) — session state changes on every
    trade. This deviates from the project's immutability-first principle
    but is appropriate for a single-threaded synchronous loop.
    """
    coin: str
    is_holding: bool = False
    position: LivePosition | None = None
    trades: list[dict] = field(default_factory=list)
    session_pnl: float = 0.0
    trade_count: int = 0
    leverage_set: bool = False
    last_signal_candle_time: datetime | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def open_position(self, coin: str, entry_price: float, size: float, notional: float) -> None:
        """Transition from flat to holding."""
        self.position = LivePosition(
            coin=coin,
            entry_price=entry_price,
            size=size,
            notional=notional,
            opened_at=datetime.now(timezone.utc),
        )
        self.is_holding = True
        self.trade_count += 1
        self.trades.append({
            "side": "BUY",
            "coin": coin,
            "price": entry_price,
            "size": size,
            "notional": notional,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def close_position(self, exit_price: float, proceeds: float) -> float:
        """Transition from holding to flat. Returns realized PnL."""
        if self.position is None:
            return 0.0
        cost = self.position.notional
        pnl = proceeds - cost
        self.session_pnl += pnl
        self.trade_count += 1
        self.trades.append({
            "side": "SELL",
            "coin": self.position.coin,
            "price": exit_price,
            "size": self.position.size,
            "notional": proceeds,
            "pnl": pnl,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.position = None
        self.is_holding = False
        return pnl

    def sync_from_exchange(self, user_state: dict, coin: str) -> str:
        """Reconcile local state with exchange ground truth.

        Returns a status string: "synced", "opened_externally", "closed_externally", "no_change".
        """
        positions = user_state.get("assetPositions", [])
        exchange_pos = None
        for p in positions:
            pos = p.get("position", {})
            if pos.get("coin") == coin:
                szi = float(pos.get("szi", "0"))
                if abs(szi) > 0:
                    exchange_pos = pos
                break

        if exchange_pos is not None and not self.is_holding:
            # Exchange has position, we don't — opened externally or state lost
            szi = float(exchange_pos.get("szi", "0"))
            entry = float(exchange_pos.get("entryPx", "0"))
            self.position = LivePosition(
                coin=coin,
                entry_price=entry,
                size=abs(szi),
                notional=abs(szi) * entry,
                opened_at=datetime.now(timezone.utc),
            )
            self.is_holding = True
            return "opened_externally"

        if exchange_pos is None and self.is_holding:
            # We think we're holding, but exchange says flat — closed externally or liquidated
            self.position = None
            self.is_holding = False
            return "closed_externally"

        if exchange_pos is not None and self.is_holding:
            # Both agree we're holding — update position details from exchange
            szi = float(exchange_pos.get("szi", "0"))
            entry = float(exchange_pos.get("entryPx", "0"))
            self.position = LivePosition(
                coin=coin,
                entry_price=entry,
                size=abs(szi),
                notional=abs(szi) * entry,
                opened_at=self.position.opened_at if self.position else datetime.now(timezone.utc),
            )
            return "synced"

        # Both agree we're flat
        return "no_change"

    def summary(self) -> str:
        """Human-readable session summary."""
        elapsed = datetime.now(timezone.utc) - self.started_at
        hours = elapsed.total_seconds() / 3600
        pos_str = f"HOLDING {self.position.coin} entry=${self.position.entry_price:,.2f} size={self.position.size}" if self.is_holding and self.position else "FLAT"
        return (
            f"Session: {hours:.1f}h | Trades: {self.trade_count} | "
            f"PnL: ${self.session_pnl:+.4f} | Position: {pos_str}"
        )


def check_candle_freshness(
    candles: pd.DataFrame,
    interval_seconds: int,
    max_staleness_factor: float = 1.5,
) -> tuple[bool, str]:
    """Check if candle data is fresh enough to trade on.

    Returns (is_fresh, reason).
    Rejects if the last candle's timestamp is older than max_staleness_factor * interval_seconds.
    """
    if candles.empty:
        return False, "No candle data"

    last_candle_time = candles["time"].iloc[-1]
    if not hasattr(last_candle_time, 'timestamp'):
        # pandas Timestamp
        last_candle_time = last_candle_time.to_pydatetime()
    if last_candle_time.tzinfo is None:
        last_candle_time = last_candle_time.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    age_seconds = (now - last_candle_time).total_seconds()
    max_age = interval_seconds * max_staleness_factor

    if age_seconds > max_age:
        return False, f"Candles stale: last={last_candle_time.isoformat()}, age={age_seconds:.0f}s > max={max_age:.0f}s"

    return True, f"Fresh: age={age_seconds:.0f}s <= max={max_age:.0f}s"


def compute_order_params(
    capital: float,
    mid_price: float,
    sz_decimals: int,
    config: ExecutionConfig,
) -> tuple[float, float]:
    """Compute (notional, size) for an order respecting ExecutionConfig caps.

    Notional = capital * 25% * max_leverage, capped at max_notional_usd.
    Size = notional / mid_price, rounded down to sz_decimals.
    """
    notional = capital * 0.25 * config.max_leverage
    if notional < config.min_notional_usd:
        notional = config.min_notional_usd
    if notional > config.max_notional_usd:
        notional = config.max_notional_usd

    raw_size = notional / mid_price
    factor = 10 ** sz_decimals
    size = math.floor(raw_size * factor) / factor

    return notional, size


def is_duplicate_signal(
    signal: Signal,
    candle_time: datetime,
    last_signal_candle_time: datetime | None,
) -> bool:
    """Return True if this signal is on the same candle as the last acted-on signal."""
    if signal == Signal.HOLD:
        return False  # HOLD is never a "duplicate" — it's a no-op
    if last_signal_candle_time is None:
        return False
    return candle_time == last_signal_candle_time
