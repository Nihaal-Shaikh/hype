# Phase 3A Step 1 — SDK Source Survey

**Date**: 2026-04-11
**SDK version**: `hyperliquid-python-sdk` 0.22.0
**Environment**: `/Users/nihaalshaikh/PROJECTEN/Personal/hype/venv/lib/python3.14/site-packages/hyperliquid/`
**Purpose**: Document exact source facts for multi-dex trading before any live API call. No live calls in this step.

---

## Executive summary

Reading the SDK source resolves all four Phase 3A unknowns and surfaces **one critical architectural finding** that reshapes Phase 3C execution:

1. ✅ **`l2_snapshot(name)` takes no `dex` kwarg.** Confirmed at `info.py:446-471`. It looks up `self.name_to_coin[name]`, which is populated at `Info.__init__` only for dexes passed via `perp_dexs=[...]`. To query `xyz:CL`, we MUST construct `Info(base_url, skip_ws=True, perp_dexs=["", "xyz"])` and call `l2_snapshot("xyz:CL")` — the full prefixed name.

2. ✅ **`name_to_coin` mapping is a passthrough for perps.** Confirmed at `info.py:71-76` via `set_perp_meta`: `self.name_to_coin[asset_info["name"]] = asset_info["name"]`. The key IS the value. For `xyz:CL` the lookup is just `name_to_coin["xyz:CL"] = "xyz:CL"`.

3. ✅ **`Exchange._get_dex(coin)`** at `exchange.py:55-56` confirms naming convention: `return coin.split(":")[0] if ":" in coin else ""`. So `"xyz:CL"` has dex `"xyz"`, core `"BTC"` has dex `""`.

4. ✅ **`send_asset` exists and supports cross-dex transfers.** Confirmed at `exchange.py:473-496`, signature `send_asset(destination, source_dex, destination_dex, token, amount)`. Docstring: "For the default perp dex use the empty string `""` as name."

## 🚨 CRITICAL finding: agent wallet cannot sign the transfer or the opt-in

Both `user_dex_abstraction` and `send_asset` are **user-signed actions**, not L1 actions. They require the main wallet's private key, which is in Rabby in the browser — NOT on this Mac. Our Python bot cannot execute them directly regardless of how dex abstraction is configured.

This means **A1 (SDK-based transfer) is not viable from the current architecture**. Only A2 (Hyperliquid UI / deployer web page, signed by Rabby) can actually move USDC across dexes or enable dex abstraction.

The full reasoning is in Section 3 below.

---

## 1. Source facts (`info.py`)

### `Info.__init__` populates `name_to_coin` from the `perp_dexs` list
`info.py:18-69`

```python
def __init__(
    self,
    base_url: Optional[str] = None,
    skip_ws: Optional[bool] = False,
    meta: Optional[Meta] = None,
    spot_meta: Optional[SpotMeta] = None,
    # Note that when perp_dexs is None, then "" is used as the perp dex. "" represents
    # the original dex.
    perp_dexs: Optional[List[str]] = None,
    timeout: Optional[float] = None,
):
    ...
    self.coin_to_asset = {}
    self.name_to_coin = {}
    self.asset_to_sz_decimals = {}

    # spot assets start at 10000 ... (spot handling omitted)

    perp_dex_to_offset = {"": 0}
    if perp_dexs is None:
        perp_dexs = [""]
    else:
        for i, perp_dex in enumerate(self.perp_dexs()[1:]):
            # builder-deployed perp dexs start at 110000
            perp_dex_to_offset[perp_dex["name"]] = 110000 + i * 10000

    for perp_dex in perp_dexs:
        offset = perp_dex_to_offset[perp_dex]
        if perp_dex == "" and meta is not None:
            self.set_perp_meta(meta, 0)
        else:
            fresh_meta = self.meta(dex=perp_dex)
            self.set_perp_meta(fresh_meta, offset)
```

**Facts:**
- Default when `perp_dexs=None` is `[""]` — only the core dex is loaded.
- To load multiple dexes, pass them explicitly: `perp_dexs=["", "xyz"]`.
- Each dex contributes to the same shared `name_to_coin` dict.
- Builder-deployed dexes get asset offsets starting at 110000.

### `Info.set_perp_meta` populates `name_to_coin[name] = name`
`info.py:71-76`

