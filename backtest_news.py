"""CLI for Phase 6A-prime edge check.

Runs a deterministic mock classifier over an archive of posts, replays
against real Hyperliquid candles at 5 ingestion-delay points, and
emits a JSON report. Go/no-go threshold is pre-committed.

Usage:
    python backtest_news.py \
        --archive tests/fixtures/truth_social_archive_60d.jsonl \
        --archive tests/fixtures/rss_archive_60d.jsonl \
        --markets BTC,xyz:SP500,xyz:GOLD \
        --output .omc/research/phase6a-prime.json

Synthetic fixture disclaimer: if archive file is flagged as synthetic,
the report marks the result as STRUCTURAL-VALIDATION-ONLY rather than
a true edge test. Real 60-day archive ingest must precede any real
go/no-go commitment.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from hype_bot import ACTIVE_DEXES, fetch_candles, make_info
from news.replay import MarketStats, replay_signals, summarize_by_market
from news.sources import NewsPost, compute_content_hash


# --- Pre-committed go/no-go threshold ------------------------------------

GO_THRESHOLD: dict[str, float] = {
    "oos_sharpe_minimum": 0.5,
    "delay_seconds": 60,
    "min_markets_passing": 2,
    "min_trades_per_market": 20,
}

DELAY_GRID_SECONDS: tuple[int, ...] = (0, 30, 60, 120, 300)


# --- Archive loader ------------------------------------------------------

def load_archive(path: Path) -> list[NewsPost]:
    """Load a JSONL archive of posts. Each line: NewsPost-shaped dict.

    Required fields: post_id, source, author, published_at, raw_text.
    """
    posts: list[NewsPost] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            published = datetime.fromisoformat(d["published_at"].replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            text = d["raw_text"]
            posts.append(NewsPost(
                post_id=d["post_id"],
                source=d["source"],
                author=d["author"],
                published_at=published,
                ingested_at=published,   # archive backfill — use published as ingested
                raw_text=text,
                url=d.get("url"),
                content_hash=compute_content_hash(text),
            ))
    return posts


# --- Candle pre-fetch ----------------------------------------------------

def fetch_candles_for_markets(
    markets: list[str], lookback_days: int = 90
) -> dict[str, pd.DataFrame]:
    """Pull candles once per market; replay reads these."""
    info = make_info(ACTIVE_DEXES)
    out: dict[str, pd.DataFrame] = {}
    for m in markets:
        df = fetch_candles(info, m, "1h", lookback_hours=lookback_days * 24)
        if df.empty:
            print(f"[warn] no candles for {m}", file=sys.stderr)
            continue
        out[m] = df
    return out


# --- Report --------------------------------------------------------------

def build_report(
    archive_paths: list[Path],
    results_by_delay: dict[int, list[MarketStats]],
    universe: list[str],
    synthetic: bool,
) -> dict:
    """Serialize results for .omc/research/phase6a-prime-*.json."""
    baseline = results_by_delay.get(GO_THRESHOLD["delay_seconds"], [])
    markets_passing = [
        ms for ms in baseline
        if ms.sharpe >= GO_THRESHOLD["oos_sharpe_minimum"]
        and ms.trades >= GO_THRESHOLD["min_trades_per_market"]
    ]
    go = len(markets_passing) >= GO_THRESHOLD["min_markets_passing"]

    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "synthetic_fixture": synthetic,
        "note": (
            "STRUCTURAL-VALIDATION-ONLY: real archive pending." if synthetic
            else "Real-archive go/no-go evaluation."
        ),
        "archive_paths": [str(p) for p in archive_paths],
        "universe": universe,
        "threshold": GO_THRESHOLD,
        "delay_grid_seconds": list(DELAY_GRID_SECONDS),
        "per_delay_stats": {
            str(delay): [asdict(ms) for ms in stats]
            for delay, stats in results_by_delay.items()
        },
        "markets_passing_at_baseline_delay": [ms.market for ms in markets_passing],
        "go_decision": "GO" if go else "NO-GO",
    }


# --- CLI -----------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 6A-prime edge check (mock classifier)")
    p.add_argument("--archive", action="append", required=True,
                   help="Path to a JSONL archive of posts (can repeat)")
    p.add_argument("--markets", default="BTC,xyz:SP500,xyz:GOLD",
                   help="Comma-separated market list")
    p.add_argument("--lookback-days", type=int, default=90,
                   help="Candle history lookback in days")
    p.add_argument("--output", default=None,
                   help="Path to write report JSON (default: .omc/research/phase6a-prime-{ts}.json)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    archive_paths = [Path(a) for a in args.archive]
    universe = [m.strip() for m in args.markets.split(",") if m.strip()]

    # Detect synthetic fixtures by name prefix
    synthetic = any("fixture" in str(p) or "sample" in str(p) for p in archive_paths)

    print(f"Loading archives: {archive_paths}")
    posts: list[NewsPost] = []
    for ap in archive_paths:
        posts.extend(load_archive(ap))
    print(f"Loaded {len(posts)} posts.")

    print(f"Fetching candles for {universe}...")
    candles = fetch_candles_for_markets(universe, lookback_days=args.lookback_days)
    for m, df in candles.items():
        print(f"  {m}: {len(df)} bars")

    results: dict[int, list[MarketStats]] = {}
    for delay in DELAY_GRID_SECONDS:
        trades = replay_signals(posts, candles, ingestion_delay_seconds=delay)
        results[delay] = summarize_by_market(trades)
        print(f"  delay={delay}s: {sum(s.trades for s in results[delay])} trades")

    report = build_report(archive_paths, results, universe, synthetic)

    if args.output:
        out = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = Path(f".omc/research/phase6a-prime-{ts}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print()
    print(f"Report: {out}")
    print(f"Decision: {report['go_decision']}  (synthetic={synthetic})")
    if synthetic:
        print("WARN: synthetic fixture — this is STRUCTURAL validation only.")
        print("      Real 60-day archive ingest required before committing to 6B.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
