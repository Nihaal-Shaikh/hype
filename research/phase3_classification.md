# Phase 3A Step 3 — Classification

**Date**: 2026-04-11
**Inputs**: `research/phase3_sdk_survey.md`, `research/phase3_sdk_probe.md`
**Verdict**: **HYBRID FORCED** (neither GREEN nor YELLOW nor RED as originally defined)

---

## Why the plan's 3-tier classification doesn't fit

The ralplan design assumed:
- **GREEN**: "A1 (SDK path) works end-to-end, do $12 transfer via Python"
- **YELLOW**: "A1 works but with unclear args, do $2 first, escalate"
- **RED**: "A1 fails, fall back to A2 (deployer web page) for Phase 3D only"

Both the source survey AND the confirmation probe independently verified that the underlying assumption was wrong:

**User-signed actions cannot be signed by an agent wallet.** This is an architectural property of Hyperliquid's signing design, not a configuration or a bug. The three Phase 3C steps split across signing pathways as follows:

| Step | Action | Signing pathway | Who can sign? |
|---|---|---|---|
| 3C.1 | `user_dex_abstraction(main, True)` | `sign_user_signed_action` / EIP-712 HyperliquidSignTransaction | **Main wallet only** (Rabby) |
| 3C.2 | `send_asset(main, "", "xyz", "USDC", 12)` | `sign_user_signed_action` / EIP-712 HyperliquidSignTransaction | **Main wallet only** (Rabby) |
| 3C.3 | `agent_enable_dex_abstraction()` | `sign_l1_action` / EIP-712 Exchange phantom agent | **Agent wallet** ✅ |

So the classification is NOT "A1 vs A2". It's:
- **3C.1 and 3C.2 MUST go through the Hyperliquid web UI** (or a deployer-specific onboarding page) so that Rabby can sign with the main wallet. No SDK alternative exists from our Mac because the main wallet's private key is in Rabby, not on disk — and it should stay there.
- **3C.3 is the one step we CAN execute via Python** once 3C.1 and 3C.2 are done.

## Updated execution flow for Phase 3C

### 3C.1 — Enable dex abstraction (USER action, Brave Beta, Rabby)
- **Status**: blocked by unknown UI path
- **What user needs to do**: open Hyperliquid mainnet in Brave Beta and find the "enable dex abstraction" option. Likely under account settings / the address dropdown / a per-dex onboarding page. We don't know the exact click path yet — this is the first UI exploration task.
- **Alternative if Hyperliquid's main UI doesn't have it**: try visiting https://app.hyperliquid.xyz/trade/xyz:CL directly and see if there's an onboarding banner asking to "enable this dex" or similar. Deployer-specific flows may exist.
- **Verification**: after signing, re-run the probe and confirm `info.query_user_dex_abstraction_state(main) == True`.

### 3C.2 — Deposit USDC to xyz dex (USER action, Brave Beta, Rabby)
- **Status**: blocked until 3C.1 is done
- **What user needs to do**: find the "transfer to xyz dex" or "deposit to xyz" flow in Hyperliquid's UI. This likely unlocks once dex abstraction is enabled on the account.
- **Amount**: $12 USDC (plan default — enough for one $10-minimum order with buffer)
- **Verification**: after signing, re-run the probe and confirm `info.user_state(main, dex="xyz")["withdrawable"] >= 11.0`.

### 3C.3 — Agent opts into dex abstraction (PYTHON, agent-signed)
- **Status**: ready to execute once 3C.1 and 3C.2 are verified
- **What we do**: write `scripts/enable_agent_dex_abstraction.py` that constructs `Exchange(wallet=agent, base_url=MAINNET, account_address=main, perp_dexs=["", "xyz"])` and calls `exchange.agent_enable_dex_abstraction()`.
- **Verification**: Phase 3D write test successfully places and cancels an order on `xyz:CL`.

## Probe-confirmed facts that make Phase 3B safe to build

Even though 3C is blocked on UI exploration, Phase 3B (multi-dex read layer) can proceed immediately because all reads are confirmed working:

- ✅ `Info(perp_dexs=["", "xyz"])` loads 870 name_to_coin entries including all xyz markets
- ✅ `info.all_mids(dex="xyz")` returns 62 xyz markets with live prices
- ✅ `info.l2_snapshot("xyz:CL")` works and shows a real order book with 3.12 bps spread
- ✅ `info.user_state(main, dex="xyz")` returns a clean zero state
- ✅ `info.perp_dexs()` enumerates all 9 deployed dexes

The multi-dex dashboard, asset-class classifier, universe module, and `tests/test_hours.py` can all be built and verified against live data WITHOUT needing any signed action.

## Hard STOP gate

Per the plan, no on-chain action proceeds without explicit user acknowledgement. The user must now choose one of:

1. **Proceed HYBRID FORCED** — I build Phase 3B (read layer) now while you explore Brave Beta to find the UI path for 3C.1 and 3C.2. When you've completed both via Rabby, we run `scripts/enable_agent_dex_abstraction.py` in Python, then move to Phase 3D.

2. **Park 3C and ship 3B only** — build the multi-dex read layer + dashboard + tests as a self-contained Phase 3 deliverable, defer cross-dex trading to Phase 4 when we have more information about the UI path. Phase 3 would end without a non-crypto write test, but the infra for one would be ready.

3. **Explore the UI first, build after** — user spends 10 min in Brave Beta looking for the dex abstraction toggle before I build anything. If found, we continue with option (1). If not found, we decide between parking and looking for a deployer-specific onboarding page.

## Budget impact

Unchanged. Still inside the $30 Phase 3 ceiling. The only on-chain activity under option (1) is:
- User-signed: $12 transfer to xyz (your money, moves between your accounts, ~$0 fee)
- Agent-signed: `agent_enable_dex_abstraction` (L1 action, tiny gas if any, ~$0.01)
- Phase 3D test order: $12 limit far-from-market, cancel, ~$0 fee

Expected total spend on Phase 3C + 3D: < $0.20. Worst case on a surprise fill in 3D: ~$0.15.

## Confidence assessment

- **HYBRID FORCED classification**: HIGH confidence. Directly evidenced by the survey (signing pathways) and the probe (confirmed dex abstraction still false, confirmed xyz account empty, confirmed the reads work).
- **UI path for 3C.1**: LOW confidence. We haven't explored the UI yet.
- **UI path for 3C.2**: LOW confidence. Same reason.
- **3C.3 will work after 3C.1 and 3C.2**: HIGH confidence. It's a clean L1 action the agent is architecturally allowed to make.
