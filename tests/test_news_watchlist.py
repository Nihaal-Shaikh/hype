"""Tests for news source watchlist — Phase 6A."""

from __future__ import annotations

import pytest

from news.sources.watchlist import WATCHED_HANDLES, is_watched


def test_watched_handle_passes():
    assert is_watched("twitter", "@realDonaldTrump") is True
    assert is_watched("truth_social", "realDonaldTrump") is True
    assert is_watched("rss", "bloomberg:markets") is True


def test_unwatched_handle_rejected():
    assert is_watched("twitter", "@randomuser") is False
    assert is_watched("twitter", "@elonmusk") is False  # not on list yet
    assert is_watched("rss", "techcrunch:latest") is False
    # Wrong source for a known author
    assert is_watched("truth_social", "@realDonaldTrump") is False


def test_watchlist_is_frozen():
    """WATCHED_HANDLES must be a frozenset — can't be mutated at runtime."""
    assert isinstance(WATCHED_HANDLES, frozenset)
    with pytest.raises(AttributeError):
        WATCHED_HANDLES.add("twitter:@someone")  # type: ignore[attr-defined]


def test_watchlist_nonempty():
    assert len(WATCHED_HANDLES) >= 5
