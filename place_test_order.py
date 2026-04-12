"""
Phase 2.2 / 2.3 — Place a limit order far from market, then cancel it.

This is a roundtrip test of the write API: place → verify resting → cancel → verify gone.
The order is placed ~50% below the current mid price so it CANNOT fill even during
a flash crash. You cannot lose money on this test; the only thing that can go wrong
is a bad API call, which we'll see immediately.

Flow:
    1. Fetch current BTC mid price
    2. Compute a limit price at 50% of market (deliberately unfillable)
    3. Compute a size so notional is just above Hyperliquid's $10 minimum
    4. Place the limit buy order via the agent wallet
    5. Print the returned order ID
    6. Fetch open orders via the API and confirm our order appears
    7. Pause — you open Hyperliquid in Brave Beta and visually verify
    8. Type 'cancel' to cancel the order via the API
    9. Fetch open orders again and confirm our order is gone

Run with:
    source venv/bin/activate
    python place_test_order.py
    python place_test_order.py --coin ETH          # use ETH instead of BTC
    python place_test_order.py --notional 15       # larger notional
    python place_test_order.py --discount 0.3      # limit at 70% of market
"""

from __future__ import annotations

import argparse
import os
import sys

import keyring
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

KEYRING_SERVICE = "hl-bot"
KEYRING_ACCOUNT = "agent-private-key"

DEFAULT_COIN = "BTC"
DEFAULT_NOTIONAL_USD = 12.0   # just above $10 min, low enough to be harmless
DEFAULT_DISCOUNT = 0.5        # limit price = market * (1 - discount) for buys


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
    """Look up the szDecimals for a given perp coin from the meta."""
    meta = info.meta()
    for asset in meta.get("universe", []):
        if asset.get("name") == coin:
            return int(asset.get("szDecimals", 5))
    sys.exit(f"ERROR: could not find {coin} in perps meta")


def _round_to_decimals(value: float, decimals: int) -> float:
    """Round a size to the asset's szDecimals, as required by Hyperliquid."""
    factor = 10 ** decimals
    return round(value * factor) / factor


def _round_price(price: float, coin: str) -> float:
    """Round a limit price. Hyperliquid accepts integer-dollar prices for BTC/ETH/etc
    so using round(price) is safe for our 'far from market' limits. For more precise
    prices, Hyperliquid has tick size rules we'd need to query."""
    return float(round(price))


