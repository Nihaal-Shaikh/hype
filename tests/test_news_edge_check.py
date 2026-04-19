"""Tests for Phase 6A-prime — mock classifier + replay + edge check."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from backtest_news import DELAY_GRID_SECONDS, GO_THRESHOLD, build_report, load_archive
from news.mock_classifier import RULES, classify
from news.replay import replay_signals, summarize_by_market
from news.sources import NewsPost, compute_content_hash


FIXTURES = Path(__file__).parent / "fixtures"


def _post(
    *, source: str, author: str, text: str, ts: datetime | None = None,
    post_id: str = "x1",
) -> NewsPost:
    ts = ts or datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    return NewsPost(
        post_id=post_id, source=source, author=author,
        published_at=ts, ingested_at=ts,
        raw_text=text, url=None,
        content_hash=compute_content_hash(text),
    )


def _synthetic_candles(start: datetime, n: int = 240, base_price: float = 100.0,
                        direction: str = "flat") -> pd.DataFrame:
    rows = []
    price = base_price
    for i in range(n):
        if direction == "up":
            price *= 1.001
        elif direction == "down":
            price *= 0.999
        rows.append({
            "time": start + timedelta(hours=i),
            "open": price * 0.999, "high": price * 1.001,
            "low": price * 0.998, "close": price, "volume": 1000.0,
        })
    return pd.DataFrame(rows)


# --- Mock classifier -----------------------------------------------------

def test_mock_classifier_deterministic():
    """Same input → same output, always."""
    p = _post(source="truth_social", author="realDonaldTrump",
              text="Massive tariff on imports.")
    r1 = classify(p)
    r2 = classify(p)
    assert r1 == r2
    assert len(r1) >= 1


def test_mock_classifier_tariff_rule():
    p = _post(source="truth_social", author="realDonaldTrump",
              text="New tariff on steel coming!")
    signals = classify(p)
    markets = {s.market for s in signals}
    assert "xyz:SP500" in markets
    assert "xyz:GOLD" in markets


def test_mock_classifier_peace_rule():
    p = _post(source="truth_social", author="realDonaldTrump",
              text="Peace deal with Iran is done. War is over!")
    signals = classify(p)
    markets = {s.market for s in signals}
    assert "xyz:CL" in markets
    assert "xyz:BRENTOIL" in markets


def test_mock_classifier_war_rule():
    p = _post(source="truth_social", author="realDonaldTrump",
              text="Military strike in response to attack.")
    signals = classify(p)
    markets = {s.market for s in signals}
    assert "xyz:CL" in markets


def test_mock_classifier_no_match_on_noise():
    p = _post(source="truth_social", author="realDonaldTrump",
              text="Had a great day golfing!")
    assert classify(p) == []


def test_mock_classifier_filters_wrong_author():
    p = _post(source="twitter", author="@randomuser",
              text="New tariff announcement!")
    assert classify(p) == []


def test_mock_classifier_rules_non_empty():
    assert len(RULES) >= 5


# --- Replay --------------------------------------------------------------

def test_replay_applies_ingestion_delay():
    """Entry price must be from the candle at (published + delay), not at published."""
    base = datetime(2026, 4, 10, 0, 0, tzinfo=timezone.utc)
    candles = _synthetic_candles(base, n=100, base_price=100.0)
    # Force a price jump after 2 hours — signal published at hour 1, ingested 60s later
    candles.loc[2:, "close"] = 150.0
    candles.loc[2:, "open"] = 150.0
    candles.loc[2:, "high"] = 151.0
    candles.loc[2:, "low"] = 149.0

    p = _post(source="truth_social", author="realDonaldTrump",
              text="Peace deal is done, war is over. Oil will crash!",
              ts=base + timedelta(hours=1),
              post_id="delay1")

    # Both delays should pick an entry candle at/after (hour1 + delay), which
    # is still candle[1] (price 100). Exit at hour 1.5 → still candle[1] (100).
    # Not useful for assertion. Use longer delay so entry shifts.
    trades_fast = replay_signals([p], {"xyz:CL": candles},
                                 ingestion_delay_seconds=0,
                                 hold_seconds=30 * 60)
    trades_slow = replay_signals([p], {"xyz:CL": candles},
                                 ingestion_delay_seconds=60 * 60 + 60,
                                 hold_seconds=30 * 60)
    # Delay across the price jump should produce different entry prices
    assert len(trades_fast) >= 1
    assert len(trades_slow) >= 1
    fast_entry = trades_fast[0].entry_price
    slow_entry = trades_slow[0].entry_price
    assert fast_entry != slow_entry


def test_replay_bullish_long_profits_on_uptrend():
    base = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    candles = _synthetic_candles(base, n=200, base_price=100.0, direction="up")
    p = _post(source="truth_social", author="realDonaldTrump",
              text="Military strike against adversary!",
              ts=base + timedelta(hours=5),
              post_id="bullish1")
    # War rule → xyz:CL BULLISH. Use 8-hour hold so entry vs exit prices
    # differ by several candles of 0.1%/hour drift.
    trades = replay_signals([p], {"xyz:CL": candles},
                            ingestion_delay_seconds=60, hold_seconds=8 * 60 * 60)
    cl_trades = [t for t in trades if t.market == "xyz:CL"]
    assert len(cl_trades) == 1
    # Long on uptrend → gross positive
    assert cl_trades[0].gross_pnl > 0


def test_replay_bearish_short_profits_on_downtrend():
    base = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    candles = _synthetic_candles(base, n=200, base_price=100.0, direction="down")
    p = _post(source="truth_social", author="realDonaldTrump",
              text="Peace deal with Iran!",
              ts=base + timedelta(hours=5),
              post_id="bearish1")
    # Peace rule → xyz:CL BEARISH. 8-hour hold to span enough candles.
    trades = replay_signals([p], {"xyz:CL": candles},
                            ingestion_delay_seconds=60, hold_seconds=8 * 60 * 60)
    cl_trades = [t for t in trades if t.market == "xyz:CL"]
    assert len(cl_trades) == 1
    assert cl_trades[0].gross_pnl > 0


def test_replay_missing_market_skipped():
    base = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    candles_only_btc = _synthetic_candles(base, n=50)
    p = _post(source="truth_social", author="realDonaldTrump",
              text="New tariff on steel!",
              ts=base + timedelta(hours=3),
              post_id="miss1")
    trades = replay_signals([p], {"BTC": candles_only_btc},
                            ingestion_delay_seconds=60)
    # Tariff rule produces signals for xyz:SP500 + xyz:GOLD; neither in candles
    assert trades == []


def test_summarize_by_market_shape():
    base = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    candles = _synthetic_candles(base, n=100, base_price=100.0, direction="up")
    posts = [
        _post(source="truth_social", author="realDonaldTrump",
              text="Military strike!",
              ts=base + timedelta(hours=i * 5),
              post_id=f"s{i}")
        for i in range(1, 4)
    ]
    trades = replay_signals(posts, {"xyz:CL": candles},
                            ingestion_delay_seconds=60, hold_seconds=60 * 60)
    stats = summarize_by_market(trades)
    assert len(stats) >= 1
    cl = next(s for s in stats if s.market == "xyz:CL")
    assert cl.trades == 3
    assert 0.0 <= cl.win_rate <= 1.0


# --- Edge decay + report -------------------------------------------------

def test_edge_decay_grid():
    """DELAY_GRID_SECONDS must have 5 rows per spec."""
    assert len(DELAY_GRID_SECONDS) == 5
    assert DELAY_GRID_SECONDS == (0, 30, 60, 120, 300)


def test_go_nogo_threshold_honored():
    """Threshold constants match the committed gate."""
    assert GO_THRESHOLD["oos_sharpe_minimum"] == 0.5
    assert GO_THRESHOLD["delay_seconds"] == 60
    assert GO_THRESHOLD["min_markets_passing"] == 2
    assert GO_THRESHOLD["min_trades_per_market"] == 20


def test_build_report_marks_synthetic(tmp_path: Path):
    # Empty stats — decision should default to NO-GO
    report = build_report(
        archive_paths=[Path("tests/fixtures/x.jsonl")],
        results_by_delay={d: [] for d in DELAY_GRID_SECONDS},
        universe=["BTC", "xyz:CL"],
        synthetic=True,
    )
    assert report["synthetic_fixture"] is True
    assert "STRUCTURAL-VALIDATION-ONLY" in report["note"]
    assert report["go_decision"] == "NO-GO"
    assert report["threshold"]["oos_sharpe_minimum"] == 0.5
    assert len(report["delay_grid_seconds"]) == 5


def test_build_report_go_when_threshold_met():
    from news.replay import MarketStats
    strong = [
        MarketStats(market="BTC", trades=25, total_net_pnl=5.0,
                    win_rate=0.6, mean_return_pct=0.1, sharpe=0.8, max_drawdown_pct=2.0),
        MarketStats(market="xyz:CL", trades=30, total_net_pnl=8.0,
                    win_rate=0.55, mean_return_pct=0.15, sharpe=0.9, max_drawdown_pct=3.0),
        MarketStats(market="xyz:GOLD", trades=10, total_net_pnl=1.0,
                    win_rate=0.4, mean_return_pct=0.05, sharpe=0.3, max_drawdown_pct=4.0),
    ]
    results = {d: strong for d in DELAY_GRID_SECONDS}
    report = build_report(
        archive_paths=[Path("real.jsonl")],
        results_by_delay=results,
        universe=["BTC", "xyz:CL", "xyz:GOLD"],
        synthetic=False,
    )
    assert report["go_decision"] == "GO"
    assert set(report["markets_passing_at_baseline_delay"]) == {"BTC", "xyz:CL"}


# --- Archive loader ------------------------------------------------------

def test_load_archive_parses_fixture():
    path = FIXTURES / "truth_social_archive_60d.jsonl"
    posts = load_archive(path)
    assert len(posts) >= 15
    assert all(p.source == "truth_social" for p in posts)
    assert all(p.author == "realDonaldTrump" for p in posts)
    assert all(p.published_at.tzinfo is not None for p in posts)
