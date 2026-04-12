"""Execution safety guards — leverage override and trade validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hype_bot import TradableMarket
from strategy import Signal


@dataclass(frozen=True)
class ExecutionConfig:
    """Safety-first execution configuration.

    Hard caps to protect a small learning account (~$50).
    """
    max_leverage: int = 3
    max_notional_usd: float = 15.0
    min_notional_usd: float = 10.0
    is_cross: bool = True


def validate_trade(
    signal: Signal,
    market: TradableMarket,
    capital: float,
    config: ExecutionConfig = ExecutionConfig(),
) -> tuple[bool, str]:
    """Pre-flight check before any trade.

    Returns (is_valid, reason).
    """
    if signal == Signal.HOLD:
        return False, "Signal is HOLD — no trade needed"

    if not market.open_now:
        return False, f"Market {market.symbol} is closed"

    notional = capital * 0.25 * config.max_leverage  # 25% position size at max leverage

    if notional < config.min_notional_usd:
        return False, f"Notional ${notional:.2f} below minimum ${config.min_notional_usd:.2f}"

    if notional > config.max_notional_usd:
        notional = config.max_notional_usd  # cap it

    margin_required = notional / config.max_leverage
    if margin_required > capital:
        return False, f"Insufficient capital: need ${margin_required:.2f}, have ${capital:.2f}"

    return True, f"Valid: {signal.value} {market.symbol}, notional=${notional:.2f}"


def set_leverage_if_needed(
    exchange: Any,  # hyperliquid.exchange.Exchange
    coin: str,
    target_leverage: int,
    is_cross: bool = True,
) -> dict:
    """Set leverage for a coin via exchange.update_leverage().

    This is an L1 action — the agent wallet CAN sign it.
    SDK signature: exchange.update_leverage(leverage: int, name: str, is_cross: bool)

    Should be called ONCE per coin before the first trade, not on every signal.
    """
    return exchange.update_leverage(target_leverage, coin, is_cross)
