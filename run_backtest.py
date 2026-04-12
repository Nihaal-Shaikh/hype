"""CLI entry point for running a backtest of the EMA crossover strategy.

Usage:
    python run_backtest.py [--coin BTC] [--fast 9] [--slow 21]
                           [--interval 1h] [--days 7] [--csv path/to/trades.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone

import pandas as pd

from backtest import BacktestConfig, BacktestResult, Trade, run_backtest
from hype_bot import ACTIVE_DEXES, make_info, fetch_candles
from strategies.ema_crossover import EmaCrossover, EmaCrossoverConfig


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

_DIVIDER = "=" * 64


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


def _fmt_dollar(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}${abs(value):.2f}"


def _period_label(candles: pd.DataFrame, days: int) -> str:
    if candles.empty:
        return "N/A"
    start = candles["time"].iloc[0]
    end = candles["time"].iloc[-1]
    start_str = start.strftime("%Y-%m-%d") if hasattr(start, "strftime") else str(start)
    end_str = end.strftime("%Y-%m-%d") if hasattr(end, "strftime") else str(end)
    return f"{start_str} to {end_str} ({days} days)"


def _trade_timestamp(ts: datetime) -> str:
    if hasattr(ts, "strftime"):
        return ts.strftime("%Y-%m-%d %H:%M")
    return str(ts)


def _pnl_for_pair(buy: Trade, sell: Trade) -> float:
    """Compute net PnL for a BUY/SELL round-trip."""
    return sell.notional - buy.notional - buy.fee - sell.fee


def print_report(
    result: BacktestResult,
    candles: pd.DataFrame,
    coin: str,
    fast: int,
    slow: int,
    interval: str,
    days: int,
    config: BacktestConfig,
) -> None:
    """Print a human-readable backtest report to stdout."""
    n_candles = len(result.equity_curve)
    period = _period_label(candles, days)
    final_equity = config.initial_capital + (
        config.initial_capital * result.total_return_pct / 100.0
    )
    dollar_return = final_equity - config.initial_capital
    position_notional = config.initial_capital * config.position_size_pct * config.leverage

    # Build paired trade PnL list
    buy_trades = [t for t in result.trades if t.side == "BUY"]
    sell_trades = [t for t in result.trades if t.side == "SELL"]
    pairs = list(zip(buy_trades, sell_trades))
    wins = sum(1 for b, s in pairs if s.price > b.price)
    n_pairs = len(pairs)

    print(_DIVIDER)
    print(f"BACKTEST REPORT — EMA Crossover ({fast}/{slow}) on {coin}")
    print(_DIVIDER)
    print(f"Period:          {period}")
    print(f"Interval:        {interval} ({n_candles} candles)")
    print(f"Initial capital: ${config.initial_capital:.2f}")
    print(f"Leverage:        {config.leverage}x")
    print(f"Position size:   {int(config.position_size_pct * 100)}%"
          f" (${position_notional:.2f} notional)")
    print()
    print("Results:")
    print(f"  Total return:   {_fmt_pct(result.total_return_pct)}"
          f"  ({_fmt_dollar(dollar_return)})")
    print(f"  Max drawdown:   {_fmt_pct(-result.max_drawdown_pct)}")
    if n_pairs > 0:
        print(f"  Win rate:       {result.win_rate * 100:.1f}% ({wins}/{n_pairs} trades)")
    else:
        print(f"  Win rate:       N/A (no completed round-trips)")
    print(f"  Total trades:   {result.total_trades}")
    print(f"  Total fees:     ${result.total_fees:.4f}")
    print(f"  Sharpe proxy:   {result.sharpe_proxy:.2f}")
    print()

    if result.trades:
        print("Trade log:")
        buy_idx = 0
        sell_idx = 0
        trade_num = 1
        for trade in result.trades:
            ts = _trade_timestamp(trade.timestamp)
            if trade.side == "BUY":
                print(f"  #{trade_num:<3} BUY   {ts}  @ ${trade.price:,.2f}"
                      f"  size {trade.size:.5f}")
                trade_num += 1
            else:
                # Find corresponding BUY for PnL
                if sell_idx < len(pairs):
                    b, s = pairs[sell_idx]
                    pnl = _pnl_for_pair(b, s)
                    sell_idx += 1
                    sign = "+" if pnl >= 0 else ""
                    print(f"  #{trade_num:<3} SELL  {ts}  @ ${trade.price:,.2f}"
                          f"  size {trade.size:.5f}  PnL: {sign}${pnl:.2f}")
                else:
                    print(f"  #{trade_num:<3} SELL  {ts}  @ ${trade.price:,.2f}"
                          f"  size {trade.size:.5f}")
                trade_num += 1
    else:
        print("Trade log:       (no trades)")

    print()
    print("NOTE: In-sample backtest on"
          f" {days} days of data. Not predictive of future")
    print("performance. Use for learning and strategy comparison only.")
    print(_DIVIDER)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

_CSV_COLUMNS = ["timestamp", "side", "price", "size", "fee", "notional"]


def export_csv(trades: tuple[Trade, ...], path: str) -> None:
    """Export the trade log to a CSV file."""
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for trade in trades:
            ts = (
                trade.timestamp.isoformat()
                if hasattr(trade.timestamp, "isoformat")
                else str(trade.timestamp)
            )
            writer.writerow({
                "timestamp": ts,
                "side": trade.side,
                "price": trade.price,
                "size": trade.size,
                "fee": trade.fee,
                "notional": trade.notional,
            })
    print(f"Trade log exported to: {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an EMA crossover backtest against Hyperliquid candle data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--coin", default="BTC", help="Coin/symbol to backtest")
    parser.add_argument("--fast", type=int, default=9, help="Fast EMA period")
    parser.add_argument("--slow", type=int, default=21, help="Slow EMA period")
    parser.add_argument("--interval", default="1h", help="Candle interval (e.g. 1h, 4h, 1d)")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days")
    parser.add_argument("--csv", default=None, metavar="PATH",
                        help="Optional path to export trade log as CSV")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.fast >= args.slow:
        print(f"Error: --fast ({args.fast}) must be less than --slow ({args.slow})",
              file=sys.stderr)
        return 1

    print(f"Fetching {args.interval} candles for {args.coin}"
          f" over the last {args.days} days…")

    info = make_info(ACTIVE_DEXES)
    candles = fetch_candles(info, args.coin, args.interval,
                            lookback_hours=args.days * 24)

    if candles.empty:
        print(f"Error: no candle data returned for {args.coin!r}"
              f" with interval {args.interval!r}",
              file=sys.stderr)
        return 1

    strategy = EmaCrossover(EmaCrossoverConfig(
        fast_period=args.fast,
        slow_period=args.slow,
    ))
    config = BacktestConfig()
    result = run_backtest(strategy, candles, config)

    print_report(
        result=result,
        candles=candles,
        coin=args.coin,
        fast=args.fast,
        slow=args.slow,
        interval=args.interval,
        days=args.days,
        config=config,
    )

    if args.csv:
        export_csv(result.trades, args.csv)

    return 0


if __name__ == "__main__":
    sys.exit(main())
