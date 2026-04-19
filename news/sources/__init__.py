"""News source adapters — Protocol, NewsPost dataclass, concrete impls.

All adapters return `list[NewsPost]` from a `fetch()` call and MUST apply
`is_watched()` filtering BEFORE any DB write or hash computation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class NewsPost:
    """One ingested news item, normalized across sources."""
    post_id: str           # source-specific unique id (tweet id, URL, etc.)
    source: str            # "twitter" | "truth_social" | "rss"
    author: str            # handle or feed identifier
    published_at: datetime
    ingested_at: datetime
    raw_text: str
    url: str | None
    content_hash: str      # sha256 of normalized text — dedup key


def compute_content_hash(text: str) -> str:
    """Normalize + hash for dedup. Lowercase + strip whitespace."""
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@runtime_checkable
class NewsSource(Protocol):
    """Contract every source adapter must satisfy."""
    source_name: str

    def fetch(self) -> list[NewsPost]:
        """Return new posts since last fetch. MUST filter via is_watched()
        BEFORE any DB write or hash computation. MUST NOT raise on network
        errors — return [] and log instead.
        """
        ...
