"""Walk-forward backtest engine for strategy evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

import pandas as pd

from strategy import Signal, Strategy


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 50.0
    leverage: int = 3
    position_size_pct: float = 0.25
    fee_rate: float = 0.00035      # Hyperliquid taker: 0.035%
    slippage_bps: float = 5.0      # 5 basis points
    min_notional: float = 10.0


@dataclass(frozen=True)
class Trade:
    timestamp: datetime
    side: str           # "BUY" or "SELL"
    price: float
    size: float
    fee: float
    notional: float


@dataclass(frozen=True)
class BacktestResult:
    trades: tuple[Trade, ...]       # use tuple for frozen compatibility
    equity_curve: tuple[float, ...]
    total_return_pct: float
    max_drawdown_pct: float
    win_rate: float
    total_trades: int
    sharpe_proxy: float
    total_fees: float


def run_backtest(
    strategy: Strategy,
    candles: pd.DataFrame,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Walk-forward backtest of *strategy* over *candles*.

    Long-only: BUY opens a position, SELL closes it.
    Position size is fixed from initial capital (no compounding).
    Fees and slippage are applied on every fill.

    Parameters
    ----------
    strategy:
        Any object satisfying the Strategy Protocol.
    candles:
        DataFrame with at minimum a ``close`` column, sorted ascending.
    config:
        BacktestConfig; uses defaults when None.

    Returns
    -------
    BacktestResult with trade log, equity curve, and summary metrics.
    """
    if config is None:
        config = BacktestConfig()

    # Determine warm-up: use strategy.config.slow_period when available.
    warm_up: int
    try:
        warm_up = int(strategy.config.slow_period)  # type: ignore[attr-defined]
    except AttributeError:
        warm_up = 21

    # Fixed notional per trade — no compounding.
    notional = config.initial_capital * config.position_size_pct * config.leverage
    if notional < config.min_notional:
        notional = config.min_notional

    slippage_factor = config.slippage_bps / 10_000.0

    cash: float = config.initial_capital
    holding: bool = False
    position_size: float = 0.0
    entry_price: float = 0.0
    trades: list[Trade] = []
    equity_curve: list[float] = []
    total_fees: float = 0.0

    n = len(candles)

    for i in range(warm_up, n):
        window = candles.iloc[: i + 1]
        close = float(candles["close"].iloc[i])
        ts: datetime = candles["time"].iloc[i]

        signal = strategy.evaluate(window)

        if signal == Signal.BUY and not holding:
            buy_price = close * (1.0 + slippage_factor)
            size = notional / buy_price
            fee = notional * config.fee_rate
            cash -= notional + fee
            position_size = size
            entry_price = buy_price
            holding = True
            total_fees += fee
            trades.append(Trade(
                timestamp=ts,
                side="BUY",
                price=buy_price,
                size=size,
                fee=fee,
                notional=notional,
            ))

        elif signal == Signal.SELL and holding:
            sell_price = close * (1.0 - slippage_factor)
            sell_notional = position_size * sell_price
            fee = sell_notional * config.fee_rate
            cash += sell_notional - fee
            holding = False
            total_fees += fee
            trades.append(Trade(
                timestamp=ts,
                side="SELL",
                price=sell_price,
                size=position_size,
                fee=fee,
                notional=sell_notional,
            ))
            position_size = 0.0
            entry_price = 0.0

        # Mark-to-market equity.
        position_value = position_size * close if holding else 0.0
        equity_curve.append(cash + position_value)

    # Force-close any open position at the last bar's close.
    if holding and n > warm_up:
        last_close = float(candles["close"].iloc[-1])
        last_ts: datetime = candles["time"].iloc[-1]
        sell_price = last_close * (1.0 - slippage_factor)
        sell_notional = position_size * sell_price
        fee = sell_notional * config.fee_rate
        cash += sell_notional - fee
        total_fees += fee
        trades.append(Trade(
            timestamp=last_ts,
            side="SELL",
            price=sell_price,
            size=position_size,
            fee=fee,
            notional=sell_notional,
        ))
        # Update last equity point to reflect the close.
        if equity_curve:
            equity_curve[-1] = cash

    # --- Metrics ---

    final_equity = cash if equity_curve else config.initial_capital
    total_return_pct = (final_equity - config.initial_capital) / config.initial_capital * 100.0

    # Max drawdown: peak-to-trough over the equity curve.
    max_drawdown_pct = 0.0
    if equity_curve:
        peak = equity_curve[0]
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            if peak > 0:
                dd = (peak - eq) / peak * 100.0
                if dd > max_drawdown_pct:
                    max_drawdown_pct = dd

    # Win rate: fraction of round-trips (BUY→SELL pairs) that were profitable.
    win_rate = 0.0
    buy_trades = [t for t in trades if t.side == "BUY"]
    sell_trades = [t for t in trades if t.side == "SELL"]
    pairs = list(zip(buy_trades, sell_trades))
    if pairs:
        wins = sum(1 for b, s in pairs if s.price > b.price)
        win_rate = wins / len(pairs)

    # Sharpe proxy: annualised return / annualised vol of bar-over-bar equity returns.
    sharpe_proxy = 0.0
    if len(equity_curve) >= 2:
        returns = [
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
            if equity_curve[i - 1] != 0
        ]
        if returns:
            n_ret = len(returns)
            mean_ret = sum(returns) / n_ret
            variance = sum((r - mean_ret) ** 2 for r in returns) / n_ret
            std_ret = math.sqrt(variance) if variance > 0 else 0.0
            # Annualise assuming hourly bars (8760 hours/year).
            bars_per_year = 8760
            ann_return = mean_ret * bars_per_year
            ann_vol = std_ret * math.sqrt(bars_per_year)
            if ann_vol > 0:
                sharpe_proxy = ann_return / ann_vol

    return BacktestResult(
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        win_rate=win_rate,
        total_trades=len(trades),
        sharpe_proxy=sharpe_proxy,
        total_fees=total_fees,
    )