```python
def set_perp_meta(self, meta: Meta, offset: int) -> Any:
    for asset, asset_info in enumerate(meta["universe"]):
        asset += offset
        self.coin_to_asset[asset_info["name"]] = asset
        self.name_to_coin[asset_info["name"]] = asset_info["name"]
        self.asset_to_sz_decimals[asset] = asset_info["szDecimals"]
```

**Fact:** For perps, `name_to_coin` is a **passthrough** — key and value are both the full name. For `xyz:CL` the entry is `"xyz:CL" → "xyz:CL"`.

### `Info.l2_snapshot(name)` signature and lookup
`info.py:446-471`

```python
def l2_snapshot(self, name: str) -> Any:
    """Retrieve L2 snapshot for a given coin
    ...
    """
    return self.post("/info", {"type": "l2Book", "coin": self.name_to_coin[name]})
```

**Facts:**
- Signature is `(self, name: str)`. NO `dex` parameter exists.
- Body looks up `self.name_to_coin[name]`. If `name` isn't in the dict, raises `KeyError`.
- To query `xyz:CL`, `Info` must have been constructed with `perp_dexs=["", "xyz"]` OR `perp_dexs=["xyz"]`. Otherwise `l2_snapshot("xyz:CL")` raises `KeyError: 'xyz:CL'` — which is exactly the error we saw in our earlier exploration.

### `Info.meta(dex="")` and `Info.all_mids(dex="")` DO take dex kwargs
`info.py:271-287`

```python
def meta(self, dex: str = "") -> Meta:
    ...
    return cast(Meta, self.post("/info", {"type": "meta", "dex": dex}))
```

**Fact:** `meta()` and (similarly) `all_mids()`, `user_state()`, `spot_user_state()` accept `dex=` as a kwarg. But `l2_snapshot()` does NOT — it relies on the dict constructed at init time.

---

## 2. Source facts (`exchange.py`)

### `_get_dex(coin)` confirms naming convention
`exchange.py:55-56`

```python
def _get_dex(coin: str) -> str:
    return coin.split(":")[0] if ":" in coin else ""
```

**Fact:** Dex prefix is extracted via `coin.split(":")[0]`. `"xyz:CL"` → `"xyz"`, `"BTC"` → `""` (core).

### `Exchange.__init__` passes `perp_dexs` through to Info
`exchange.py:63-79`

```python
def __init__(
    self,
    wallet: LocalAccount,
    base_url: Optional[str] = None,
    meta: Optional[Meta] = None,
    vault_address: Optional[str] = None,
    account_address: Optional[str] = None,
    spot_meta: Optional[SpotMeta] = None,
    perp_dexs: Optional[List[str]] = None,
    timeout: Optional[float] = None,
):
    super().__init__(base_url, timeout)
    self.wallet = wallet
    self.vault_address = vault_address
    self.account_address = account_address
    self.info = Info(base_url, True, meta, spot_meta, perp_dexs, timeout)
    ...
```

**Facts:**
- `Exchange` auto-constructs its own `Info` internally (with `skip_ws=True`), passing `perp_dexs` through.
- So `Exchange(wallet=agent, base_url=..., account_address=main, perp_dexs=["", "xyz"])` gives us a fully multi-dex aware Exchange that can place orders on `"xyz:CL"`.

### `Exchange.send_asset` signature and signing
`exchange.py:473-496`

```python
def send_asset(self, destination: str, source_dex: str, destination_dex: str, token: str, amount: float) -> Any:
    """
    For the default perp dex use the empty string "" as name. For spot use "spot".
    Token must match the collateral token if transferring to or from a perp dex.
    """
    timestamp = get_timestamp_ms()
    str_amount = str(amount)

    action = {
        "type": "sendAsset",
        "destination": destination,
        "sourceDex": source_dex,
        "destinationDex": destination_dex,
        "token": token,
        "amount": str_amount,
        "fromSubAccount": self.vault_address if self.vault_address else "",
        "nonce": timestamp,
    }
    signature = sign_send_asset_action(self.wallet, action, self.base_url == MAINNET_API_URL)
    return self._post_action(action, signature, timestamp)
```

**Facts:**
- Signed via `sign_send_asset_action(self.wallet, ...)`.
- `self.wallet` is whatever `Exchange` was constructed with — agent wallet in our case.
- **But** (see Section 3 below) `sign_send_asset_action` is a user-signed action, not L1. It requires the main wallet's signature, not the agent's.

