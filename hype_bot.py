"""
hype_bot — shared module for the Hyperliquid multi-asset trading bot.

Flat file at repo root. Imported as `from hype_bot import X` from any
top-level script. No package, no src/ tree, no editable install — see
.omc/plans/ralplan-phase3-multidex.md Decision 1 for rationale.

Phase 3 scope:
- Dex registry (DEPLOYED_DEXES, ACTIVE_DEXES)
- Asset class enum + classifier for xyz dex
- Market hours stub (exact CL schedule, loose elsewhere)
- Info/Exchange client factories with multi-dex support
- Multi-dex balance + mid query helpers
- TradableMarket dataclass definition (NOT a populator — Phase 5 work)
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
import keyring
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants


# --- Constants -----------------------------------------------------------

KEYRING_SERVICE = "hl-bot"
KEYRING_ACCOUNT = "agent-private-key"

# The 9 deployed perp dexes on Hyperliquid mainnet.
# Empty string "" = the core/original crypto dex.
DEPLOYED_DEXES: list[str] = [
    "",      # core (229 crypto perps)
    "xyz",   # XYZ — stocks, commodities, forex, indices (62 markets)
    "flx",   # Felix Exchange
    "hyna",  # HyENA
    "km",    # Markets by Kinetiq
    "cash",  # dreamcash
    "vntl",  # Ventuals — thematic + private co exposure (OPENAI, ANTHROPIC, SPACEX)
    "para",  # Paragon — crypto dominance indices
    "abcd",  # ABCDEx (empty, newly deploying)
]

# Subset we actually use for reads/writes in Phase 3.
# Extend this list as we onboard more dexes in later phases.
ACTIVE_DEXES: list[str] = ["", "xyz"]


# --- Asset class classification ------------------------------------------

class AssetClass(enum.Enum):
    CRYPTO = "crypto"
    STOCK = "stock"
    COMMODITY = "commodity"
    FOREX = "forex"
    INDEX = "index"
    UNKNOWN = "unknown"


# xyz dex asset class map, based on inspection of the 62-market xyz universe.
# Keys are the BARE symbol (no "xyz:" prefix) since the prefix is implied
# by the dex parameter passed to classify().
_XYZ_ASSET_CLASS: dict[str, AssetClass] = {
    # Stocks — individual equities
    "AAPL": AssetClass.STOCK,
    "NVDA": AssetClass.STOCK,
    "TSLA": AssetClass.STOCK,
    "MSFT": AssetClass.STOCK,
    "GOOGL": AssetClass.STOCK,
    "AMZN": AssetClass.STOCK,
    "META": AssetClass.STOCK,
    "NFLX": AssetClass.STOCK,
    "AMD": AssetClass.STOCK,
    "INTC": AssetClass.STOCK,
    "ORCL": AssetClass.STOCK,
    "COIN": AssetClass.STOCK,
    "MSTR": AssetClass.STOCK,
    "COST": AssetClass.STOCK,
    "LLY": AssetClass.STOCK,
    "TSM": AssetClass.STOCK,
    "BABA": AssetClass.STOCK,
    "MU": AssetClass.STOCK,
    "SNDK": AssetClass.STOCK,
    "CRCL": AssetClass.STOCK,
    "PLTR": AssetClass.STOCK,
    "RIVN": AssetClass.STOCK,
    "HIMS": AssetClass.STOCK,
    "HOOD": AssetClass.STOCK,
    "DKNG": AssetClass.STOCK,
    "HYUNDAI": AssetClass.STOCK,
    "SMSN": AssetClass.STOCK,
    "SKHX": AssetClass.STOCK,
    "CRWV": AssetClass.STOCK,
    "USAR": AssetClass.STOCK,
    "KIOXIA": AssetClass.STOCK,
    "SOFTBANK": AssetClass.STOCK,
    "GME": AssetClass.STOCK,
    "BX": AssetClass.STOCK,
    "MRVL": AssetClass.STOCK,
    "LITE": AssetClass.STOCK,
    # Commodities
    "CL": AssetClass.COMMODITY,          # WTI Crude Oil
    "BRENTOIL": AssetClass.COMMODITY,    # Brent Crude
    "NATGAS": AssetClass.COMMODITY,      # Henry Hub natgas
    "GOLD": AssetClass.COMMODITY,
    "SILVER": AssetClass.COMMODITY,
    "PLATINUM": AssetClass.COMMODITY,
    "PALLADIUM": AssetClass.COMMODITY,
    "COPPER": AssetClass.COMMODITY,
    "ALUMINIUM": AssetClass.COMMODITY,
    "URANIUM": AssetClass.COMMODITY,
    "URNM": AssetClass.COMMODITY,        # uranium miners ETF basket
    "WHEAT": AssetClass.COMMODITY,
    "CORN": AssetClass.COMMODITY,
    "TTF": AssetClass.COMMODITY,         # Dutch TTF natural gas
    # Forex
    "JPY": AssetClass.FOREX,
    "EUR": AssetClass.FOREX,
    "DXY": AssetClass.FOREX,             # US dollar index
    # Indices
    "SP500": AssetClass.INDEX,
    "JP225": AssetClass.INDEX,
    "KR200": AssetClass.INDEX,
    "XYZ100": AssetClass.INDEX,
    "EWJ": AssetClass.INDEX,             # iShares Japan ETF
    "EWY": AssetClass.INDEX,             # iShares Korea ETF
    "XLE": AssetClass.INDEX,             # energy sector ETF
    "VIX": AssetClass.INDEX,
    "PURRDAT": AssetClass.UNKNOWN,
}


def classify(dex: str, symbol: str) -> AssetClass:
    """Return the asset class for a given (dex, symbol) pair.

    Core dex ("") is all crypto.
    xyz dex consults _XYZ_ASSET_CLASS.
    Other deployed dexes default to UNKNOWN (to be filled in later phases).

    `symbol` may be either the full prefixed name ("xyz:CL") or the bare
    symbol ("CL"). Both forms are accepted.
    """
    if dex == "":
        return AssetClass.CRYPTO
    if dex == "xyz":
        bare = symbol.split(":", 1)[1] if ":" in symbol else symbol
        return _XYZ_ASSET_CLASS.get(bare, AssetClass.UNKNOWN)
    return AssetClass.UNKNOWN


# --- Market hours stub ---------------------------------------------------

def is_open_now(asset_class: AssetClass, now: datetime | None = None) -> bool:
    """Is the given asset class currently tradable?

    Phase 3 stub: the COMMODITY case encodes the exact CME Globex WTI
    schedule (see tests/test_hours.py). Other asset classes use loose
    heuristics, which is acceptable because Phase 3's only write test
    targets CL specifically. Phase 5 will replace this with
    pandas_market_calendars for full accuracy across all classes.

    All times are UTC.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    assert now.tzinfo is not None, "now must be timezone-aware (UTC preferred)"

    wd = now.weekday()   # Mon=0, ..., Sat=5, Sun=6
    hh = now.hour
    mm = now.minute
    hhmm = hh * 100 + mm

    if asset_class == AssetClass.CRYPTO:
        # 24/7
        return True

    if asset_class == AssetClass.COMMODITY:
        # CL (WTI Crude) on CME Globex — exact schedule per Phase 3 plan Amendment 3:
        #   Sun 23:00 UTC -> Mon 22:00 UTC  (with 22:00-23:00 UTC daily break)
        #   Mon 23:00 UTC -> Tue 22:00 UTC  (same break)
        #   Tue 23:00 UTC -> Wed 22:00 UTC
        #   Wed 23:00 UTC -> Thu 22:00 UTC
        #   Thu 23:00 UTC -> Fri 22:00 UTC  (weekend close at 22:00 UTC Fri)
        #   Saturday: CLOSED all day
        #   Sunday before 23:00 UTC: CLOSED
        if wd == 5:  # Saturday
            return False
        if wd == 6:  # Sunday — opens at 23:00 UTC
            return hh >= 23
        if wd == 4:  # Friday — closes at 22:00 UTC
            return hh < 22
        # Mon-Thu: closed during daily maintenance break 22:00-22:59 UTC
        if hh == 22:
            return False
        return True

    if asset_class == AssetClass.STOCK:
        # NYSE RTH ~ 13:30-20:00 UTC Mon-Fri (varies with DST — loose stub, Phase 5 replaces)
        if wd >= 5:
            return False
        return 1330 <= hhmm < 2000

    if asset_class == AssetClass.FOREX:
        # 24/5: Sun 22:00 UTC -> Fri 22:00 UTC, closed Sat and early Sun
        if wd == 5:
            return False
        if wd == 6:
            return hh >= 22
        if wd == 4:
            return hh < 22
        return True

    if asset_class == AssetClass.INDEX:
        # Approximate same as stocks (SP500, JP225, etc. have their own sessions
        # — Phase 5 handles per-index accuracy)
        if wd >= 5:
            return False
        return 1330 <= hhmm < 2000

    return False  # UNKNOWN


