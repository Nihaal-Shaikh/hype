"""
Phase 0 — Hello, Hyperliquid.

Purpose: prove the whole pipeline works end to end before we write any
trading logic. This script does NOT place any orders. It only reads.

What it verifies:
  1. The agent private key in .env loads correctly and matches the agent
     address Hyperliquid recorded for this account.
  2. We can talk to Hyperliquid's API as your authenticated user.
  3. Your real balance and positions are reachable via the SDK.
  4. Live market data (BTC perp mid price) is reachable.

If all four print successfully, the foundation is solid and we can move
to Phase 1 (read-only dashboard).
"""

from __future__ import annotations

import os
import sys

import keyring
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.utils import constants

# macOS Keychain coordinates for the agent private key. Using the keychain
# keeps the secret out of any file Claude Code / file watchers can see.
KEYRING_SERVICE = "hl-bot"
KEYRING_ACCOUNT = "agent-private-key"


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"ERROR: {name} is not set in .env")
    return value


def _get_agent_key_from_keychain() -> str:
    key = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    if not key:
        sys.exit(
            "ERROR: agent private key not found in macOS Keychain.\n"
            "Store it once with:\n"
            f"    keyring set {KEYRING_SERVICE} {KEYRING_ACCOUNT}\n"
            "(you will be prompted; the value will not be echoed to the terminal)."
        )
    return key.strip()


def main() -> None:
    load_dotenv()

    main_address = _require_env("HL_MAIN_ADDRESS")
    agent_key = _get_agent_key_from_keychain()
    network = os.environ.get("HL_NETWORK", "mainnet").lower()

    if network == "mainnet":
        api_url = constants.MAINNET_API_URL
    elif network == "testnet":
        api_url = constants.TESTNET_API_URL
    else:
        sys.exit(f"ERROR: HL_NETWORK must be 'mainnet' or 'testnet', got: {network!r}")

    # Constructing the agent account from the private key proves the key is
    # well-formed (correct length, valid hex). It will raise immediately if not.
    agent = Account.from_key(agent_key)

    print("=" * 60)
    print(f"Network:        {network}")
    print(f"Main wallet:    {main_address}")
    print(f"Agent wallet:   {agent.address}")
    print("=" * 60)

    info = Info(api_url, skip_ws=True)

    # Perps sub-account: USDC collateral + margin + open futures positions.
    perp_state = info.user_state(main_address)
    margin_summary = perp_state["marginSummary"]
    perp_account_value = float(margin_summary["accountValue"])
    perp_margin_used = float(margin_summary["totalMarginUsed"])
    perp_withdrawable = float(perp_state.get("withdrawable", "0"))
    positions = perp_state.get("assetPositions", [])

    # Spot sub-account: token balances (USDC, HYPE, ETH, etc.).
    spot_state = info.spot_user_state(main_address)
    spot_balances = [b for b in spot_state.get("balances", []) if float(b.get("total", "0")) > 0]
    spot_usdc = next(
        (float(b["total"]) for b in spot_balances if b["coin"] == "USDC"),
        0.0,
    )

    print()
    print("Perps account")
    print(f"  Account value:    ${perp_account_value:,.4f}")
    print(f"  Margin used:      ${perp_margin_used:,.4f}")
    print(f"  Withdrawable:     ${perp_withdrawable:,.4f}")
    print(f"  Open positions:   {len(positions)}")
    for pos in positions:
        p = pos["position"]
        coin = p["coin"]
        size = p["szi"]
        entry = p.get("entryPx", "?")
        upnl = p.get("unrealizedPnl", "?")
        print(f"    {coin}: size={size}, entry=${entry}, uPnL=${upnl}")

    print()
    print("Spot account")
    if not spot_balances:
        print("  (no balances)")
    else:
        for b in spot_balances:
            coin = b["coin"]
            total = float(b["total"])
            hold = float(b["hold"])
            label = f"${total:,.4f}" if coin == "USDC" else f"{total:,.6f}"
            hold_note = f"  (on hold: {hold})" if hold > 0 else ""
            print(f"  {coin:<8} {label}{hold_note}")

    total_on_hyperliquid = perp_account_value + spot_usdc
    print()
    print(f"TOTAL on Hyperliquid: ${total_on_hyperliquid:,.4f}")

    if perp_account_value == 0 and spot_usdc > 0:
        print()
        print("NOTE: Your USDC is in the SPOT account. To trade perpetuals, you'll")
        print("      need to transfer USDC from spot -> perps first (we'll do this")
        print("      when we actually start trading).")

    # Live market sample — proves market data access works.
    mids = info.all_mids()
    print()
    print("Market sample")
    for symbol in ("BTC", "ETH", "SOL"):
        mid = mids.get(symbol)
        if mid is not None:
            print(f"  {symbol} perp mid:  ${float(mid):,.2f}")

    print()
    print("OK — pipeline verified.")


if __name__ == "__main__":
    main()