### `Exchange.user_dex_abstraction(user, enabled)` — main-signed
`exchange.py:1165-1178`

```python
def user_dex_abstraction(self, user: str, enabled: bool) -> Any:
    timestamp = get_timestamp_ms()
    action = {
        "type": "userDexAbstraction",
        "user": user.lower(),
        "enabled": enabled,
        "nonce": timestamp,
    }
    signature = sign_user_dex_abstraction_action(self.wallet, action, self.base_url == MAINNET_API_URL)
    return self._post_action(action, signature, timestamp)
```

**Facts:**
- Takes the user's address as an explicit argument.
- Signed via `sign_user_dex_abstraction_action(self.wallet, ...)` → **user-signed action**, main wallet only (Section 3).

### `Exchange.agent_enable_dex_abstraction()` — agent-signed (L1)
`exchange.py:1126-1143`

```python
def agent_enable_dex_abstraction(self) -> Any:
    timestamp = get_timestamp_ms()
    action = {
        "type": "agentEnableDexAbstraction",
    }
    signature = sign_l1_action(
        self.wallet,
        action,
        self.vault_address,
        timestamp,
        self.expires_after,
        self.base_url == MAINNET_API_URL,
    )
    return self._post_action(action, signature, timestamp)
```

**Facts:**
- No explicit user arg — signs for "this agent".
- Signed via `sign_l1_action(self.wallet, ...)` → **L1 action**, CAN be signed by an agent wallet.
- **This is the ONE dex-abstraction method that our Python-based agent CAN execute.**

### `Exchange._post_action` special-cases sendAsset + usdClassTransfer
`exchange.py:81-90`

```python
def _post_action(self, action, signature, nonce):
    payload = {
        "action": action,
        "nonce": nonce,
        "signature": signature,
        "vaultAddress": self.vault_address if action["type"] not in ["usdClassTransfer", "sendAsset"] else None,
        "expiresAfter": self.expires_after,
    }
    ...
```

**Fact:** `sendAsset` and `usdClassTransfer` explicitly null out `vaultAddress` in the payload — because these are account-level (user-signed) operations, not sub-account operations.

---

## 3. The critical finding — L1 actions vs user-signed actions

`exchange.py:11-37` lists the sign functions used by the Exchange class:

```python
from hyperliquid.utils.signing import (
    ...
    sign_l1_action,              # ← L1 actions: trading, agent_enable_dex_abstraction
    sign_send_asset_action,      # ← user-signed
    sign_spot_transfer_action,   # ← user-signed
    sign_token_delegate_action,  # ← user-signed
    sign_usd_class_transfer_action,  # ← user-signed
    sign_usd_transfer_action,    # ← user-signed
    sign_user_dex_abstraction_action,  # ← user-signed
    sign_user_set_abstraction_action,  # ← user-signed
    sign_withdraw_from_bridge_action,  # ← user-signed
)
```

`signing.py` defines two distinct signing pathways:

### L1 actions (`sign_l1_action`, signing.py:239-243)
```python
def sign_l1_action(wallet, action, active_pool, nonce, expires_after, is_mainnet):
    hash = action_hash(action, active_pool, nonce, expires_after)
    phantom_agent = construct_phantom_agent(hash, is_mainnet)
    data = l1_payload(phantom_agent)
    return sign_inner(wallet, data)
```

- Uses the "phantom agent" pattern with a dedicated EIP-712 domain (`Exchange`, chainId 1337)
- Designed to be signable by **delegated agent wallets** — this is how approved agents trade without the main wallet needing to sign every order
- Used by: `order`, `cancel`, `market_open`, `market_close`, `update_leverage`, `agent_enable_dex_abstraction`, `sub_account_transfer`, etc.
- **Our `bot-dev-v2` agent can sign these.**

### User-signed actions (`sign_user_signed_action`, signing.py:246-252)
```python
def sign_user_signed_action(wallet, action, payload_types, primary_type, is_mainnet):
    action["signatureChainId"] = "0x66eee"
    action["hyperliquidChain"] = "Mainnet" if is_mainnet else "Testnet"
    data = user_signed_payload(primary_type, payload_types, action)
    return sign_inner(wallet, data)
```

