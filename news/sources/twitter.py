"""Twitter / X source adapter.

Phase 6A: structural adapter — API credentials not wired in this phase.
Actual HTTP polling deferred to 6E. This phase validates the data flow
(fetch → filter → dedup → list[NewsPost]).

The adapter takes a `fetch_raw` callable so tests can inject fixtures
without needing a tweepy client.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable

from news.sources import NewsPost, compute_content_hash
from news.sources.watchlist import is_watched


RawTweet = dict  # {"id": str, "author": "@handle", "text": str, "created_at": iso8601, "url": str}


class TwitterSource:
    source_name: str = "twitter"

    def __init__(
        self,
        fetch_raw: Callable[[], Iterable[RawTweet]],
        *,
        seen_ids: set[str] | None = None,
    ) -> None:
        self._fetch_raw = fetch_raw
        self._seen: set[str] = set(seen_ids or ())

    def fetch(self) -> list[NewsPost]:
        try:
            raw = list(self._fetch_raw())
        except Exception:
            return []

        posts: list[NewsPost] = []
        now = datetime.now(timezone.utc)

        for r in raw:
            author = str(r.get("author", "")).strip()
            # Filter BEFORE hash/DB work — R3
            if not is_watched(self.source_name, author):
                continue

            post_id = str(r.get("id", "")).strip()
            if not post_id or post_id in self._seen:
                continue

            text = str(r.get("text", ""))
            published_raw = r.get("created_at")
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
                author=author,
                published_at=published_at,
                ingested_at=now,
                raw_text=text,
                url=r.get("url"),
                content_hash=compute_content_hash(text),
            ))
            self._seen.add(post_id)

        return posts
