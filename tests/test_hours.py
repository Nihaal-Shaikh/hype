"""Tests for hype_bot.is_open_now() — CL schedule boundary conditions.

Per Phase 3 plan Amendment 3 and Amendment 7, this is the ONE pytest file
in Phase 3. Scope is deliberately narrow: exactly 8 boundary assertions
for the WTI Crude (COMMODITY) schedule. Other asset classes are loose
and not tested here — Phase 5 adds broader coverage.

Run with:
    source venv/bin/activate
    pytest tests/test_hours.py -v
"""
from datetime import datetime, timezone

from hype_bot import AssetClass, is_open_now


def _dt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Helper: build a UTC datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# --- CL (WTI crude) schedule boundary tests ------------------------------
# CME Globex WTI exact schedule (all UTC):
#   Sun 23:00 -> Mon 22:00  (with 22:00-23:00 daily break)
#   Mon 23:00 -> Tue 22:00  (same break)
#   ...through Thu 23:00 -> Fri 22:00
#   Saturday: closed all day
#   Sunday before 23:00: closed
#
# Calendar anchor dates used below (2026-04-11 era):
#   2026-04-11 is Saturday (wd=5)
#   2026-04-12 is Sunday   (wd=6)
#   2026-04-13 is Monday   (wd=0)
#   2026-04-14 is Tuesday  (wd=1)
#   2026-04-17 is Friday   (wd=4)


def test_cl_saturday_closed():
    """Saturday any time -> closed."""
    assert is_open_now(AssetClass.COMMODITY, _dt(2026, 4, 11, 1, 30)) is False


def test_cl_sunday_before_2300_closed():
    """Sunday 22:59 UTC -> still closed, session hasn't started."""
    assert is_open_now(AssetClass.COMMODITY, _dt(2026, 4, 12, 22, 59)) is False


def test_cl_sunday_2300_open():
    """Sunday 23:01 UTC -> session open (Globex week starts)."""
    assert is_open_now(AssetClass.COMMODITY, _dt(2026, 4, 12, 23, 1)) is True


def test_cl_monday_mid_session_open():
    """Monday 15:00 UTC -> mid-session, open."""
    assert is_open_now(AssetClass.COMMODITY, _dt(2026, 4, 13, 15, 0)) is True


def test_cl_monday_daily_break_closed():
    """Monday 22:30 UTC -> inside daily maintenance break, closed."""
    assert is_open_now(AssetClass.COMMODITY, _dt(2026, 4, 13, 22, 30)) is False


def test_cl_tuesday_after_break_open():
    """Tuesday 23:01 UTC -> after daily break, new session, open."""
    assert is_open_now(AssetClass.COMMODITY, _dt(2026, 4, 14, 23, 1)) is True


def test_cl_friday_before_close_open():
    """Friday 21:59 UTC -> just before weekly close, still open."""
    assert is_open_now(AssetClass.COMMODITY, _dt(2026, 4, 17, 21, 59)) is True


def test_cl_friday_after_close_closed():
    """Friday 22:01 UTC -> weekly close happened, closed for the weekend."""
    assert is_open_now(AssetClass.COMMODITY, _dt(2026, 4, 17, 22, 1)) is False


# --- Sanity checks for the other asset classes (not in the required 8, ---
# --- but cheap and catch obvious regressions) -----------------------------


def test_crypto_always_open():
    """Crypto is 24/7."""
    assert is_open_now(AssetClass.CRYPTO, _dt(2026, 4, 11, 1, 30)) is True
    assert is_open_now(AssetClass.CRYPTO, _dt(2026, 4, 12, 22, 59)) is True


def test_unknown_always_closed():
    """Unknown asset class defaults to closed (conservative)."""
    assert is_open_now(AssetClass.UNKNOWN, _dt(2026, 4, 13, 15, 0)) is False
