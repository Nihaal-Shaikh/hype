"""
Phase 3 — B: First real oil trade.

Market buy ~$12 of WTI crude (xyz:CL), hold for a few seconds, close.
Same pattern as Phase 2's BTC roundtrip, but on oil. Uses hype_bot for
multi-dex support.

Expected cost: ~$0.01-0.05 (taker fees + spread + slippage).

Run with:
    source venv/bin/activate
    PYTHONPATH=. python oil_roundtrip.py
"""

from __future__ import annotations

import json
import math
import sys
import time

from hype_bot import (
    ACTIVE_DEXES,
    get_tradable_market,
    load_main_address,
    make_exchange,
    make_info,
)

COIN = "xyz:CL"
DEX = "xyz"
NOTIONAL_USD = 12.0


def main() -> None:
    main_addr = load_main_address()
    info = make_info(ACTIVE_DEXES)
    exchange = make_exchange(ACTIVE_DEXES)

    market = get_tradable_market(info, DEX, COIN)
    if market.current_mid is None:
        sys.exit("ERROR: no mid price for xyz:CL")

    mid = market.current_mid
    size = math.ceil(NOTIONAL_USD / mid * 10**market.size_decimals) / 10**market.size_decimals
    notional = size * mid

    # Pre-state
    spot_before = info.spot_user_state(main_addr)
    usdc_before = next(
        (float(b["total"]) for b in spot_before.get("balances", []) if b["coin"] == "USDC"),
        0.0,
    )

    print("=" * 66)
    print(f"FIRST OIL TRADE — WTI Crude (xyz:CL)")
    print("=" * 66)
    print(f"Main wallet:   {main_addr}")
    print(f"Agent wallet:  {exchange.wallet.address}")
    print(f"Current mid:   ${mid:,.4f} / barrel")
    print(f"Size:          {size} CL ({market.size_decimals} decimals)")
    print(f"Notional:      ${notional:,.4f}")
    print(f"Max leverage:  {market.max_leverage}x")
    print(f"Market open:   {market.open_now}  ({market.asset_class.value})")
    print(f"Spot USDC:     ${usdc_before:,.4f}")
    print("=" * 66)
    print()
    print("This will IMMEDIATELY open a LONG position on WTI crude oil.")
    print(f"Expected cost of the full roundtrip: $0.01-$0.05.")
    print()

    if input("Type 'yes' to BUY oil: ").strip().lower() != "yes":
        print("Aborted.")
        return

    # Open
    print("\nOpening position via market_open...")
    open_result = exchange.market_open(COIN, is_buy=True, sz=size, slippage=0.05)
    print("Raw response:")
    print(json.dumps(open_result, indent=2, default=str))

    if open_result.get("status") != "ok":
        sys.exit("\nOpen FAILED. See response above.")

    try:
        filled = open_result["response"]["data"]["statuses"][0].get("filled", {})
        if filled:
            fill_px = float(filled.get("avgPx", 0))
            fill_sz = float(filled.get("totalSz", 0))
            print(f"\n✅ Filled: {fill_sz} CL @ ${fill_px:,.4f}")
            print(f"   Actual notional: ${fill_sz * fill_px:,.4f}")
    except (KeyError, IndexError, TypeError):
        pass

    # Check position
    time.sleep(1.0)
    state = info.user_state(main_addr, dex=DEX)
    for p in state.get("assetPositions", []):
        pos = p["position"]
        if pos["coin"] == COIN:
            print(f"\nPosition open:")
            print(f"  Size:     {pos['szi']} CL")
            print(f"  Entry:    ${pos.get('entryPx', '?')}")
            print(f"  uPnL:    ${pos.get('unrealizedPnl', '?')}")
            print(f"  Leverage: {pos.get('leverage', {}).get('value', '?')}x")

    print()
    print("=" * 66)
    print("You are now LONG crude oil. Verify in Brave Beta if you want.")
    print("=" * 66)
    print()

    if input("Type 'close' to close the position: ").strip().lower() != "close":
        print("\nPosition is STILL OPEN. Close it via the Hyperliquid UI or rerun.")
        return

    # Close
    print("\nClosing position via market_close...")
    close_result = exchange.market_close(coin=COIN, slippage=0.05)
    print("Raw response:")
    print(json.dumps(close_result, indent=2, default=str))

    try:
        filled = close_result["response"]["data"]["statuses"][0].get("filled", {})
        if filled:
            close_px = float(filled.get("avgPx", 0))
            close_sz = float(filled.get("totalSz", 0))
            print(f"\n✅ Closed: {close_sz} CL @ ${close_px:,.4f}")
    except (KeyError, IndexError, TypeError):
        pass

    # Final state
    time.sleep(1.0)
    spot_after = info.spot_user_state(main_addr)
    usdc_after = next(
        (float(b["total"]) for b in spot_after.get("balances", []) if b["coin"] == "USDC"),
        0.0,
    )
    delta = usdc_after - usdc_before

    print()
    print("=" * 66)
    print(f"Spot USDC before: ${usdc_before:,.4f}")
    print(f"Spot USDC after:  ${usdc_after:,.4f}")
    print(f"Net change:       {delta:+,.4f} USDC")
    print(f"  (includes fees + spread + any price movement)")
    print("=" * 66)
    print()
    print("🛢️  Your first oil trade is complete.")


if __name__ == "__main__":
    main()
