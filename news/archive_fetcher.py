"""Real-world archive fetchers for Phase 6A-prime edge check.

Sources that actually work today:
  - trumpstruth.org/feed          — last ~100 Trump posts (days, not months)
  - federalreserve.gov RSS        — last ~20 press releases (months)
  - coindesk.com RSS              — last ~25 crypto headlines
  - marketwatch.com RSS           — last ~10 markets headlines

All emit `NewsPost` instances consistent with the existing schema.

Limitation: depth is source-dependent. trumpstruth RSS covers ~3 days
of Trump activity. Deeper Trump history needs Wayback CDX scraping
(deferred — Phase 6A-prime+).
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Callable

import feedparser
import requests

from news.sources import NewsPost, compute_content_hash


USER_AGENT = "hype-bot/0.1 (research; +github.com/Nihaal-Shaikh/hype)"
DEFAULT_TIMEOUT = 15


# --- Generic RSS pull helper --------------------------------------------

def _fetch_feed(url: str, timeout: int = DEFAULT_TIMEOUT) -> "feedparser.FeedParserDict":
    """Polite HTTP GET → feedparser. Returns parsed feed (may be empty on error)."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
    except Exception:
        return feedparser.parse(b"")
    return feedparser.parse(resp.content)


def _clean_html(raw: str) -> str:
    """Strip HTML tags + decode entities + collapse whitespace."""
    no_tags = re.sub(r"<[^>]+>", " ", raw)
    decoded = html.unescape(no_tags)
    return re.sub(r"\s+", " ", decoded).strip()


def _parsed_ts(entry) -> datetime:
    """Return a tz-aware UTC datetime from an RSS entry, best-effort."""
    if getattr(entry, "published_parsed", None):
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    if getattr(entry, "updated_parsed", None):
        return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


# --- Trump Truth Social via trumpstruth.org /feed ------------------------

def fetch_trumpstruth(feed_url: str = "https://trumpstruth.org/feed") -> list[NewsPost]:
    """Pull latest Trump Truth Social posts via trumpstruth.org public RSS."""
    feed = _fetch_feed(feed_url)
    posts: list[NewsPost] = []
    now = datetime.now(timezone.utc)

    for entry in feed.entries:
        raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        text = _clean_html(raw_summary)
        if not text:
            continue
        # Skip pure re-posts (of the form "RT: https://..."; no original content)
        if text.startswith("RT:") and "truthsocial.com" in text:
            text = text.replace("RT:", "").strip()
            if len(text) < 30:
                continue
        link = getattr(entry, "link", "") or ""
        # post_id from link suffix: .../statuses/NNNN
        post_id = link.rsplit("/", 1)[-1] if link else str(hash(text))
        posts.append(NewsPost(
            post_id=f"trumpstruth_{post_id}",
            source="truth_social",
            author="realDonaldTrump",
            published_at=_parsed_ts(entry),
            ingested_at=now,
            raw_text=text,
            url=link or None,
            content_hash=compute_content_hash(text),
        ))
    return posts


# --- Generic RSS watched-feed ingest ------------------------------------

_RSS_FEEDS: dict[str, str] = {
    "fed:press-releases": "https://www.federalreserve.gov/feeds/press_all.xml",
    "bloomberg:markets": "https://www.marketwatch.com/rss/topstories",  # MarketWatch as a proxy — Bloomberg feeds are paywalled
    "cryptopanic:hot": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "reuters:markets": "https://feeds.reuters.com/reuters/marketsNews",
}


def fetch_rss_watched(feed_id: str | None = None) -> list[NewsPost]:
    """Pull all configured RSS feeds (or one when feed_id is given)."""
    posts: list[NewsPost] = []
    now = datetime.now(timezone.utc)
    feeds = {feed_id: _RSS_FEEDS[feed_id]} if feed_id else _RSS_FEEDS

    for fid, url in feeds.items():
        feed = _fetch_feed(url)
        for entry in feed.entries:
            title = _clean_html(getattr(entry, "title", ""))
            summary = _clean_html(getattr(entry, "summary", "") or getattr(entry, "description", ""))
            text = f"{title}. {summary}" if summary else title
            if not text:
                continue
            link = getattr(entry, "link", "") or ""
            post_id = getattr(entry, "id", "") or link
            if not post_id:
                continue
            posts.append(NewsPost(
                post_id=f"rss_{fid}_{abs(hash(post_id))}",
                source="rss",
                author=fid,
                published_at=_parsed_ts(entry),
                ingested_at=now,
                raw_text=text,
                url=link or None,
                content_hash=compute_content_hash(text),
            ))
    return posts


# --- Aggregate + serialize ----------------------------------------------

def post_to_jsonl_row(post: NewsPost) -> dict:
    """Serialize a NewsPost for JSONL archive storage."""
    return {
        "post_id": post.post_id,
        "source": post.source,
        "author": post.author,
        "published_at": post.published_at.isoformat(),
        "raw_text": post.raw_text,
        "url": post.url,
    }
