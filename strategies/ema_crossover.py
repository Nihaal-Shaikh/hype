"""EMA crossover strategy — golden cross / death cross signal generator."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from hype_bot import AssetClass
from strategy import Signal, StrategyConfig


@dataclass(frozen=True)
class EmaCrossoverConfig(StrategyConfig):
    """Config for the EMA crossover strategy.

    Asset-class suggested defaults:
      CRYPTO:    fast=9,  slow=21
      COMMODITY: fast=12, slow=26
      STOCK:     fast=10, slow=30
    """
    name: str = field(default="ema_crossover")
    fast_period: int = 9
    slow_period: int = 21


class EmaCrossover:
    """EMA crossover strategy.

    Emits BUY on a golden cross (fast EMA crosses above slow EMA) and
    SELL on a death cross (fast EMA crosses below slow EMA).  Returns
    HOLD in all other conditions, including when there is insufficient
    data to compute both EMAs.
    """

    # Suggested configs per asset class — instantiate with these as a
    # starting point, then override any field as needed.
    CRYPTO = EmaCrossoverConfig(fast_period=9, slow_period=21,
                                asset_class=AssetClass.CRYPTO)
    COMMODITY = EmaCrossoverConfig(fast_period=12, slow_period=26,
                                   asset_class=AssetClass.COMMODITY)
    STOCK = EmaCrossoverConfig(fast_period=10, slow_period=30,
                               asset_class=AssetClass.STOCK)

    def __init__(self, config: EmaCrossoverConfig | None = None) -> None:
        self.config: EmaCrossoverConfig = config or EmaCrossoverConfig()

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        """Return a Signal based on the last two bars of EMA data.

        Parameters
        ----------
        candles:
            DataFrame with at minimum a ``close`` column.  Expected columns:
            time, open, high, low, close, volume.

        Returns
        -------
        Signal.BUY   — golden cross on the last two bars
        Signal.SELL  — death cross on the last two bars
        Signal.HOLD  — insufficient data or no crossover
        """
        if len(candles) < self.config.slow_period:
            return Signal.HOLD

        close = candles["close"]
        fast = close.ewm(span=self.config.fast_period, adjust=False).mean()
        slow = close.ewm(span=self.config.slow_period, adjust=False).mean()

        f_prev, f_last = float(fast.iloc[-2]), float(fast.iloc[-1])
        s_prev, s_last = float(slow.iloc[-2]), float(slow.iloc[-1])

        if f_last > s_last and f_prev <= s_prev:
            return Signal.BUY
        if f_last < s_last and f_prev >= s_prev:
            return Signal.SELL
        return Signal.HOLD

    def describe(self) -> str:
        """Return a human-readable description of this strategy instance."""
        return (
            f"{self.config.name}"
            f"({self.config.fast_period}/{self.config.slow_period})"
        )