# --- Credentials + client factories --------------------------------------

def load_main_address() -> str:
    """Load HL_MAIN_ADDRESS from .env. Raises if missing."""
    load_dotenv()
    addr = os.environ.get("HL_MAIN_ADDRESS", "").strip()
    if not addr:
        raise RuntimeError("HL_MAIN_ADDRESS not set in .env")
    return addr


def load_agent_key_from_keychain() -> str:
    """Load agent private key from macOS Keychain. Raises if missing."""
    key = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    if not key:
        raise RuntimeError(
            f"Agent private key not found in macOS Keychain.\n"
            f"Store it with: keyring set {KEYRING_SERVICE} {KEYRING_ACCOUNT}"
        )
    return key.strip()


def make_info(dexes: Iterable[str] | None = None) -> Info:
    """Construct a read-only Info client with the given dexes loaded.

    Default is ACTIVE_DEXES. The dexes argument controls which perp dex
    universes get populated into name_to_coin and related lookups —
    required for l2_snapshot() on non-core symbols (see phase3_sdk_survey.md).
    """
    dex_list = list(dexes) if dexes is not None else ACTIVE_DEXES
    return Info(constants.MAINNET_API_URL, skip_ws=True, perp_dexs=dex_list)


def make_exchange(dexes: Iterable[str] | None = None) -> Exchange:
    """Construct an authenticated Exchange client for writing.

    Wallet is the agent (loaded from macOS Keychain).
    account_address is the main wallet (loaded from .env).
    perp_dexs controls which dexes can be traded on this client.

    The returned Exchange CAN place L1 actions (orders, cancels, etc.) but
    CANNOT sign user-signed actions (transfers, dex abstraction opt-in, etc.)
    — those require the main wallet signature, which lives in Rabby.
    """
    main = load_main_address()
    agent_key = load_agent_key_from_keychain()
    agent = Account.from_key(agent_key)
    dex_list = list(dexes) if dexes is not None else ACTIVE_DEXES
    return Exchange(
        wallet=agent,
        base_url=constants.MAINNET_API_URL,
        account_address=main,
        perp_dexs=dex_list,
    )