def _find_open_orders_for_agent(info: Info, address: str, oid: int) -> dict | None:
    """Return the open order with matching oid, or None."""
    orders = info.open_orders(address)
    for o in orders:
        if int(o.get("oid", -1)) == oid:
            return o
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Place a limit buy order far from market, then cancel it (roundtrip test)",
    )
    parser.add_argument("--coin", default=DEFAULT_COIN, help=f"Perp coin (default: {DEFAULT_COIN})")
    parser.add_argument("--notional", type=float, default=DEFAULT_NOTIONAL_USD,
                        help=f"Order notional in USD (default: {DEFAULT_NOTIONAL_USD})")
    parser.add_argument("--discount", type=float, default=DEFAULT_DISCOUNT,
                        help="Limit price discount from market, 0..1 (default: 0.5 = half of market)")
    args = parser.parse_args()

    if not (0 < args.discount < 1):
        sys.exit("ERROR: --discount must be between 0 and 1")
    if args.notional < 10:
        sys.exit(f"ERROR: Hyperliquid minimum notional is $10, got {args.notional}")

    load_dotenv()
    main_address = _require_env("HL_MAIN_ADDRESS")
    agent_key = _get_agent_key()
    network = os.environ.get("HL_NETWORK", "mainnet").lower()

    api_url = constants.MAINNET_API_URL if network == "mainnet" else constants.TESTNET_API_URL

    info = Info(api_url, skip_ws=True)
    agent = Account.from_key(agent_key)
    exchange = Exchange(wallet=agent, base_url=api_url, account_address=main_address)

    # Get current market state
    mids = info.all_mids()
    if args.coin not in mids:
        sys.exit(f"ERROR: {args.coin} not found in live mids. Check spelling.")
    mid = float(mids[args.coin])

    sz_decimals = _get_sz_decimals(info, args.coin)
    limit_px = _round_price(mid * (1 - args.discount), args.coin)
    raw_size = args.notional / limit_px
    size = _round_to_decimals(raw_size, sz_decimals)

    if size <= 0:
        sys.exit(f"ERROR: computed size rounded to 0. Try a larger --notional.")

    notional_at_limit = size * limit_px
    notional_at_mid = size * mid

    print("=" * 66)
    print(f"Network:          {network}")
    print(f"Main wallet:      {main_address}")
    print(f"Agent wallet:     {agent.address}")
    print("-" * 66)
    print(f"Coin:             {args.coin}")
    print(f"Current mid:      ${mid:,.2f}")
    print(f"Proposed side:    BUY (limit, GTC)")
    print(f"Limit price:      ${limit_px:,.2f}  ({args.discount*100:.0f}% below market)")
    print(f"Size:             {size} {args.coin}  ({sz_decimals} decimals)")
    print(f"Notional at limit: ${notional_at_limit:,.4f}")
    print(f"Notional at mid:   ${notional_at_mid:,.4f}  (for margin check)")
    print("=" * 66)
    print()
    print("This order is ~50% below market. It will NOT fill. If it does, BTC")
    print("has crashed and you have bigger things to worry about.")
    print()

    confirm = input("Type 'yes' to place this order: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    print("\nPlacing order...")
    order_result = exchange.order(
        name=args.coin,
        is_buy=True,
        sz=size,
        limit_px=limit_px,
        order_type={"limit": {"tif": "Gtc"}},
        reduce_only=False,
    )
    print(f"Raw response: {order_result}")

    if order_result.get("status") != "ok":
        sys.exit(f"\nERROR: order placement failed. See response above.")

    # Extract oid
    try:
        statuses = order_result["response"]["data"]["statuses"]
        first = statuses[0]
        if "resting" in first:
            oid = int(first["resting"]["oid"])
        elif "filled" in first:
            oid = int(first["filled"]["oid"])
            print("⚠️  UNEXPECTED: order filled immediately. Something is very wrong.")
        else:
            print(f"Unknown status shape: {first}")
            return
    except (KeyError, IndexError, TypeError) as e:
        print(f"Could not parse order ID from response: {e}")
        return

    print(f"\n✅ Order placed — oid = {oid}")
    print()

    # Confirm via API that it's resting in the open orders list
    found = _find_open_orders_for_agent(info, main_address, oid)
    if found:
        print("Confirmed in open orders:")
        print(f"  coin:       {found.get('coin')}")
        print(f"  side:       {found.get('side')}  (B=buy, A=ask/sell)")
        print(f"  limit_px:   {found.get('limitPx')}")
        print(f"  size:       {found.get('sz')}")
        print(f"  timestamp:  {found.get('timestamp')}")
    else:
        print("⚠️  Order placed but NOT found in open orders query — weird, investigate.")

    print()
    print("=" * 66)
    print("NOW: open Hyperliquid in Brave Beta and visually verify the order.")
    print("     https://app.hyperliquid.xyz/trade/BTC")
    print("     Bottom of the page → 'Open Orders' tab → you should see this one.")
    print("=" * 66)
    print()

    choice = input("Type 'cancel' to cancel it now, or 'leave' to leave it resting: ").strip().lower()
    if choice == "cancel":
        print("\nCancelling order...")
        cancel_result = exchange.cancel(args.coin, oid)
        print(f"Raw response: {cancel_result}")

        still_there = _find_open_orders_for_agent(info, main_address, oid)
        if still_there is None:
            print("\n✅ Cancelled — order no longer in open orders list.")
        else:
            print("\n⚠️  Cancel may have failed — order still appears in open orders.")
    else:
        print("\nLeaving order resting. You can cancel it later via the UI or")
        print(f"by running: python cancel_order.py {args.coin} {oid}")


if __name__ == "__main__":
    main()
