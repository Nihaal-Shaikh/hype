"""SQL DDL for Phase 6 news tables.

Six tables appended to the existing `.hype.db` (Phase 5G):
  news_posts              — raw ingested posts
  classifications         — LLM classifier outputs (Phase 6B)
  classification_errors   — failures (Phase 6B, architect R6)
  news_signals            — materialized (market, sentiment, decay) signals
  llm_spend               — per-day LLM cost tally for DailyBudget
  llm_deferred            — headlines deferred when budget exhausted

All tables use `CREATE IF NOT EXISTS` — idempotent.
"""

from __future__ import annotations

import sqlite3


_DDL = """
CREATE TABLE IF NOT EXISTS news_posts (
    post_id         TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    author          TEXT NOT NULL,
    published_at    TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    raw_text        TEXT NOT NULL,
    url             TEXT,
    content_hash    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_posts_hash ON news_posts(content_hash);
CREATE INDEX IF NOT EXISTS idx_news_posts_published ON news_posts(published_at);

CREATE TABLE IF NOT EXISTS classifications (
    content_hash        TEXT PRIMARY KEY,
    prompt_version      TEXT NOT NULL,
    model               TEXT NOT NULL,
    classified_at       TEXT NOT NULL,
    sentiment           TEXT NOT NULL,
    affected_markets    TEXT NOT NULL,
    confidence          REAL NOT NULL,
    tokens_in           INTEGER NOT NULL,
    tokens_out          INTEGER NOT NULL,
    cost_usd            REAL NOT NULL,
    latency_ms          INTEGER NOT NULL,
    raw_response_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classification_errors (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash        TEXT NOT NULL,
    prompt_version      TEXT NOT NULL,
    occurred_at         TEXT NOT NULL,
    error_code          TEXT NOT NULL,
    error_message       TEXT,
    raw_response        TEXT
);
CREATE INDEX IF NOT EXISTS idx_class_err_hash ON classification_errors(content_hash);
CREATE INDEX IF NOT EXISTS idx_class_err_time ON classification_errors(occurred_at);

CREATE TABLE IF NOT EXISTS news_signals (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id                 TEXT NOT NULL,
    market                  TEXT NOT NULL,
    sentiment               TEXT NOT NULL,
    confidence              REAL NOT NULL,
    created_at              TEXT NOT NULL,
    decay_at                TEXT NOT NULL,
    consumed_at             TEXT,
    news_arrival_delay_ms   INTEGER,
    FOREIGN KEY (post_id) REFERENCES news_posts(post_id)
);
CREATE INDEX IF NOT EXISTS idx_news_signals_decay ON news_signals(decay_at);
CREATE INDEX IF NOT EXISTS idx_news_signals_consumed ON news_signals(consumed_at);

CREATE TABLE IF NOT EXISTS llm_spend (
    date_utc        TEXT PRIMARY KEY,
    total_usd       REAL NOT NULL DEFAULT 0.0,
    call_count      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS llm_deferred (
    content_hash    TEXT PRIMARY KEY,
    deferred_at     TEXT NOT NULL,
    reason          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_deferred_at ON llm_deferred(deferred_at);
"""


# Table names applied — used by tests to verify all 6 tables exist.
NEWS_TABLES: tuple[str, ...] = (
    "news_posts",
    "classifications",
    "classification_errors",
    "news_signals",
    "llm_spend",
    "llm_deferred",
)


def apply_news_schema(conn: sqlite3.Connection) -> None:
    """Idempotent CREATE of all Phase 6 news tables + indexes."""
    conn.executescript(_DDL)
