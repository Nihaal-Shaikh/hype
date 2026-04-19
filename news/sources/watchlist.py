"""Author/source watchlist — filter unwatched posts BEFORE any DB write.

Keys are canonical `"{source}:{author}"` strings. The watchlist is
imported at module load time and exposed as a `frozenset` so it can't
drift at runtime.

See Phase 6 plan ADR #3: drift between adapters and this list is
blocked by `test_watchlist_is_frozen` and the import-time invariant
that `WATCHED_HANDLES` is an unmutable frozenset.
"""

from __future__ import annotations

WATCHED_HANDLES: frozenset[str] = frozenset({
    # Presidential / political — biggest market movers
    "twitter:@realDonaldTrump",
    "truth_social:realDonaldTrump",
    # Federal Reserve — rate decisions, policy signals
    "twitter:@federalreserve",
    "twitter:@jeromepowell",
    # Treasury / regulators
    "twitter:@SECGov",
    "twitter:@USTreasury",
    # Institutional news feeds (RSS)
    "rss:reuters:markets",
    "rss:bloomberg:markets",
    "rss:fed:press-releases",
    "rss:cryptopanic:hot",
})


def is_watched(source: str, author: str) -> bool:
    """Return True if (source, author) is in the watchlist."""
    key = f"{source}:{author}"
    return key in WATCHED_HANDLES
