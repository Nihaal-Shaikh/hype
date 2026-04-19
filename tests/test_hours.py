"""Tests for hype_bot.is_open_now() + is_prime_session().

Hyperliquid perps trade 24/7 regardless of the underlying's session
schedule. Updated in Phase 6A follow-up:
  - is_open_now       returns True for all known asset classes
  - is_prime_session  encodes the CME/NYSE-style reference schedule

Run:  pytest tests/test_hours.py -v
"""
from datetime import datetime, timezone

from hype_bot import AssetClass, is_open_now, is_prime_session


def _dt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# --- is_open_now: Hyperliquid trades 24/7 for every non-UNKNOWN class ----

def test_open_now_crypto_anytime():
    assert is_open_now(AssetClass.CRYPTO, _dt(2026, 4, 11, 1, 30)) is True


def test_open_now_commodity_saturday():
    """Previously False. Hyperliquid xyz:CL trades weekends — this must be True."""
    assert is_open_now(AssetClass.COMMODITY, _dt(2026, 4, 11, 1, 30)) is True


def test_open_now_commodity_sunday_before_2300():
    assert is_open_now(AssetClass.COMMODITY, _dt(2026, 4, 12, 22, 59)) is True


def test_open_now_stock_on_weekend():
    """Hyperliquid single-name perps (xyz:TSLA etc.) trade weekends too."""
    assert is_open_now(AssetClass.STOCK, _dt(2026, 4, 11, 10, 0)) is True


def test_open_now_unknown_still_closed():
    """Conservative: unknown asset class still returns False."""
    assert is_open_now(AssetClass.UNKNOWN, _dt(2026, 4, 13, 15, 0)) is False


# --- is_prime_session: CME/NYSE schedule (liquidity gate, NOT open/close) -

def test_prime_cl_saturday_not_prime():
    assert is_prime_session(AssetClass.COMMODITY, _dt(2026, 4, 11, 1, 30)) is False


def test_prime_cl_sunday_before_2300_not_prime():
    assert is_prime_session(AssetClass.COMMODITY, _dt(2026, 4, 12, 22, 59)) is False


def test_prime_cl_sunday_2300_prime():
    assert is_prime_session(AssetClass.COMMODITY, _dt(2026, 4, 12, 23, 1)) is True


def test_prime_cl_monday_mid_session_prime():
    assert is_prime_session(AssetClass.COMMODITY, _dt(2026, 4, 13, 15, 0)) is True


def test_prime_cl_monday_daily_break_not_prime():
    assert is_prime_session(AssetClass.COMMODITY, _dt(2026, 4, 13, 22, 30)) is False


def test_prime_cl_tuesday_after_break_prime():
    assert is_prime_session(AssetClass.COMMODITY, _dt(2026, 4, 14, 23, 1)) is True


def test_prime_cl_friday_before_close_prime():
    assert is_prime_session(AssetClass.COMMODITY, _dt(2026, 4, 17, 21, 59)) is True


def test_prime_cl_friday_after_close_not_prime():
    assert is_prime_session(AssetClass.COMMODITY, _dt(2026, 4, 17, 22, 1)) is False


def test_prime_crypto_always_prime():
    assert is_prime_session(AssetClass.CRYPTO, _dt(2026, 4, 11, 1, 30)) is True
    assert is_prime_session(AssetClass.CRYPTO, _dt(2026, 4, 12, 22, 59)) is True


def test_prime_stock_weekend_not_prime():
    assert is_prime_session(AssetClass.STOCK, _dt(2026, 4, 11, 15, 0)) is False


def test_prime_stock_weekday_rth_prime():
    assert is_prime_session(AssetClass.STOCK, _dt(2026, 4, 13, 15, 0)) is True


def test_prime_unknown_not_prime():
    assert is_prime_session(AssetClass.UNKNOWN, _dt(2026, 4, 13, 15, 0)) is False
