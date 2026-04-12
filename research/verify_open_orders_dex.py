"""
Verify that info.open_orders(address, dex="xyz") returns xyz dex orders.

Phase 3 leftover: the probe script called open_orders(main) without dex="xyz",
which is why it returned []. This script confirms the fix:

1. Place a safe limit BUY on xyz:CL at 50% below market
2. Check open_orders with dex="" (should NOT contain it)
3. Check open_orders with dex="xyz" (SHOULD contain it)
4. Check get_open_orders_all_dexes() (SHOULD contain it)
5. Cancel the order
6. Verify it's gone

Run with:
    source venv/bin/activate
    python research/verify_open_orders_dex.py
"""

from __future__ import annotations

import json
import math
import sys
import time

from hype_bot import (
    ACTIVE_DEXES,
    get_open_orders_all_dexes,
    get_tradable_market,
    load_main_address,
    make_exchange,
    make_info,
)

COIN = "xyz:CL"
NOTIONAL_USD = 12.0
DISCOUNT = 0.5


def _hl_round_px(px: float, sz_decimals: int) -> float:
    five_sig = float(f"{px:.5g}")
    max_decimals = 6 - sz_decimals
    return round(five_sig, max_decimals)


def _ceil_to_decimals(value: float, decimals: int) -> float:
    factor = 10**decimals
    return math.ceil(value * factor) / factor


def main() -> None:
    main_address = load_main_address()
    info = make_info(ACTIVE_DEXES)
    exchange = make_exchange(ACTIVE_DEXES)

    print("=" * 70)
    print("VERIFY: open_orders dex= parameter fix")
    print("=" * 70)

    # Build market + order params
    market = get_tradable_market(info, "xyz", COIN)
    if market.current_mid is None:
        print(f"No mid price for {COIN} — market may be closed")
        sys.exit(1)

    limit_px = _hl_round_px(market.current_mid * (1 - DISCOUNT), market.size_decimals)
    size = _ceil_to_decimals(NOTIONAL_USD / limit_px, market.size_decimals)

    print(f"Placing limit BUY {size} {COIN} @ ${limit_px} (50% below ${market.current_mid:.2f})")

    result = exchange.order(
        name=COIN,
        is_buy=True,
        sz=size,
        limit_px=limit_px,
        order_type={"limit": {"tif": "Gtc"}},
        reduce_only=False,
    )

    # Extract oid
    try:
        status = result["response"]["data"]["statuses"][0]
        oid = status["resting"]["oid"]
    except (KeyError, IndexError):
        print(f"Order did not rest as expected: {json.dumps(result, default=str)}")
        sys.exit(1)

    print(f"Order resting, oid={oid}")
    time.sleep(1.5)

    # Test 1: open_orders with default dex="" (core) — should NOT find it
    core_orders = info.open_orders(main_address)
    core_oids = [o.get("oid") for o in core_orders]
    t1_pass = oid not in core_oids
    print(f"\nTest 1: open_orders(dex='')  -> {len(core_orders)} orders, oid {oid} present: {not t1_pass}")
    print(f"  {'PASS' if t1_pass else 'FAIL'} — xyz order should NOT appear in core query")

    # Test 2: open_orders with dex="xyz" — SHOULD find it
    xyz_orders = info.open_orders(main_address, dex="xyz")
    xyz_oids = [o.get("oid") for o in xyz_orders]
    t2_pass = oid in xyz_oids
    print(f"\nTest 2: open_orders(dex='xyz') -> {len(xyz_orders)} orders, oid {oid} present: {t2_pass}")
    print(f"  {'PASS' if t2_pass else 'FAIL'} — xyz order SHOULD appear in xyz query")

    # Test 3: get_open_orders_all_dexes helper — should find it in xyz
    all_orders = get_open_orders_all_dexes(info, main_address)
    xyz_from_helper = all_orders.get("xyz", [])
    helper_oids = [o.get("oid") for o in xyz_from_helper]
    t3_pass = oid in helper_oids
    print(f"\nTest 3: get_open_orders_all_dexes() -> xyz has {len(xyz_from_helper)} orders, oid {oid} present: {t3_pass}")
    print(f"  {'PASS' if t3_pass else 'FAIL'} — helper should aggregate across dexes")

    # Cleanup: cancel
    print(f"\nCancelling oid {oid}...")
    cancel_result = exchange.cancel(COIN, oid)
    time.sleep(1.0)
    after = info.open_orders(main_address, dex="xyz")
    still_there = any(o.get("oid") == oid for o in after)
    t4_pass = not still_there
    print(f"Test 4: Cancel verified: {'PASS' if t4_pass else 'FAIL'}")

    # Summary
    print("\n" + "=" * 70)
    all_pass = all([t1_pass, t2_pass, t3_pass, t4_pass])
    if all_pass:
        print("ALL TESTS PASSED — open_orders(dex='xyz') is the fix.")
    else:
        print("SOME TESTS FAILED — investigate output above.")
    print("=" * 70)


if __name__ == "__main__":
    main()
