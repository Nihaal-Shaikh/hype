"""
Phase 3A Step 2 — Confirmation probe.

Read-only. Zero money, zero signatures, zero writes.

Confirms the source-survey hypothesis from research/phase3_sdk_survey.md by
making live calls against Hyperliquid mainnet and inspecting the results.

Questions the probe answers:
  1. How is xyz:CL keyed in Info.name_to_coin after perp_dexs=["", "xyz"]?
  2. Does info.all_mids() (no dex) return only core, only xyz, or flat?
  3. Does info.all_mids(dex="xyz") return xyz markets only?
  4. Does l2_snapshot("xyz:CL") work now with perp_dexs=["", "xyz"]?
  5. What does query_user_dex_abstraction_state return for our main wallet?
  6. What does user_state(main, dex="xyz") look like for our empty xyz account?
  7. Does info.open_orders(main) return cross-dex orders in one call?

Usage:
    source venv/bin/activate
    python research/phase3_probe.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import os

from dotenv import load_dotenv
from hyperliquid.info import Info
from hyperliquid.utils import constants

load_dotenv()
MAIN = os.environ.get("HL_MAIN_ADDRESS", "").strip()


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def main() -> None:
    print(f"Phase 3A Step 2 — confirmation probe")
    print(f"Started: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"Main wallet: {MAIN}")
    print(f"Network: mainnet")

    # --- 1. Construct Info with perp_dexs=["", "xyz"]
    section("1. Construct Info(perp_dexs=['', 'xyz']) and inspect name_to_coin")
    info = Info(constants.MAINNET_API_URL, skip_ws=True, perp_dexs=["", "xyz"])
    total_names = len(info.name_to_coin)
    xyz_names = sorted([k for k in info.name_to_coin if k.startswith("xyz:")])
    core_sample = sorted([k for k in info.name_to_coin if k in ("BTC", "ETH", "SOL", "HYPE")])
    print(f"  Total keys in name_to_coin: {total_names}")
    print(f"  Sample core keys found: {core_sample}")
    print(f"  xyz:* keys found: {len(xyz_names)}")
    print(f"  First 10 xyz keys: {xyz_names[:10]}")
    if "xyz:CL" in info.name_to_coin:
        print(f"  ✅ 'xyz:CL' IS in name_to_coin")
        print(f"     value: {info.name_to_coin['xyz:CL']!r}")
        print(f"     coin_to_asset: {info.coin_to_asset.get('xyz:CL')}")
    else:
        print(f"  ❌ 'xyz:CL' NOT in name_to_coin — hypothesis failed")
        sys.exit(1)

    # --- 2. all_mids() with no dex arg
    section("2. info.all_mids() with NO dex arg")
    mids_default = info.all_mids()
    print(f"  Total keys: {len(mids_default)}")
    xyz_in_default = [k for k in mids_default if k.startswith("xyz:")]
    print(f"  xyz:* keys in default all_mids: {len(xyz_in_default)}")
    print(f"  'BTC' in default all_mids: {'BTC' in mids_default}")
    print(f"  'xyz:CL' in default all_mids: {'xyz:CL' in mids_default}")
    print(f"  → Conclusion: {'flat across all loaded dexes' if xyz_in_default else 'core only'}")

    # --- 3. all_mids(dex="xyz")
    section("3. info.all_mids(dex='xyz')")
    mids_xyz = info.all_mids(dex="xyz")
    print(f"  Total keys: {len(mids_xyz)}")
    print(f"  Sample xyz keys: {sorted(list(mids_xyz.keys()))[:10]}")
    print(f"  'xyz:CL' in all_mids(dex='xyz'): {'xyz:CL' in mids_xyz}")
    if "xyz:CL" in mids_xyz:
        print(f"  xyz:CL price: ${float(mids_xyz['xyz:CL']):,.4f}")
    if "xyz:GOLD" in mids_xyz:
        print(f"  xyz:GOLD price: ${float(mids_xyz['xyz:GOLD']):,.4f}")
    if "xyz:TSLA" in mids_xyz:
        print(f"  xyz:TSLA price: ${float(mids_xyz['xyz:TSLA']):,.4f}")

    # --- 4. l2_snapshot("xyz:CL")
    section("4. info.l2_snapshot('xyz:CL')")
    try:
        book = info.l2_snapshot("xyz:CL")
        levels = book.get("levels", [])
        print(f"  ✅ Call succeeded")
        print(f"  coin: {book.get('coin')}")
        print(f"  time: {book.get('time')}")
        print(f"  levels shape: {len(levels)} sides")
        if len(levels) == 2:
            bids, asks = levels
            print(f"  Top 3 bids: {[(b.get('px'), b.get('sz')) for b in bids[:3]]}")
            print(f"  Top 3 asks: {[(a.get('px'), a.get('sz')) for a in asks[:3]]}")
            if bids and asks:
                best_bid = float(bids[0]["px"])
                best_ask = float(asks[0]["px"])
                mid = (best_bid + best_ask) / 2
                spread = best_ask - best_bid
                spread_bps = (spread / mid) * 10000
                print(f"  best_bid=${best_bid:,.4f}  best_ask=${best_ask:,.4f}  spread=${spread:.4f} ({spread_bps:.2f} bps)")
            else:
                print(f"  Book is empty (market likely closed)")
    except Exception as e:
        print(f"  ❌ Call FAILED: {type(e).__name__}: {e}")

    # Also try a known-24h non-CL symbol to rule out session-closed confusion:
    section("4b. info.l2_snapshot('xyz:GOLD')  (fallback — gold trades nearly 24h)")
    try:
        book = info.l2_snapshot("xyz:GOLD")
        levels = book.get("levels", [])
        if len(levels) == 2:
            bids, asks = levels
            if bids and asks:
                best_bid = float(bids[0]["px"])
                best_ask = float(asks[0]["px"])
                print(f"  ✅ xyz:GOLD book: bid=${best_bid:,.4f}  ask=${best_ask:,.4f}")
            else:
                print(f"  Book is empty")
    except Exception as e:
        print(f"  ❌ Call FAILED: {type(e).__name__}: {e}")

    # --- 5. query_user_dex_abstraction_state
    section("5. info.query_user_dex_abstraction_state(main)")
    state = info.query_user_dex_abstraction_state(MAIN)
    print(f"  Result: {state!r}")
    print(f"  Type: {type(state).__name__}")
    print(f"  → Dex abstraction is currently: {'ENABLED' if state is True else 'DISABLED'}")

    # --- 6. user_state for xyz dex
    section("6. info.user_state(main, dex='xyz')")
    xyz_state = info.user_state(MAIN, dex="xyz")
    ms = xyz_state.get("marginSummary", {})
    print(f"  accountValue: ${float(ms.get('accountValue', '0')):,.4f}")
    print(f"  withdrawable: ${float(xyz_state.get('withdrawable', '0')):,.4f}")
    print(f"  positions: {len(xyz_state.get('assetPositions', []))}")

    # --- 7. open_orders — flat or per-dex?
    section("7. info.open_orders(main) — shape and cross-dex behavior")
    orders = info.open_orders(MAIN)
    print(f"  Total orders returned: {len(orders)}")
    if orders:
        coins = sorted({o.get("coin", "?") for o in orders})
        print(f"  Distinct coin field values: {coins}")
        print(f"  First order (truncated): {json.dumps(orders[0], indent=2)[:400]}")
    else:
        print(f"  (no open orders — cannot verify cross-dex behavior yet; will verify in Phase 3D)")

    # --- 8. perp_dexs() list for the record
    section("8. info.perp_dexs()  (full list of deployed dexes)")
    dexes = info.perp_dexs()
    if dexes:
        for i, d in enumerate(dexes):
            if d is None:
                print(f"  [{i}] None  (core perps dex)")
            elif isinstance(d, dict):
                name = d.get("name", "?")
                full = d.get("fullName", "?")
                print(f"  [{i}] {name:10s} = {full}")
            else:
                print(f"  [{i}] {d!r}")

    section("Probe complete")
    print(f"Finished: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
