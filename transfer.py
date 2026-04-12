"""
Phase 2.1 — Transfer USDC between spot and perps sub-accounts.

Hyperliquid deposits from Arbitrum land in your SPOT sub-account. To trade
perpetuals you need USDC in your PERPS sub-account as collateral. This
script moves USDC between the two.

Transfers happen on Hyperliquid's own L1 — no Arbitrum gas, no 15-second
waits. It's instant and free, the only cost is the one-time signature.

Run with:
    source venv/bin/activate
    python transfer.py                  # default: 20 USDC spot -> perps
    python transfer.py 15                # 15 USDC spot -> perps
    python transfer.py 5 --to-spot       # 5 USDC perps -> spot (reverse)
    python transfer.py 20 --yes          # skip the "type yes" confirm

Safety:
    - Reads the agent private key from macOS Keychain (hl-bot / agent-private-key)
    - Shows your balances BEFORE and AFTER so you can visually verify
    - Requires typing 'yes' unless --yes is passed
    - Only this script's output lands in the terminal; nothing hits .env
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
DEFAULT_AMOUNT_USDC = 20.0


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
            f"Store it with: keyring set {KEYRING_SERVICE} {KEYRING_ACCOUNT}"
        )
    return key.strip()


def _fetch_balances(info: Info, main_address: str) -> tuple[float, float]:
    """Returns (spot_usdc, perps_account_value)."""
    spot = info.spot_user_state(main_address)
    spot_usdc = next(
        (float(b["total"]) for b in spot.get("balances", []) if b["coin"] == "USDC"),
        0.0,
    )
    perp = info.user_state(main_address)
    perp_value = float(perp["marginSummary"]["accountValue"])
    return spot_usdc, perp_value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transfer USDC between spot and perps sub-accounts on Hyperliquid",
    )
    parser.add_argument(
        "amount",
        nargs="?",
        type=float,
        default=DEFAULT_AMOUNT_USDC,
        help=f"Amount in USDC (default: {DEFAULT_AMOUNT_USDC})",
    )
    parser.add_argument(
        "--to-spot",
        action="store_true",
        help="Reverse direction: perps -> spot (default is spot -> perps)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the interactive confirmation prompt",
    )
    args = parser.parse_args()

    if args.amount <= 0:
        sys.exit("ERROR: amount must be > 0")

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

    info = Info(api_url, skip_ws=True)
    agent = Account.from_key(agent_key)
    exchange = Exchange(wallet=agent, base_url=api_url, account_address=main_address)

    spot_before, perp_before = _fetch_balances(info, main_address)
    direction = "perps -> spot" if args.to_spot else "spot -> perps"
    source_before = perp_before if args.to_spot else spot_before

    print("=" * 60)
    print(f"Network:        {network}")
    print(f"Main wallet:    {main_address}")
    print(f"Agent wallet:   {agent.address}")
    print("-" * 60)
    print("Current balances")
    print(f"  Spot USDC:    ${spot_before:,.4f}")
    print(f"  Perps value:  ${perp_before:,.4f}")
    print("-" * 60)
    print(f"Proposed:       transfer ${args.amount:,.4f} USDC  ({direction})")
    print("=" * 60)

    if args.amount > source_before:
        sys.exit(
            f"\nERROR: insufficient balance. Requested ${args.amount:,.2f} "
            f"but source sub-account has only ${source_before:,.4f}."
        )

    if not args.yes:
        confirm = input("\nType 'yes' to confirm and execute: ").strip().lower()
        if confirm != "yes":
            print("Aborted. No transfer executed.")
            return

    print("\nExecuting...")
    to_perp = not args.to_spot
    result = exchange.usd_class_transfer(args.amount, to_perp=to_perp)
    print(f"Raw API response: {result}")

    spot_after, perp_after = _fetch_balances(info, main_address)
    spot_delta = spot_after - spot_before
    perp_delta = perp_after - perp_before

    print()
    print("New balances")
    print(f"  Spot USDC:    ${spot_after:,.4f}    (delta {spot_delta:+,.4f})")
    print(f"  Perps value:  ${perp_after:,.4f}    (delta {perp_delta:+,.4f})")
    print()

    expected_sign = -1 if not args.to_spot else 1
    if (spot_delta * expected_sign) > 0 and abs(abs(spot_delta) - args.amount) < 0.01:
        print("✅ Transfer succeeded — balances moved as expected.")
    else:
        print("⚠️  Balances did not move as expected — check the raw API response above.")


if __name__ == "__main__":
    main()
