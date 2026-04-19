"""Tests for Phase 6A news schema."""

from __future__ import annotations

from pathlib import Path

import pytest

import history
from news.schema import NEWS_TABLES, apply_news_schema


def test_init_db_creates_news_tables(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    with history.connect(db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    for t in NEWS_TABLES:
        assert t in tables, f"table {t} missing after init_db"


def test_init_db_is_idempotent(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    history.init_db(db)  # second call must not fail
    with history.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM news_posts").fetchone()[0]
    assert count == 0


def test_classification_errors_table_exists(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    with history.connect(db) as conn:
        cols = conn.execute("PRAGMA table_info(classification_errors)").fetchall()
    col_names = {c[1] for c in cols}
    required = {"id", "content_hash", "prompt_version", "occurred_at",
                "error_code", "error_message", "raw_response"}
    assert required.issubset(col_names)


def test_llm_deferred_has_deferred_at_index(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    with history.connect(db) as conn:
        idxs = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='llm_deferred'"
        ).fetchall()
    names = {i[0] for i in idxs}
    assert "idx_llm_deferred_at" in names


def test_news_signals_has_arrival_delay_column(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    with history.connect(db) as conn:
        cols = {c[1] for c in conn.execute("PRAGMA table_info(news_signals)").fetchall()}
    assert "news_arrival_delay_ms" in cols


def test_news_signals_decay_index_exists(tmp_path: Path):
    db = tmp_path / "x.db"
    history.init_db(db)
    with history.connect(db) as conn:
        idxs = {i[0] for i in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='news_signals'"
        ).fetchall()}
    assert "idx_news_signals_decay" in idxs
    assert "idx_news_signals_consumed" in idxs


def test_apply_news_schema_on_empty_db(tmp_path: Path):
    """apply_news_schema works even without legacy history tables."""
    import sqlite3
    db = tmp_path / "fresh.db"
    with sqlite3.connect(str(db)) as conn:
        apply_news_schema(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    for t in NEWS_TABLES:
        assert t in tables
