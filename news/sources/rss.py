"""RSS feed source adapter.

Generic RSS adapter for Bloomberg, Reuters, Fed press releases, etc.
Feed URL is configured per-instance. Author key is the feed identifier
(e.g., "reuters:markets"), matched against the watchlist as
"rss:{feed_id}".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable

from news.sources import NewsPost, compute_content_hash
from news.sources.watchlist import is_watched


RawEntry = dict  # {"id": str, "title": str, "summary": str, "published": iso8601, "link": str}


class RssSource:
    source_name: str = "rss"

    def __init__(
        self,
        feed_id: str,               # e.g., "reuters:markets"
        fetch_raw: Callable[[], Iterable[RawEntry]],
        *,
        seen_ids: set[str] | None = None,
    ) -> None:
        self.feed_id = feed_id
        self._fetch_raw = fetch_raw
        self._seen: set[str] = set(seen_ids or ())

    def fetch(self) -> list[NewsPost]:
        if not is_watched(self.source_name, self.feed_id):
            return []

        try:
            raw = list(self._fetch_raw())
        except Exception:
            return []

        posts: list[NewsPost] = []
        now = datetime.now(timezone.utc)

        for r in raw:
            post_id = str(r.get("id") or r.get("link") or "").strip()
            if not post_id or post_id in self._seen:
                continue

            title = str(r.get("title", "")).strip()
            summary = str(r.get("summary", "")).strip()
            text = f"{title}. {summary}" if summary else title

            published_raw = r.get("published") or r.get("updated")
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
                author=self.feed_id,
                published_at=published_at,
                ingested_at=now,
                raw_text=text,
                url=r.get("link"),
                content_hash=compute_content_hash(text),
            ))
            self._seen.add(post_id)

        return posts
