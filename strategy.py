"""Strategy framework — Signal enum, StrategyConfig base, Strategy Protocol."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd

from hype_bot import AssetClass


class Signal(enum.Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class StrategyConfig:
    """Base config. Concrete strategies should override name with a sensible default."""
    name: str = "unnamed"
    asset_class: AssetClass | None = None


@runtime_checkable
class Strategy(Protocol):
    config: StrategyConfig

    def evaluate(self, candles: pd.DataFrame) -> Signal: ...
    def describe(self) -> str: ...
