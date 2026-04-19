"""CLI — pull real archive from public news sources, write JSONL.

Usage:
    python fetch_archive.py --output tests/fixtures/real_archive.jsonl

Pulls from:
  - trumpstruth.org /feed (Trump Truth Social — last ~100 posts)
  - federalreserve.gov press-releases RSS
  - coindesk.com RSS
  - marketwatch.com RSS (Bloomberg proxy)

Prints coverage summary (date range, count per source) so caller can
judge whether the sample is deep enough for meaningful edge check.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from news.archive_fetcher import fetch_rss_watched, fetch_trumpstruth, post_to_jsonl_row
from news.sources import NewsPost


def _coverage(posts: list[NewsPost]) -> dict:
    by_source = Counter(p.source for p in posts)
    by_author = Counter(f"{p.source}:{p.author}" for p in posts)
    if posts:
        newest = max(p.published_at for p in posts)
        oldest = min(p.published_at for p in posts)
        span_days = (newest - oldest).total_seconds() / 86400
    else:
        newest = oldest = datetime.now(timezone.utc)
        span_days = 0.0
    return {
        "total_posts": len(posts),
        "by_source": dict(by_source),
        "by_author": dict(by_author),
        "oldest": oldest.isoformat(),
        "newest": newest.isoformat(),
        "span_days": round(span_days, 2),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pull real news archive to JSONL.")
    p.add_argument("--output", default="tests/fixtures/real_archive.jsonl",
                   help="Output JSONL path")
    p.add_argument("--skip-trump", action="store_true")
    p.add_argument("--skip-rss", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    posts: list[NewsPost] = []

    if not args.skip_trump:
        print("Fetching trumpstruth.org /feed...")
        trump = fetch_trumpstruth()
        print(f"  got {len(trump)} posts")
        posts.extend(trump)

    if not args.skip_rss:
        print("Fetching RSS feeds (Fed, MarketWatch, CoinDesk, Reuters)...")
        rss = fetch_rss_watched()
        print(f"  got {len(rss)} posts (watchlist-filtered)")
        posts.extend(rss)

    # Dedup by (source, author, content_hash)
    seen = set()
    unique: list[NewsPost] = []
    for p in posts:
        key = (p.source, p.author, p.content_hash)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    cov = _coverage(unique)
    print()
    print("--- Coverage ---")
    print(f"Total unique posts: {cov['total_posts']}")
    print(f"Date span: {cov['span_days']} days  ({cov['oldest']} → {cov['newest']})")
    print(f"By source: {cov['by_source']}")
    print(f"By author: {cov['by_author']}")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as fh:
        for p in unique:
            fh.write(json.dumps(post_to_jsonl_row(p), default=str) + "\n")
    print()
    print(f"Written: {out}  ({out.stat().st_size:,} bytes)")

    # Write coverage metadata sidecar
    meta = out.with_suffix(".meta.json")
    meta.write_text(json.dumps(cov, indent=2, default=str))
    print(f"Coverage sidecar: {meta}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
