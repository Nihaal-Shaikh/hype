"""Truth Social source adapter — polls trumpstruth.org archive.

Public archive of Trump's Truth Social posts. HTML-scraped since no
official API exists. Fetcher is injected so tests can supply fixture
HTML.

Only `realDonaldTrump` is whitelisted (see watchlist). Other Truth
Social accounts are filtered out at adapter level.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Iterable

from news.sources import NewsPost, compute_content_hash
from news.sources.watchlist import is_watched


ARCHIVE_URL = "https://trumpstruth.org/"
TRUTH_AUTHOR = "realDonaldTrump"


RawPost = dict  # {"id": str, "text": str, "published_at": iso8601, "url": str}


class TruthSocialSource:
    source_name: str = "truth_social"

    def __init__(
        self,
        parse_raw: Callable[[], Iterable[RawPost]],
        *,
        seen_ids: set[str] | None = None,
    ) -> None:
        self._parse_raw = parse_raw
        self._seen: set[str] = set(seen_ids or ())

    def fetch(self) -> list[NewsPost]:
        # Watchlist check at adapter level — only realDonaldTrump allowed
        if not is_watched(self.source_name, TRUTH_AUTHOR):
            return []

        try:
            raw = list(self._parse_raw())
        except Exception:
            return []

        posts: list[NewsPost] = []
        now = datetime.now(timezone.utc)

        for r in raw:
            post_id = str(r.get("id", "")).strip()
            if not post_id or post_id in self._seen:
                continue

            text = str(r.get("text", ""))
            published_raw = r.get("published_at")
            try:
                published_at = (
                    datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                    if isinstance(published_raw, str)
                    else now
                )
            except ValueError:
                published_at = now

            posts.append(NewsPost(
                post_id=post_id,
                source=self.source_name,
                author=TRUTH_AUTHOR,
                published_at=published_at,
                ingested_at=now,
                raw_text=text,
                url=r.get("url"),
                content_hash=compute_content_hash(text),
            ))
            self._seen.add(post_id)

        return posts


# --- Helpers for real-world HTML parsing (tested indirectly via fixture) ---

_POST_BLOCK = re.compile(
    r'<article[^>]*data-post-id="(?P<id>[^"]+)"[^>]*>(?P<inner>.*?)</article>',
    re.DOTALL,
)
_TEXT_BLOCK = re.compile(r'<div class="status__content">(?P<text>.*?)</div>', re.DOTALL)
_TIMESTAMP = re.compile(r'datetime="(?P<ts>[^"]+)"')


def parse_trumpstruth_html(html: str) -> list[RawPost]:
    """Extract posts from trumpstruth.org HTML. Tolerant to minor drift."""
    posts: list[RawPost] = []
    for m in _POST_BLOCK.finditer(html):
        inner = m.group("inner")
        text_m = _TEXT_BLOCK.search(inner)
        ts_m = _TIMESTAMP.search(inner)
        if not text_m or not ts_m:
            continue
        text = re.sub(r"<[^>]+>", "", text_m.group("text")).strip()
        posts.append({
            "id": m.group("id"),
            "text": text,
            "published_at": ts_m.group("ts"),
            "url": f"{ARCHIVE_URL}#{m.group('id')}",
        })
    return posts
