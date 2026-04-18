"""Market scanner — applies a strategy (or class→strategy map) across the universe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from hype_bot import AssetClass, Info, TradableMarket, fetch_candles
from strategy import Signal, Strategy


@dataclass(frozen=True)
class ScanResult:
    market: TradableMarket
    signal: Signal
    strategy_name: str
    scanned_at: datetime


def _resolve_strategy(
    spec: Strategy | dict[AssetClass, Strategy],
    market: TradableMarket,
) -> Strategy:
    """Pick the strategy for a market. If spec is a dict and the class is
    missing, fall back to the first strategy in the dict.
    """
    if isinstance(spec, dict):
        if market.asset_class in spec:
            return spec[market.asset_class]
        return next(iter(spec.values()))
    return spec


def scan_universe(
    info: Info,
    strategy: Strategy | dict[AssetClass, Strategy],
    markets: list[TradableMarket],
    interval: str = "1h",
    lookback_hours: int = 48,
) -> list[ScanResult]:
    """Apply a strategy to each market in the given list.

    `strategy` may be a single Strategy (applied to every market) or a dict
    mapping AssetClass → Strategy (applied per market class).

    Skips markets where market.open_now is False.
    Returns HOLD for markets where candle data is empty or insufficient.
    """
    results: list[ScanResult] = []
    now = datetime.now(timezone.utc)

    for market in markets:
        if not market.open_now:
            continue

        s = _resolve_strategy(strategy, market)
        candles = fetch_candles(info, market.symbol, interval, lookback_hours)

        if candles.empty:
            signal = Signal.HOLD
        else:
            signal = s.evaluate(candles)

        results.append(ScanResult(
            market=market,
            signal=signal,
            strategy_name=s.describe(),
            scanned_at=now,
        ))

    return results