# --- Multi-dex state queries ---------------------------------------------

@dataclass(frozen=True)
class DexBalance:
    """Balance snapshot for a single dex from a user's perspective."""
    dex: str                # "core" for "", or the dex name like "xyz"
    account_value: float    # perps account value in USD
    withdrawable: float
    position_count: int
    spot_usdc: float        # only meaningful on core dex; 0 elsewhere


def get_balance_all_dexes(
    info: Info,
    address: str,
    dexes: Iterable[str] | None = None,
) -> list[DexBalance]:
    """Query balance + position count across the given dexes.

    Returns a list of DexBalance instances, one per dex, in the input order.
    """
    dex_list = list(dexes) if dexes is not None else ACTIVE_DEXES
    result: list[DexBalance] = []
    for dex in dex_list:
        perp = info.user_state(address, dex=dex)
        ms = perp.get("marginSummary", {})
        account_value = float(ms.get("accountValue", "0"))
        withdrawable = float(perp.get("withdrawable", "0"))
        positions = perp.get("assetPositions", [])

        spot_usdc = 0.0
        if dex == "":  # spot only exists on core
            try:
                spot = info.spot_user_state(address)
                for b in spot.get("balances", []):
                    if b.get("coin") == "USDC":
                        spot_usdc = float(b.get("total", "0"))
                        break
            except Exception:
                pass

        result.append(DexBalance(
            dex=dex or "core",
            account_value=account_value,
            withdrawable=withdrawable,
            position_count=len(positions),
            spot_usdc=spot_usdc,
        ))
    return result


