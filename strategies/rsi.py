"""RSI mean-reversion strategy.

Signals when RSI crosses into oversold/overbought zones.
Classic interpretation: buy oversold (expect bounce), sell overbought
(expect pullback). Uses Wilder smoothing (standard RSI formula).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from hype_bot import AssetClass
from strategy import Signal, StrategyConfig


@dataclass(frozen=True)
class RsiConfig(StrategyConfig):
    """Config for the RSI mean-reversion strategy.

    Asset-class suggested defaults:
      CRYPTO:    period=14, oversold=30, overbought=70
      COMMODITY: period=14, oversold=25, overbought=75  (wider — slower assets)
      STOCK:     period=14, oversold=30, overbought=70
    """
    name: str = field(default="rsi")
    period: int = 14
    oversold: float = 30.0
    overbought: float = 70.0

    @property
    def slow_period(self) -> int:
        """Warm-up bars needed for backtest engine. 2x period for stable RSI."""
        return self.period * 2


def _compute_rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder-smoothed RSI. Returns Series aligned with `close`."""
    delta = close.diff()
    gains = delta.where(delta > 0, 0.0)
    losses = -delta.where(delta < 0, 0.0)
    # Wilder smoothing = EMA with alpha = 1/period
    avg_gain = gains.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100.0 - (100.0 / (1.0 + rs))


class Rsi:
    """RSI mean-reversion strategy.

    BUY  when RSI crosses DOWN into oversold   (prev >= oversold, curr < oversold)
    SELL when RSI crosses UP into overbought   (prev <= overbought, curr > overbought)
    HOLD otherwise
    """

    CRYPTO = RsiConfig(period=14, oversold=30.0, overbought=70.0,
                       asset_class=AssetClass.CRYPTO)
    COMMODITY = RsiConfig(period=14, oversold=25.0, overbought=75.0,
                          asset_class=AssetClass.COMMODITY)
    STOCK = RsiConfig(period=14, oversold=30.0, overbought=70.0,
                      asset_class=AssetClass.STOCK)

    def __init__(self, config: RsiConfig | None = None) -> None:
        self.config: RsiConfig = config or RsiConfig()

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < self.config.slow_period:
            return Signal.HOLD

        rsi = _compute_rsi(candles["close"], self.config.period)
        prev = float(rsi.iloc[-2])
        curr = float(rsi.iloc[-1])

        if prev >= self.config.oversold and curr < self.config.oversold:
            return Signal.BUY
        if prev <= self.config.overbought and curr > self.config.overbought:
            return Signal.SELL
        return Signal.HOLD

    def describe(self) -> str:
        return (
            f"{self.config.name}"
            f"({self.config.period},{int(self.config.oversold)}/{int(self.config.overbought)})"
        )
