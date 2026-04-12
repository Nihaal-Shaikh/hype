"""
Phase 2.4 / 2.5 — Open a tiny market position, verify, then close it.

THIS IS THE FIRST SCRIPT THAT SPENDS REAL MONEY. A market buy fills
immediately at the best available ask. You will have a real BTC long
position for the ~10 seconds between the open and the close.

Expected cost of a successful roundtrip:
    - Taker fee: ~0.035% × notional × 2 (open + close) = ~$0.01
    - Spread: a couple cents depending on book depth
    - Slippage: usually <$0.05 for $15 orders on BTC (super deep book)
    Total realistic cost: $0.01 – $0.10

Worst reasonable case:
    - BTC moves 1% against you between open and close = $0.15 loss
    - That is your actual upper-bound tuition for this roundtrip

Flow:
    1. Fetch mid price and meta
    2. Compute size so notional ≈ --notional (default $15)
    3. Show pre-state (balances, open positions, proposed trade)
    4. Confirm 'yes' to open
    5. Place market buy via exchange.market_open(..., slippage=0.05)
    6. Show fill details and the new position
    7. Pause — you verify in Brave Beta that the position is real
    8. Confirm 'close' to close
    9. Close via exchange.market_close(coin)
   10. Show final realized PnL

Run with:
    source venv/bin/activate
    python roundtrip_market.py               # default: $15 BTC long roundtrip
    python roundtrip_market.py --coin ETH    # ETH instead
    python roundtrip_market.py --notional 12 # smaller (still above $10 min)
    python roundtrip_market.py --short       # open SHORT instead of LONG
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import keyring
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

KEYRING_SERVICE = "hl-bot"
KEYRING_ACCOUNT = "agent-private-key"

DEFAULT_COIN = "BTC"
DEFAULT_NOTIONAL_USD = 15.0
MIN_NOTIONAL_USD = 10.0


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"ERROR: {name} is not set in .env")
    return value


def _get_agent_key() -> str:
    key = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    if not key:
        sys.exit(
            "ERROR: agent private key not found in macOS Keychain.\n"
            f"Store it with: keyring set {KEYRING_SERVICE} {KEYRING_ACCOUNT}"
        )
    return key.strip()


def _get_sz_decimals(info: Info, coin: str) -> int:
    meta = info.meta()
    for asset in meta.get("universe", []):
        if asset.get("name") == coin:
            return int(asset.get("szDecimals", 5))
    sys.exit(f"ERROR: could not find {coin} in perps meta")


def _ceil_to_decimals(value: float, decimals: int) -> float:
    """Round UP to the asset's szDecimals so the notional stays >= target."""
    factor = 10 ** decimals
    import math
    return math.ceil(value * factor) / factor


def _fetch_account_snapshot(info: Info, address: str) -> dict:
    perp = info.user_state(address)
    spot = info.spot_user_state(address)
    spot_usdc = next(
        (float(b["total"]) for b in spot.get("balances", []) if b["coin"] == "USDC"),
        0.0,
    )
    margin = perp.get("marginSummary", {})
    return {
        "account_value": float(margin.get("accountValue", "0")),
        "margin_used": float(margin.get("totalMarginUsed", "0")),
        "withdrawable": float(perp.get("withdrawable", "0")),
        "positions": perp.get("assetPositions", []),
        "spot_usdc": spot_usdc,
    }


def _find_position(snapshot: dict, coin: str) -> dict | None:
    for p in snapshot["positions"]:
        pos = p.get("position", {})
        if pos.get("coin") == coin and float(pos.get("szi", "0")) != 0:
            return pos
    return None