- Uses `HyperliquidSignTransaction` EIP-712 domain
- Designed to require the **actual account owner's signature** — the main wallet
- Used by: `usd_class_transfer`, `send_asset`, `user_dex_abstraction`, `withdraw_from_bridge`, `spot_transfer`, `usd_transfer`
- **Our agent wallet CANNOT sign these.** If it does, Hyperliquid rejects the action — which is exactly what happened earlier with our `transfer.py` attempt that returned `'Must deposit before performing actions. User: 0x19553adb960a1d210164eb93a72bdc34224475b4'`. The backend saw the agent as the signer, looked up its deposits (zero), and rejected.

### Why this matters for Phase 3C

The three actions Phase 3C wanted to automate:
1. `user_dex_abstraction(main, True)` — **user-signed** → requires main wallet
2. `send_asset(main, "", "xyz", "USDC", 12)` — **user-signed** → requires main wallet
3. `agent_enable_dex_abstraction()` — **L1** → CAN be agent-signed

Our main wallet's private key lives in Rabby in Brave Beta. **It is not on this Mac. It should not be on this Mac.** That was an explicit security decision from Phase 2 when we rotated the compromised agent key — main wallet keys stay in Rabby, period.

Therefore:
- **Steps 1 and 2 of the Phase 3C flow CANNOT be executed from Python.** They must be done via the Hyperliquid web UI in Brave Beta, where Rabby signs.
- **Step 3 (agent_enable_dex_abstraction) CAN be executed from Python** once steps 1 and 2 are done.

---

## 4. Hypothesis for the actual Phase 3C execution flow

Based on the source reading, the reality-compatible Phase 3C flow is:

### Step 3C.1 — User enables dex abstraction (Hyperliquid UI, Rabby signature)
- Path: Hyperliquid portfolio / settings / ? (need to find the exact button in the UI — likely under the address dropdown or account settings)
- What happens: Hyperliquid UI calls `user_dex_abstraction(main, True)` internally, Rabby pops up, user signs
- Verifiable via: `info.query_user_dex_abstraction_state(main) == True`

### Step 3C.2 — User deposits USDC to xyz dex (Hyperliquid UI, Rabby signature)
- Path: either (a) the xyz deployer's own website has an "onboard / deposit USDC" flow that triggers `send_asset(main, "", "xyz", "USDC", amount)` signed by Rabby, OR (b) Hyperliquid's main UI has a "transfer between dexes" option that's now enabled because dex abstraction is on
- Verifiable via: `info.user_state(main, dex="xyz")["withdrawable"] >= 11.0`

### Step 3C.3 — Agent opts into dex abstraction (Python, agent-signed)
- Path: `scripts/enable_agent_dex_abstraction.py` — one call to `exchange.agent_enable_dex_abstraction()` where `exchange` is constructed with `wallet=agent, account_address=main, perp_dexs=["", "xyz"]`
- Verifiable via: Phase 3D write test actually succeeding

### Step 3C.4 — Phase 3D write test on `xyz:CL`
- Construct `Exchange(wallet=agent, base_url=MAINNET, account_address=main, perp_dexs=["", "xyz"])`
- Compute size from `info.all_mids(dex="xyz")["xyz:CL"]`
- Call `exchange.order(name="xyz:CL", is_buy=True, sz=size, limit_px=far_below_market, order_type={"limit": {"tif": "Gtc"}}, reduce_only=False)`
- Check `info.open_orders(main)` or `info.frontend_open_orders(main)` (may need to check if these support multi-dex)
- Cancel via `exchange.cancel("xyz:CL", oid)`

### Notable order-op behavior to verify in Step 2 probe

The `open_orders` method may or may not return cross-dex orders in one call. Need to verify whether `open_orders(address)` returns a flat list across all dexes or needs per-dex filtering. The plan's Phase 3D acceptance criterion depends on being able to see the xyz order in the list.

---

## 5. Updated classification for A1/A2 decision gate

Based on this source survey — **without** running the probe step:

| Criterion | Source evidence | Verdict |
|---|---|---|
| Does `user_dex_abstraction` exist in SDK? | Yes, `exchange.py:1165-1178` | ✅ |
| Can agent wallet sign it? | NO — it's a user-signed action (`sign_user_dex_abstraction_action`, signing.py:381-388) | ❌ |
| Does `send_asset` exist in SDK? | Yes, `exchange.py:473-496` | ✅ |
| Can agent wallet sign it? | NO — it's a user-signed action (`sign_send_asset_action`, signing.py:371-378) | ❌ |
| Does `agent_enable_dex_abstraction` exist? | Yes, `exchange.py:1126-1143` | ✅ |
| Can agent wallet sign it? | YES — it's an L1 action (`sign_l1_action`, signing.py:239-243) | ✅ |

