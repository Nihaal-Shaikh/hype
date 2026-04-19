"""CLI entry point for walk-forward out-of-sample evaluation.

Usage:
    python run_walkforward.py [--coin BTC] [--strategy ema|rsi]
                              [--interval 1h] [--days 30]
                              [--train-frac 0.7] [--metric sharpe|return|win_rate|dd]
"""

from __future__ import annotations

import argparse
import sys

from hype_bot import ACTIVE_DEXES, fetch_candles, make_info
from walkforward import OosReport, metric_value, out_of_sample_eval


_DIVIDER = "=" * 64


def _format_params(params: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in params.items())


def print_report(report: OosReport) -> None:
    train = report.train_result
    test = report.test_result

    print(_DIVIDER)
    print(f"WALK-FORWARD — {report.strategy.upper()} — metric: {report.metric}")
    print(_DIVIDER)
    print(f"Split:         {report.train_frac*100:.0f}% train / {(1-report.train_frac)*100:.0f}% test")
    print(f"Bars:          {report.n_train} train / {report.n_test} test")
    print(f"Combos tried:  {report.combos_tried}")
    print(f"Best params:   {_format_params(report.best_params)}")
    print()
    print(f"{'Metric':<14}{'Train':>12}{'Test':>12}{'Delta':>12}")
    print("-" * 50)

    metrics = [
        ("Return %",       train.total_return_pct,    test.total_return_pct),
        ("Sharpe proxy",   train.sharpe_proxy,         test.sharpe_proxy),
        ("Max DD %",       train.max_drawdown_pct,     test.max_drawdown_pct),
        ("Win rate %",     train.win_rate * 100,       test.win_rate * 100),
        ("Trades",         float(train.total_trades),  float(test.total_trades)),
        ("Fees $",         train.total_fees,           test.total_fees),
    ]
    for label, t, s in metrics:
        delta = s - t
        sign = "+" if delta >= 0 else ""
        print(f"{label:<14}{t:>12.2f}{s:>12.2f}{sign}{delta:>11.2f}")

    print()

    # Interpretation hint
    key_train = metric_value(train, report.metric)
    key_test = metric_value(test, report.metric)
    verdict: str
    if report.test_result.total_trades == 0:
        verdict = "INCONCLUSIVE — no trades fired out-of-sample"
    elif key_test >= key_train * 0.7:
        verdict = "PLAUSIBLE edge — test metric within 70% of train"
    elif key_test > 0:
        verdict = "WEAK — test positive but much below train (likely some overfit)"
    else:
        verdict = "OVERFIT — train profitable, test loses money"

    print(f"Verdict: {verdict}")
    print("NOTE: Single split, one coin, one window. Do not over-trust.")
    print(_DIVIDER)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Walk-forward out-of-sample evaluation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--coin", default="BTC")
    parser.add_argument("--strategy", choices=["ema", "rsi"], default="ema")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--metric", choices=["return", "sharpe", "win_rate", "dd"],
                        default="sharpe")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    print(f"Fetching {args.interval} candles for {args.coin} ({args.days}d)...")
    info = make_info(ACTIVE_DEXES)
    candles = fetch_candles(info, args.coin, args.interval, lookback_hours=args.days * 24)

    if candles.empty:
        print(f"No candle data for {args.coin!r}", file=sys.stderr)
        return 1
    print(f"Got {len(candles)} candles.")
    print()

    report = out_of_sample_eval(
        strategy_name=args.strategy,
        candles=candles,
        train_frac=args.train_frac,
        metric=args.metric,
    )
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
