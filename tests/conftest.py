"""Shared test fixtures for Phase 4+ strategy testing."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pandas as pd


def make_candles(
    trend: str = "up",
    n: int = 100,
    start_price: float = 100.0,
    volatility: float = 0.02,
) -> pd.DataFrame:
    """Generate synthetic candle data for testing.

    Args:
        trend: "up" (steady rise), "down" (steady fall), or "sideways" (flat with noise)
        n: number of candles
        start_price: initial close price
        volatility: noise factor (fraction of price per bar)

    Returns:
        DataFrame with columns: time, open, high, low, close, volume
        Sorted ascending by time. Same schema as dashboard.py / hype_bot.fetch_candles().
    """
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    times = [base + timedelta(hours=i) for i in range(n)]

    closes = []
    price = start_price
    for i in range(n):
        if trend == "up":
            price *= 1 + volatility * 0.3  # steady upward drift
        elif trend == "down":
            price *= 1 - volatility * 0.3  # steady downward drift
        else:  # sideways
            # Alternate up/down to stay roughly flat
            price *= 1 + volatility * 0.1 * (1 if i % 2 == 0 else -1)
        closes.append(price)

    # Build OHLCV from closes
    rows = []
    for i, (t, c) in enumerate(zip(times, closes)):
        noise = c * volatility * 0.5
        o = c - noise if trend == "up" else c + noise if trend == "down" else c
        h = max(o, c) + abs(noise)
        l = min(o, c) - abs(noise)
        v = 1000.0 + i * 10  # increasing volume
        rows.append({"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v})

    return pd.DataFrame(rows)