**Pre-probe classification: effectively RED for Phase 3C steps 1 & 2, GREEN for step 3.**

The plan's A1/A2 dichotomy was built on the assumption that "A1 = SDK path, A2 = web page path". With the source survey, the picture is more nuanced:

- **Transfer + user opt-in MUST go through the web UI** (Rabby signature) — this is not a choice, it's an architectural constraint of Hyperliquid's user-signed action design
- **Agent opt-in CAN go through the SDK** — we will execute this via Python

So rather than "A1 vs A2", the real Phase 3C is: "hybrid flow — UI for the two user-signed steps, Python for the agent opt-in".

---

## 6. What the Step 2 probe still needs to answer

The source reading answered the architectural questions. The probe (Phase 3A Step 2) still needs to answer three empirical questions:

1. **How is `xyz:CL` actually keyed in `name_to_coin` after constructing `Info(perp_dexs=["", "xyz"])`?** Hypothesis says `"xyz:CL"`. Confirm by inspecting the keys at runtime.
2. **Does `info.all_mids()` (without dex arg) return only core, or all dexes flattened?** Source is ambiguous. Need to call it and see.
3. **Does `info.open_orders(main)` return cross-dex orders or only core?** Source is ambiguous. Need to place a test order on xyz (Phase 3D) and see if the query returns it.
4. **What's the exact UI path for enabling dex abstraction?** User will need to find this in Brave Beta (spoken navigation, not SDK).

---

## 7. Open questions to escalate before Phase 3C

- **Where in the Hyperliquid UI is the "enable dex abstraction" toggle?** Under account settings? Under the xyz dex page? Under a specific deployer website? The plan should not proceed to 3C until we've identified the clickable path.
- **Does the xyz deployer have its own onboarding site, or is the transfer done through Hyperliquid's main UI?** Finding this out is itself a task — likely done by opening https://app.hyperliquid.xyz/trade/xyz:CL in Brave Beta and looking for an "enable this dex" or "deposit to xyz" button.
- **Is there a way to verify the xyz dex deposit on-chain/via API before placing an order?** Yes: `info.user_state(main, dex="xyz")["withdrawable"]` should reflect the deposited amount.

---

## 8. Confidence assessment

- Findings 1–4 in the Executive Summary: **HIGH confidence** — directly quoted from SDK source, verified line numbers.
- The "agent cannot sign user-signed actions" conclusion: **HIGH confidence** — consistent with how sign_l1_action vs sign_user_signed_action are separated in signing.py, and consistent with the empirical error we got earlier from `transfer.py` ("Must deposit before performing actions. User: <agent_address>").
- The "hybrid UI + Python" Phase 3C hypothesis: **MEDIUM-HIGH confidence** — the architecture forces this, but the exact UI path is unconfirmed until Step 2.
- The `open_orders` cross-dex behavior: **LOW confidence** — can only be answered empirically.

---

## 9. Recommendation for next step

Proceed to **Phase 3A Step 2 (confirmation probe)** with an updated probe script that:

1. Constructs `Info(MAINNET_API_URL, skip_ws=True, perp_dexs=["", "xyz"])`
2. Prints `sorted(k for k in info.name_to_coin.keys() if "xyz" in k or k == "BTC")` to confirm the naming
3. Calls `info.all_mids()` with no dex arg, and with `dex="xyz"`, and diffs
4. Calls `info.l2_snapshot("xyz:CL")` at a known-open window to confirm it works now
5. Calls `info.query_user_dex_abstraction_state(main)` to confirm false (already done earlier)
6. Calls `info.user_state(main, dex="xyz")["withdrawable"]` to confirm 0
7. Calls `info.open_orders(main)` to see if the query key is just the address or needs dex filtering

The probe will NOT attempt any `send_asset`, `user_dex_abstraction`, or write operation — those are blocked by the user-signed constraint and require the UI path.

After the probe, we move to Phase 3C (hybrid flow) with the user at the Brave Beta keyboard for the two Rabby-signed steps, and our Python script for the agent opt-in.
