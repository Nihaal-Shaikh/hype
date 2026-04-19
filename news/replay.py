"""News replay engine for Phase 6A-prime edge check.

Given a list of `NewsPost`s and per-market candle data, simulate what
would have happened if a bot saw each post with realistic ingestion
delay and held a small position for a fixed window.

Metric: sum of per-trade returns, Sharpe, win-rate, max DD per market.
Fees + slippage modeled to match `backtest.BacktestConfig`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import pandas as pd

from news.mock_classifier import MockSignal, classify as classify_mock
from news.sources import NewsPost


@dataclass(frozen=True)
class ReplayTrade:
    post_id: str
    market: str
    rule_id: str
    sentiment: str
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    size: float
    notional: float
    fee_total: float
    gross_pnl: float     # before fees
    net_pnl: float       # after fees
    return_pct: float    # net_pnl / notional


@dataclass(frozen=True)
class MarketStats:
    market: str
    trades: int
    total_net_pnl: float
    win_rate: float
    mean_return_pct: float
    sharpe: float        # mean/std of per-trade return_pct, annualized is moot here
    max_drawdown_pct: float


def _ensure_utc(ts) -> datetime:
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _find_price_at(candles: pd.DataFrame, target: datetime) -> float | None:
    """Return close price of the first candle with `time >= target`."""
    if candles is None or candles.empty:
        return None
    series_ts = candles["time"]
    if pd.api.types.is_datetime64_any_dtype(series_ts):
        ts_utc = series_ts.dt.tz_localize("UTC") if series_ts.dt.tz is None else series_ts
    else:
        ts_utc = pd.to_datetime(series_ts, utc=True)
    mask = ts_utc >= pd.Timestamp(target)
    if not mask.any():
        return None
    return float(candles.loc[mask.idxmax(), "close"])


def replay_signals(
    posts: Iterable[NewsPost],
    candles_by_market: dict[str, pd.DataFrame],
    *,
    ingestion_delay_seconds: int = 60,
    hold_seconds: int = 30 * 60,     # 30-minute hold
    notional_usd: float = 10.0,
    fee_rate: float = 0.00035,       # Hyperliquid taker
    slippage_bps: float = 5.0,       # 5bp each side
) -> list[ReplayTrade]:
    """Classify each post, simulate a fixed-hold trade per emitted signal.

    BULLISH = long. BEARISH = short. NEUTRAL or unknown signals skipped.
    """
    slip_factor = slippage_bps / 10_000.0
    trades: list[ReplayTrade] = []

    sorted_posts = sorted(posts, key=lambda p: p.published_at)

    for post in sorted_posts:
        for signal in classify_mock(post):
            market = signal.market
            candles = candles_by_market.get(market)
            if candles is None or candles.empty:
                continue

            published_utc = _ensure_utc(post.published_at)
            entry_ts = published_utc + timedelta(seconds=ingestion_delay_seconds)
            exit_ts = entry_ts + timedelta(seconds=hold_seconds)

            entry_base = _find_price_at(candles, entry_ts)
            exit_base = _find_price_at(candles, exit_ts)
            if entry_base is None or exit_base is None:
                continue

            side = 1 if signal.sentiment == "BULLISH" else -1 if signal.sentiment == "BEARISH" else 0
            if side == 0:
                continue

            entry_px = entry_base * (1 + slip_factor * side)
            exit_px = exit_base * (1 - slip_factor * side)
            size = notional_usd / entry_px
            fee_entry = notional_usd * fee_rate
            fee_exit = (size * exit_px) * fee_rate
            fee_total = fee_entry + fee_exit

            gross = side * (exit_px - entry_px) * size
            net = gross - fee_total
            trades.append(ReplayTrade(
                post_id=post.post_id,
                market=market,
                rule_id=signal.rule_id,
                sentiment=signal.sentiment,
                entry_ts=entry_ts,
                exit_ts=exit_ts,
                entry_price=entry_px,
                exit_price=exit_px,
                size=size,
                notional=notional_usd,
                fee_total=fee_total,
                gross_pnl=gross,
                net_pnl=net,
                return_pct=(net / notional_usd) * 100.0,
            ))

    return trades


def summarize_by_market(trades: list[ReplayTrade]) -> list[MarketStats]:
    """Per-market aggregates for the go/no-go gate."""
    out: list[MarketStats] = []
    by_market: dict[str, list[ReplayTrade]] = {}
    for t in trades:
        by_market.setdefault(t.market, []).append(t)

    for market, ts in by_market.items():
        returns = [t.return_pct for t in ts]
        n = len(returns)
        if n == 0:
            continue
        mean_ret = sum(returns) / n
        variance = sum((r - mean_ret) ** 2 for r in returns) / n
        std = math.sqrt(variance) if variance > 0 else 0.0
        sharpe = mean_ret / std if std > 0 else 0.0
        wins = sum(1 for t in ts if t.net_pnl > 0)
        win_rate = wins / n

        equity = 0.0
        peak = 0.0
        max_dd_pct = 0.0
        for t in ts:
            equity += t.net_pnl
            peak = max(peak, equity)
            if peak > 0:
                dd = (peak - equity) / peak * 100.0
                max_dd_pct = max(max_dd_pct, dd)

        out.append(MarketStats(
            market=market,
            trades=n,
            total_net_pnl=sum(t.net_pnl for t in ts),
            win_rate=win_rate,
            mean_return_pct=mean_ret,
            sharpe=sharpe,
            max_drawdown_pct=max_dd_pct,
        ))
    return out
