"""Deterministic mock classifier for Phase 6A-prime edge check.

Hard-coded (source, author, keyword) → (market, sentiment) rules. No
LLM calls, zero API cost. Used ONLY to validate that the news → market
→ edge pipeline architecture works end-to-end, BEFORE we commit to 14h
of LLM infra in 6B.

RULES are git-committed BEFORE running against the archive (per plan
risk mitigation) to prevent cherry-picking. Any rule change should be
a separate commit with documented rationale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from news.sources import NewsPost


Sentiment = Literal["BULLISH", "BEARISH", "NEUTRAL"]


@dataclass(frozen=True)
class MockSignal:
    """One (market, sentiment) pair emitted from a post."""
    market: str
    sentiment: Sentiment
    rule_id: str      # for audit trail


@dataclass(frozen=True)
class Rule:
    """One classifier rule: source + author + regex + market signals."""
    rule_id: str
    source: str
    author: str
    pattern: re.Pattern[str]
    signals: tuple[tuple[str, Sentiment], ...]


# --- Rules (committed before any archive run) ----------------------------

# Rule syntax: rule_id, source, author, regex, [(market, sentiment), ...]
_RAW_RULES: list[tuple[str, str, str, str, list[tuple[str, Sentiment]]]] = [
    # Trump Truth Social — tariff posts → US equities down, gold up
    ("trump_tariff", "truth_social", "realDonaldTrump",
     r"\btariff",
     [("xyz:SP500", "BEARISH"), ("xyz:GOLD", "BULLISH")]),
    # Trump — peace / deal / ceasefire → oil down, gold down (risk-off unwinds)
    ("trump_peace", "truth_social", "realDonaldTrump",
     r"(?i)\b(peace\s+deal|ceasefire|war\s+(is\s+)?over|deal\s+done)\b",
     [("xyz:CL", "BEARISH"), ("xyz:BRENTOIL", "BEARISH"), ("xyz:GOLD", "BEARISH")]),
    # Trump — war / strike / attack → oil up, gold up
    ("trump_war", "truth_social", "realDonaldTrump",
     r"(?i)\b(strike|attack|war(?!\s+is\s+over)|military\s+action)\b",
     [("xyz:CL", "BULLISH"), ("xyz:BRENTOIL", "BULLISH"), ("xyz:GOLD", "BULLISH")]),
    # Fed on Twitter — rate cut → risk-on (BTC, SP500 up)
    ("fed_cut", "twitter", "@federalreserve",
     r"(?i)\b(rate\s+cut|cuts?\s+rates?|pause|holds?\s+steady|lower\s+rates?)\b",
     [("BTC", "BULLISH"), ("xyz:SP500", "BULLISH")]),
    # Fed on Twitter — rate hike → risk-off
    ("fed_hike", "twitter", "@federalreserve",
     r"(?i)\b(rate\s+hike|raise\s+rates?|hikes?\s+rates?|tightening)\b",
     [("BTC", "BEARISH"), ("xyz:SP500", "BEARISH")]),
    # Bloomberg — oil crash headlines
    ("bloomberg_oil_down", "rss", "bloomberg:markets",
     r"(?i)oil\s+(crashes|plunges|tumbles|drops|slides)",
     [("xyz:CL", "BEARISH"), ("xyz:BRENTOIL", "BEARISH")]),
    # Bloomberg — oil rally headlines
    ("bloomberg_oil_up", "rss", "bloomberg:markets",
     r"(?i)oil\s+(rallies|soars|jumps|climbs|surges)",
     [("xyz:CL", "BULLISH"), ("xyz:BRENTOIL", "BULLISH")]),
]


RULES: tuple[Rule, ...] = tuple(
    Rule(
        rule_id=rid,
        source=src,
        author=author,
        pattern=re.compile(pat),
        signals=tuple(sigs),
    )
    for (rid, src, author, pat, sigs) in _RAW_RULES
)


def classify(post: NewsPost) -> list[MockSignal]:
    """Apply rules, return all matching signals. Empty list if none match."""
    out: list[MockSignal] = []
    for rule in RULES:
        if post.source != rule.source:
            continue
        if post.author != rule.author:
            continue
        if not rule.pattern.search(post.raw_text):
            continue
        for market, sentiment in rule.signals:
            out.append(MockSignal(market=market, sentiment=sentiment, rule_id=rule.rule_id))
    return out