def _print_snapshot(label: str, snap: dict, coin: str) -> None:
    print(f"\n[{label}]")
    print(f"  Spot USDC:       ${snap['spot_usdc']:,.4f}")
    print(f"  Perp acct value: ${snap['account_value']:,.4f}")
    print(f"  Withdrawable:    ${snap['withdrawable']:,.4f}")
    pos = _find_position(snap, coin)
    if pos:
        size = float(pos["szi"])
        entry = pos.get("entryPx", "?")
        upnl = pos.get("unrealizedPnl", "?")
        print(f"  {coin} position:    size={size}  entry=${entry}  uPnL=${upnl}")
    else:
        print(f"  {coin} position:    (none)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Open a tiny market position and close it — Phase 2.4 + 2.5 roundtrip",
    )
    parser.add_argument("--coin", default=DEFAULT_COIN, help=f"Perp coin (default: {DEFAULT_COIN})")
    parser.add_argument("--notional", type=float, default=DEFAULT_NOTIONAL_USD,
                        help=f"Target notional in USD (default: {DEFAULT_NOTIONAL_USD})")
    parser.add_argument("--short", action="store_true", help="Open SHORT instead of LONG")
    parser.add_argument("--slippage", type=float, default=0.05,
                        help="Max slippage tolerance, 0..1 (default: 0.05 = five percent)")
    args = parser.parse_args()

    if args.notional < MIN_NOTIONAL_USD:
        sys.exit(f"ERROR: --notional must be >= {MIN_NOTIONAL_USD} (Hyperliquid minimum)")

    load_dotenv()
    main_address = _require_env("HL_MAIN_ADDRESS")
    agent_key = _get_agent_key()
    network = os.environ.get("HL_NETWORK", "mainnet").lower()
    api_url = constants.MAINNET_API_URL if network == "mainnet" else constants.TESTNET_API_URL

    info = Info(api_url, skip_ws=True)
    agent = Account.from_key(agent_key)
    exchange = Exchange(wallet=agent, base_url=api_url, account_address=main_address)

    mids = info.all_mids()
    if args.coin not in mids:
        sys.exit(f"ERROR: {args.coin} not found in live mids")
    mid = float(mids[args.coin])

    sz_decimals = _get_sz_decimals(info, args.coin)
    # Round UP so notional stays above target
    size = _ceil_to_decimals(args.notional / mid, sz_decimals)
    expected_notional = size * mid

    side = "SHORT" if args.short else "LONG"
    is_buy = not args.short

    snap_before = _fetch_account_snapshot(info, main_address)

    print("=" * 66)
    print(f"Network:          {network}")
    print(f"Main wallet:      {main_address}")
    print(f"Agent wallet:     {agent.address}")
    print("-" * 66)
    print(f"Coin:             {args.coin}")
    print(f"Current mid:      ${mid:,.2f}")
    print(f"Proposed:         MARKET {side}  ({args.notional:.2f} USDC target)")
    print(f"Size:             {size} {args.coin}  ({sz_decimals} decimals)")
    print(f"Expected notional: ${expected_notional:,.4f}")
    print(f"Max slippage:     {args.slippage*100:.1f}%")
    print("=" * 66)
    _print_snapshot("Before", snap_before, args.coin)
    print("=" * 66)
    print()
    print(f"⚠️  This will IMMEDIATELY fill at market. You will have a real")
    print(f"    open {side} position until the close step. Expected total")
    print(f"    cost of the roundtrip: $0.01-$0.10.")
    print()

    if input("Type 'yes' to OPEN the position: ").strip().lower() != "yes":
        print("Aborted. No position opened.")
        return

    print("\nOpening position via market_open...")
    open_result = exchange.market_open(
        name=args.coin,
        is_buy=is_buy,
        sz=size,
        px=None,          # None → use current market
        slippage=args.slippage,
    )
    print("Raw open response:")
    print(json.dumps(open_result, indent=2, default=str))

    if open_result.get("status") != "ok":
        sys.exit("\n❌ Open FAILED. See response above. No close needed.")

    # Extract fill details
    try:
        statuses = open_result["response"]["data"]["statuses"]
        filled = statuses[0].get("filled", {})
        if filled:
            fill_px = float(filled.get("avgPx", 0))
            fill_sz = float(filled.get("totalSz", 0))
            print(f"\n✅ Filled: {fill_sz} {args.coin} @ avg ${fill_px:,.2f}")
            print(f"   Actual notional: ${fill_sz * fill_px:,.4f}")
    except (KeyError, IndexError, TypeError):
        print("(could not parse fill details — check raw response)")

    # Give the API a moment to reflect the position
    time.sleep(1.0)
    snap_open = _fetch_account_snapshot(info, main_address)
    _print_snapshot("Position now open", snap_open, args.coin)

    print()
    print("=" * 66)
    print("NOW: open Hyperliquid in Brave Beta and verify the position.")
    print(f"     https://app.hyperliquid.xyz/trade/{args.coin}")
    print("     Bottom of the page → 'Positions' tab → you should see it.")
    print("=" * 66)
    print()

    if input("Type 'close' to close the position NOW: ").strip().lower() != "close":
        print()
        print("⚠️  Position is STILL OPEN. You can close it via the UI or by running:")
        print(f"   python close_position.py {args.coin}")
        return

    print("\nClosing position via market_close...")
    close_result = exchange.market_close(
        coin=args.coin,
        sz=None,          # None → close entire position
        px=None,
        slippage=args.slippage,
    )
    print("Raw close response:")
    print(json.dumps(close_result, indent=2, default=str))

    if close_result.get("status") != "ok":
        print("\n⚠️  Close response suggests an issue. Verify in UI. If position")
        print("   is still open, close it manually via Hyperliquid's UI.")
        return

    try:
        statuses = close_result["response"]["data"]["statuses"]
        filled = statuses[0].get("filled", {})
        if filled:
            close_px = float(filled.get("avgPx", 0))
            close_sz = float(filled.get("totalSz", 0))
            print(f"\n✅ Closed: {close_sz} {args.coin} @ avg ${close_px:,.2f}")
    except (KeyError, IndexError, TypeError):
        pass

    # Final snapshot + PnL summary
    time.sleep(1.0)
    snap_after = _fetch_account_snapshot(info, main_address)
    _print_snapshot("Position closed", snap_after, args.coin)

    delta = snap_after["account_value"] + snap_after["spot_usdc"] \
            - (snap_before["account_value"] + snap_before["spot_usdc"])
    print()
    print("=" * 66)
    print(f"Net change across roundtrip: {delta:+,.4f} USDC")
    print(f"  (includes fees + slippage + any intra-second price move)")
    print("=" * 66)
    print()
    print("✅ Phase 2.4 + 2.5 complete. The bot pipeline can now open AND")
    print("   close positions via the agent wallet.")


if __name__ == "__main__":
    main()
