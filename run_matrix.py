"""Backtest matrix — grid strategies × coins × windows, compare metrics.

Usage:
    python run_matrix.py [--coins BTC,ETH,SOL,HYPE]
                         [--strategies ema,rsi]
                         [--interval 1h]
                         [--days 7,14,30]
                         [--csv results.csv]
                         [--sort return|sharpe|win_rate|dd]

Fetches candles once per (coin, max-days) then runs every (strategy, days)
combo against that data. Prints a sorted table + optional CSV export.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from backtest import BacktestConfig, BacktestResult, run_backtest
from hype_bot import ACTIVE_DEXES, fetch_candles, make_info
from strategy import Strategy
from strategies.ema_crossover import EmaCrossover, EmaCrossoverConfig
from strategies.rsi import Rsi, RsiConfig


# ---------------------------------------------------------------------------
# Strategy factory
# ---------------------------------------------------------------------------

def build_strategy(name: str, fast: int = 9, slow: int = 21) -> Strategy:
    """Construct a Strategy instance from a short name."""
    n = name.strip().lower()
    if n == "ema":
        return EmaCrossover(EmaCrossoverConfig(fast_period=fast, slow_period=slow))
    if n == "rsi":
        return Rsi(RsiConfig())
    raise ValueError(f"Unknown strategy: {name!r} (expected 'ema' or 'rsi')")


# ---------------------------------------------------------------------------
# Row shape + formatting
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MatrixRow:
    coin: str
    strategy: str
    interval: str
    days: int
    return_pct: float
    trades: int
    win_rate_pct: float
    max_drawdown_pct: float
    sharpe: float
    fees: float


def _row_from_result(
    coin: str,
    strategy: str,
    interval: str,
    days: int,
    result: BacktestResult,
) -> MatrixRow:
    return MatrixRow(
        coin=coin,
        strategy=strategy,
        interval=interval,
        days=days,
        return_pct=result.total_return_pct,
        trades=result.total_trades,
        win_rate_pct=result.win_rate * 100.0,
        max_drawdown_pct=result.max_drawdown_pct,
        sharpe=result.sharpe_proxy,
        fees=result.total_fees,
    )


_SORT_KEYS = {
    "return": lambda r: r.return_pct,
    "sharpe": lambda r: r.sharpe,
    "win_rate": lambda r: r.win_rate_pct,
    "dd": lambda r: -r.max_drawdown_pct,  # less drawdown = better
    "trades": lambda r: r.trades,
}


def sort_rows(rows: list[MatrixRow], key: str = "return") -> list[MatrixRow]:
    if key not in _SORT_KEYS:
        raise ValueError(f"Unknown sort key: {key!r} (choices: {list(_SORT_KEYS)})")
    return sorted(rows, key=_SORT_KEYS[key], reverse=True)


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

_HEADERS = ["Coin", "Strategy", "Int", "Days", "Return%", "Trades", "Win%", "DD%", "Sharpe", "Fees$"]


def _format_row(r: MatrixRow) -> list[str]:
    return [
        r.coin,
        r.strategy,
        r.interval,
        str(r.days),
        f"{r.return_pct:+.2f}",
        str(r.trades),
        f"{r.win_rate_pct:.0f}" if r.trades > 0 else "—",
        f"{r.max_drawdown_pct:.2f}",
        f"{r.sharpe:.2f}",
        f"{r.fees:.4f}",
    ]


def print_table(rows: list[MatrixRow]) -> None:
    formatted = [_HEADERS] + [_format_row(r) for r in rows]
    widths = [max(len(row[i]) for row in formatted) for i in range(len(_HEADERS))]

    def _line(values: list[str]) -> str:
        return "  ".join(v.ljust(widths[i]) for i, v in enumerate(values))

    print(_line(_HEADERS))
    print("  ".join("-" * w for w in widths))
    for r in formatted[1:]:
        print(_line(r))


def export_csv(rows: list[MatrixRow], path: str) -> None:
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "coin", "strategy", "interval", "days",
            "return_pct", "trades", "win_rate_pct",
            "max_drawdown_pct", "sharpe", "fees",
        ])
        for r in rows:
            writer.writerow([
                r.coin, r.strategy, r.interval, r.days,
                f"{r.return_pct:.4f}", r.trades, f"{r.win_rate_pct:.2f}",
                f"{r.max_drawdown_pct:.4f}", f"{r.sharpe:.4f}", f"{r.fees:.6f}",
            ])


# ---------------------------------------------------------------------------
# Matrix runner
# ---------------------------------------------------------------------------

def run_matrix(
    info,
    coins: list[str],
    strategies: list[str],
    interval: str,
    days_list: list[int],
    fast: int = 9,
    slow: int = 21,
    config: BacktestConfig | None = None,
) -> list[MatrixRow]:
    """Run every (coin × strategy × days) combo. Returns flat list of rows."""
    if config is None:
        config = BacktestConfig()
    max_days = max(days_list)
    rows: list[MatrixRow] = []

    for coin in coins:
        candles = fetch_candles(info, coin, interval, lookback_hours=max_days * 24)
        if candles.empty:
            print(f"[skip] No candles for {coin} ({interval})", file=sys.stderr)
            continue

        for days in days_list:
            bars_needed = days * (24 if interval == "1h" else 6 if interval == "4h" else 1)
            sliced = candles.tail(bars_needed).reset_index(drop=True) if len(candles) > bars_needed else candles

            for strat_name in strategies:
                strategy = build_strategy(strat_name, fast=fast, slow=slow)
                result = run_backtest(strategy, sliced, config)
                rows.append(_row_from_result(coin, strat_name, interval, days, result))

    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_list(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _parse_int_list(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grid-search backtest matrix over strategies × coins × windows.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--coins", default="BTC,ETH,SOL,HYPE",
                        help="Comma-separated coin list")
    parser.add_argument("--strategies", default="ema,rsi",
                        help="Comma-separated strategies (ema, rsi)")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--days", default="7,14,30",
                        help="Comma-separated lookback windows in days")
    parser.add_argument("--fast", type=int, default=9, help="EMA fast period")
    parser.add_argument("--slow", type=int, default=21, help="EMA slow period")
    parser.add_argument("--csv", default=None, help="Optional CSV output path")
    parser.add_argument("--sort", default="return",
                        choices=list(_SORT_KEYS.keys()),
                        help="Column to sort by (desc)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    coins = _parse_list(args.coins)
    strategies = _parse_list(args.strategies)
    days_list = _parse_int_list(args.days)

    print(f"Matrix: {len(coins)} coins × {len(strategies)} strategies × "
          f"{len(days_list)} windows = {len(coins) * len(strategies) * len(days_list)} cells")
    print(f"Fetching {args.interval} candles (max {max(days_list)}d)...")
    print()

    info = make_info(ACTIVE_DEXES)
    rows = run_matrix(info, coins, strategies, args.interval, days_list,
                      fast=args.fast, slow=args.slow)

    if not rows:
        print("No results. All coins failed to fetch?", file=sys.stderr)
        return 1

    rows = sort_rows(rows, key=args.sort)
    print_table(rows)
    print()
    print(f"Sorted by: {args.sort} (desc)")
    print("NOTE: In-sample backtest. Past performance != future performance.")

    if args.csv:
        export_csv(rows, args.csv)
        print(f"CSV exported: {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