def get_mids_all_dexes(
    info: Info,
    dexes: Iterable[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Query mid prices for all loaded dexes.

    Returns {dex_label: {symbol: price}}. dex_label is "core" for "" or the
    dex name like "xyz".

    Note: info.all_mids() with no dex arg returns core only (confirmed in
    research/phase3_sdk_probe.md Section 2), so we must call once per dex.
    """
    dex_list = list(dexes) if dexes is not None else ACTIVE_DEXES
    result: dict[str, dict[str, float]] = {}
    for dex in dex_list:
        mids = info.all_mids(dex=dex)
        result[dex or "core"] = {k: float(v) for k, v in mids.items()}
    return result


def get_open_orders_all_dexes(
    info: Info,
    address: str,
    dexes: Iterable[str] | None = None,
) -> dict[str, list[dict]]:
    """Query open orders across the given dexes.

    Returns {dex_label: [order_dicts]}. dex_label is "core" for "" or the
    dex name like "xyz".

    Note: info.open_orders() defaults to dex="" (core only). To see orders
    on xyz, you MUST pass dex="xyz". This was the root cause of the Phase 3
    probe seeing [] — the order was on xyz:CL but the query hit core.
    """
    dex_list = list(dexes) if dexes is not None else ACTIVE_DEXES
    result: dict[str, list[dict]] = {}
    for dex in dex_list:
        orders = info.open_orders(address, dex=dex)
        result[dex or "core"] = orders
    return result


# --- Candle data fetch ---------------------------------------------------

def fetch_candles(
    info: Info,
    coin: str,
    interval: str = "1h",
    lookback_hours: int = 48,
) -> pd.DataFrame:
    """Fetch candle data and return a normalized DataFrame.

    Columns: time (datetime), open, high, low, close, volume (all float).
    Sorted ascending by time. Empty DataFrame if no data returned.

    IMPORTANT: Uses raw field `t` (candle OPEN time), not `T` (candle
    close time). See SDK info.py:candles_snapshot() for the full schema.
    Matches the existing pattern in dashboard.py:92.

    NOTE: The `info` client must have been constructed with the relevant
    dex loaded (e.g., make_info(["", "xyz"]) for xyz:CL). A KeyError
    will occur if the coin's dex is not in the Info client's perp_dexs.
    """
    import time as _time
    now_ms = int(_time.time() * 1000)
    start_ms = now_ms - (lookback_hours * 60 * 60 * 1000)
    raw = info.candles_snapshot(coin, interval, start_ms, now_ms)
    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(raw)
    df["time"] = pd.to_datetime(df["t"], unit="ms")
    for col in ("o", "h", "l", "c", "v"):
        df[col] = pd.to_numeric(df[col])
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df[["time", "open", "high", "low", "close", "volume"]]


# --- TradableMarket (Phase 3E, definition only) --------------------------

@dataclass(frozen=True)
class TradableMarket:
    """Universal representation of a single tradable perp market.

    This is a DEFINITION only — Phase 3 does not include a build_universe()
    populator. Phase 3D constructs ONE instance for `xyz:CL` via
    `get_tradable_market()` below. Phase 5 will add a populator that
    iterates all dexes and all symbols; see open-questions.md.
    """
    dex: str                    # "" for core, "xyz" for xyz, etc.
    symbol: str                 # full prefixed name, e.g., "xyz:CL" or "BTC"
    asset_class: AssetClass
    max_leverage: int
    size_decimals: int
    min_notional: float         # Hyperliquid-wide minimum = $10
    current_mid: float | None   # None if not queryable at construction time
    open_now: bool              # per is_open_now(asset_class)


def get_tradable_market(info: Info, dex: str, symbol: str) -> TradableMarket:
    """Build a single TradableMarket for the given (dex, symbol).

    Raises ValueError if the symbol is not found in the dex's meta.
    """
    meta = info.meta(dex=dex)
    asset_info = None
    for a in meta.get("universe", []):
        full_name = a.get("name", "")
        # Match on either the full name ("xyz:CL") or the bare one ("CL")
        if full_name == symbol or full_name == symbol.split(":", 1)[-1]:
            asset_info = a
            break
    if asset_info is None:
        raise ValueError(f"Symbol {symbol!r} not found in dex {dex!r} meta")

    mids = info.all_mids(dex=dex)
    mid_raw = mids.get(symbol)
    current_mid = float(mid_raw) if mid_raw is not None else None

    ac = classify(dex, symbol)

    return TradableMarket(
        dex=dex,
        symbol=symbol,
        asset_class=ac,
        max_leverage=int(asset_info.get("maxLeverage", 10)),
        size_decimals=int(asset_info.get("szDecimals", 5)),
        min_notional=10.0,
        current_mid=current_mid,
        open_now=is_open_now(ac),
    )
