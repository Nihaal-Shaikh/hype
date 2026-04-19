"""Tests for news source adapters — Phase 6A."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from news.sources import NewsPost, NewsSource, compute_content_hash
from news.sources.rss import RssSource
from news.sources.truth_social import TruthSocialSource, parse_trumpstruth_html
from news.sources.twitter import TwitterSource


FIXTURES = Path(__file__).parent / "fixtures"


def _load_tweets() -> list[dict]:
    with open(FIXTURES / "sample_tweets.jsonl") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _load_archive_html() -> str:
    return (FIXTURES / "sample_truth_archive.html").read_text()


# --- Protocol conformance ------------------------------------------------

def test_twitter_source_protocol_conformance():
    s = TwitterSource(fetch_raw=lambda: [])
    assert isinstance(s, NewsSource)
    assert s.source_name == "twitter"


def test_truth_social_protocol_conformance():
    s = TruthSocialSource(parse_raw=lambda: [])
    assert isinstance(s, NewsSource)
    assert s.source_name == "truth_social"


def test_rss_protocol_conformance():
    s = RssSource(feed_id="reuters:markets", fetch_raw=lambda: [])
    assert isinstance(s, NewsSource)
    assert s.source_name == "rss"


# --- Twitter adapter -----------------------------------------------------

def test_twitter_source_filters_unwatched_at_adapter():
    """Unwatched authors must NOT appear in output, not even as dropped hashes."""
    raw = _load_tweets()
    # Raw fixture has 5 entries: 3 from @realDonaldTrump, 1 fed, 1 random, 1 elon
    src = TwitterSource(fetch_raw=lambda: raw)
    posts = src.fetch()
    authors = {p.author for p in posts}
    assert "@realDonaldTrump" in authors
    assert "@federalreserve" in authors
    assert "@randomuser" not in authors
    assert "@elonmusk" not in authors


def test_twitter_source_dedupe_by_post_id():
    raw = _load_tweets()
    src = TwitterSource(fetch_raw=lambda: raw)
    first = src.fetch()
    second = src.fetch()  # same raw, all seen now
    assert len(second) == 0
    # First call produced one entry per unique post_id from watched authors
    assert len(first) == len({p.post_id for p in first})


def test_twitter_source_rate_limit_backoff():
    """Network / rate-limit exceptions must return [] not crash."""
    def raiser():
        raise RuntimeError("429 rate limit")
    src = TwitterSource(fetch_raw=raiser)
    assert src.fetch() == []


def test_twitter_normalizes_timestamps():
    raw = _load_tweets()
    src = TwitterSource(fetch_raw=lambda: raw)
    posts = src.fetch()
    for p in posts:
        assert p.published_at.tzinfo is not None
        assert p.ingested_at.tzinfo is not None


def test_twitter_computes_content_hash():
    raw = _load_tweets()
    src = TwitterSource(fetch_raw=lambda: raw)
    posts = src.fetch()
    for p in posts:
        assert p.content_hash == compute_content_hash(p.raw_text)


# --- Truth Social adapter ------------------------------------------------

def test_truth_social_parses_archive_html():
    html = _load_archive_html()
    raw = parse_trumpstruth_html(html)
    assert len(raw) == 3
    assert raw[0]["id"] == "112345"
    assert "war is over" in raw[0]["text"].lower()
    # HTML tags stripped from last post
    assert "<b>" not in raw[2]["text"]


def test_truth_social_skips_seen_posts():
    html = _load_archive_html()
    src = TruthSocialSource(parse_raw=lambda: parse_trumpstruth_html(html))
    first = src.fetch()
    assert len(first) == 3
    second = src.fetch()  # same fixture, all seen
    assert second == []


def test_truth_social_survives_parse_error():
    def raiser():
        raise ValueError("bad html")
    src = TruthSocialSource(parse_raw=raiser)
    assert src.fetch() == []


# --- RSS adapter ---------------------------------------------------------

def test_rss_source_parses_bloomberg_fixture():
    entries = [
        {"id": "b1", "title": "Oil rallies on war headlines",
         "summary": "Crude futures up 4%", "published": "2026-04-18T14:00:00Z",
         "link": "https://bloomberg.com/news/b1"},
        {"id": "b2", "title": "Fed signals pause",
         "summary": "Minutes show caution", "published": "2026-04-18T15:00:00Z",
         "link": "https://bloomberg.com/news/b2"},
    ]
    src = RssSource(feed_id="bloomberg:markets", fetch_raw=lambda: entries)
    posts = src.fetch()
    assert len(posts) == 2
    assert posts[0].author == "bloomberg:markets"
    assert "Oil rallies" in posts[0].raw_text


def test_rss_source_filters_unwatched_feed():
    """A feed_id that's not on the watchlist returns nothing."""
    entries = [{"id": "x1", "title": "noise", "summary": "", "link": "http://x"}]
    src = RssSource(feed_id="techcrunch:latest", fetch_raw=lambda: entries)
    assert src.fetch() == []


def test_rss_source_dedupe():
    entries = [{"id": "r1", "title": "hello", "summary": "world",
                "published": "2026-04-18T14:00:00Z", "link": "http://r1"}]
    src = RssSource(feed_id="reuters:markets", fetch_raw=lambda: entries)
    first = src.fetch()
    second = src.fetch()
    assert len(first) == 1
    assert second == []


# --- Generic failure mode ------------------------------------------------

def test_source_returns_empty_on_network_error():
    """All three adapters return [] on exceptions, never raise."""
    def raiser():
        raise ConnectionError("network down")

    for src in (
        TwitterSource(fetch_raw=raiser),
        TruthSocialSource(parse_raw=raiser),
        RssSource(feed_id="reuters:markets", fetch_raw=raiser),
    ):
        assert src.fetch() == []
