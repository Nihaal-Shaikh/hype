"""
Phase 3 shortcut probe — attempt a xyz:CL limit order with NO prior 3C setup.

Goal: determine empirically whether the agent wallet can trade on a non-core
perp dex right now, under unified account mode, WITHOUT any additional
dex-abstraction opt-in or cross-dex transfer.

Three possible outcomes:

  RESTING    → Order placed and resting. Agent can trade xyz immediately.
               No 3C setup needed. Skip 3C entirely, Phase 3D trivial.
  ERR_AUTH   → Rejected with an abstraction/agent/permission error.
               We need 3C.1 (user opt-in) + 3C.3 (agent opt-in).
  ERR_MARGIN → Rejected with insufficient-margin/deposit error.
               Collateral IS isolated per-dex. Need 3C.1 + 3C.3 + transfer.
  ERR_OTHER  → Unexpected error. Investigate the raw response.

The order is placed ~50% below current market so it cannot fill even
during a flash crash. If it somehow fills, we immediately market-close.

Run with:
    source venv/bin/activate
    python research/phase3_probe_write.py
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
NOTIONAL_USD = 12.0
DISCOUNT = 0.5  # limit price = market * (1 - discount) for buys


def _ceil_to_decimals(value: float, decimals: int) -> float:
    factor = 10**decimals
    return math.ceil(value * factor) / factor


def _hl_round_px(px: float, sz_decimals: int) -> float:
    """Round a price per Hyperliquid's rules: 5 sig figs, then decimals."""
    five_sig = float(f"{px:.5g}")
    max_decimals = 6 - sz_decimals  # per exchange.py _slippage_price
    return round(five_sig, max_decimals)


def _classify(raw: dict) -> tuple[str, str]:
    """Return (category, detail)."""
    if raw.get("status") == "ok":
        try:
            first = raw["response"]["data"]["statuses"][0]
            if "resting" in first:
                return ("RESTING", f"oid={first['resting']['oid']}")
            if "filled" in first:
                return ("FILLED", json.dumps(first["filled"]))
            if "error" in first:
                return ("STATUS_ERROR", str(first["error"]))
            return ("OK_UNKNOWN", str(first))
        except Exception as e:
            return ("PARSE_ERROR", f"{e}; raw={raw}")
    return ("API_ERR", str(raw.get("response", raw)))


def _tag_err(err_text: str) -> str:
    t = err_text.lower()
    if "abstraction" in t or "not authorized" in t or "agent" in t or "permission" in t:
        return "ERR_AUTH"
    if "margin" in t or "deposit" in t or "balance" in t or "insufficient" in t:
        return "ERR_MARGIN"
    return "ERR_OTHER"


def main() -> None:
    main_address = load_main_address()
    info = make_info(ACTIVE_DEXES)
    exchange = make_exchange(ACTIVE_DEXES)

    print("=" * 70)
    print("Phase 3 SHORTCUT PROBE: attempt xyz:CL order with NO 3C setup")
    print("=" * 70)
    print(f"Main wallet:  {main_address}")
    print(f"Agent wallet: {exchange.wallet.address}")
    print(f"perp_dexs:    {ACTIVE_DEXES}")
    print()

    # Build market spec
    try:
        market = get_tradable_market(info, "xyz", COIN)
    except Exception as e:
        print(f"❌ Cannot build TradableMarket for {COIN}: {e}")
        sys.exit(1)

    if market.current_mid is None:
        print(f"❌ No mid price for {COIN}")
        sys.exit(1)

    limit_px = _hl_round_px(market.current_mid * (1 - DISCOUNT), market.size_decimals)
    raw_size = NOTIONAL_USD / limit_px
    size = _ceil_to_decimals(raw_size, market.size_decimals)
    notional_at_limit = size * limit_px

    print(f"Market:            {market.symbol}  ({market.asset_class.value})")
    print(f"Max leverage:      {market.max_leverage}x")
    print(f"Size decimals:     {market.size_decimals}")
    print(f"Current mid:       ${market.current_mid:,.4f}")
    print(f"Limit px:          ${limit_px:,.4f}  ({DISCOUNT*100:.0f}% below)")
    print(f"Size:              {size}")
    print(f"Notional at limit: ${notional_at_limit:,.4f}")
    print(f"Market open_now:   {market.open_now}")
    print()
    print("Placing order (this is a real write operation)...")
    print()

    try:
        result = exchange.order(
            name=COIN,
            is_buy=True,
            sz=size,
            limit_px=limit_px,
            order_type={"limit": {"tif": "Gtc"}},
            reduce_only=False,
        )
    except Exception as e:
        print(f"❌ EXCEPTION at exchange.order(): {type(e).__name__}: {e}")
        print()
        print("=" * 70)
        print("OUTCOME: SDK_EXCEPTION")
        print("=" * 70)
        return

    print("Raw response:")
    print(json.dumps(result, indent=2, default=str))
    print()

    category, detail = _classify(result)
    print("=" * 70)

    if category == "RESTING":
        oid = int(detail.replace("oid=", ""))
        print(f"✅ OUTCOME: RESTING — oid={oid}")
        print(f"   Agent CAN trade xyz:CL without any 3C setup!")
        print(f"   Unified account mode handles cross-dex collateral automatically.")
        print("=" * 70)

        # Verify via open_orders
        time.sleep(1.0)
        orders = info.open_orders(main_address)
        print()
        print(f"Verification: info.open_orders(main) → {len(orders)} orders")
        found = False
        for o in orders:
            if int(o.get("oid", -1)) == oid:
                found = True
                print(f"  ✅ our order present: {json.dumps(o, default=str)}")
                break
        if not found:
            print(f"  ⚠️  our order NOT in open_orders list")
            print(f"     all oids in list: {[o.get('oid') for o in orders]}")
            print(f"     all coins: {sorted({o.get('coin') for o in orders})}")

        # Cancel
        print()
        print(f"Cancelling oid {oid}...")
        cancel_result = exchange.cancel(COIN, oid)
        print(f"Cancel raw: {json.dumps(cancel_result, default=str)}")

        time.sleep(1.0)
        after = info.open_orders(main_address)
        still_there = any(int(o.get("oid", -1)) == oid for o in after)
        if not still_there:
            print(f"✅ Cancel verified — order no longer in open_orders")
        else:
            print(f"⚠️  Order STILL in open_orders after cancel — investigate")

    elif category in ("API_ERR", "STATUS_ERROR"):
        err_tag = _tag_err(detail)
        print(f"❌ OUTCOME: {err_tag}")
        print(f"   Raw error: {detail[:300]}")
        print("=" * 70)
        print()
        if err_tag == "ERR_AUTH":
            print("→ Need Phase 3C: user opts in via Rabby + agent opts in via Python")
        elif err_tag == "ERR_MARGIN":
            print("→ Collateral IS isolated per-dex. Need full 3C including a transfer.")
        else:
            print("→ Unexpected error. Dump the full response above and we debug together.")

    elif category == "FILLED":
        print(f"⚠️  OUTCOME: UNEXPECTED FILL")
        print(f"   Details: {detail}")
        print("=" * 70)
        print()
        print("This should not have been possible at 50% below market.")
        print("Either BTC-style flash crash on oil, or a bug in limit price calc.")
        print("Closing the position immediately...")
        close_result = exchange.market_close(coin=COIN)
        print(f"Close raw: {json.dumps(close_result, default=str)}")

    else:
        print(f"? OUTCOME: {category}")
        print(f"   Details: {detail}")
        print("=" * 70)


if __name__ == "__main__":
    main()
