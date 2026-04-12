"""Market scanner — applies a strategy across the multi-dex universe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from hype_bot import Info, TradableMarket, fetch_candles
from strategy import Signal, Strategy


@dataclass(frozen=True)
class ScanResult:
    market: TradableMarket
    signal: Signal
    strategy_name: str
    scanned_at: datetime


def scan_universe(
    info: Info,
    strategy: Strategy,
    markets: list[TradableMarket],
    interval: str = "1h",
    lookback_hours: int = 48,
) -> list[ScanResult]:
    """Apply a strategy to each market in the given list.

    Skips markets where market.open_now is False.
    Returns HOLD for markets where candle data is empty or insufficient.
    """
    results: list[ScanResult] = []
    now = datetime.now(timezone.utc)

    for market in markets:
        if not market.open_now:
            continue

        candles = fetch_candles(info, market.symbol, interval, lookback_hours)

        if candles.empty:
            signal = Signal.HOLD
        else:
            signal = strategy.evaluate(candles)

        results.append(ScanResult(
            market=market,
            signal=signal,
            strategy_name=strategy.describe(),
            scanned_at=now,
        ))

    return results
