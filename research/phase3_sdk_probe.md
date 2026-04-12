Phase 3A Step 2 — confirmation probe
Started: 2026-04-11T21:26:56+00:00
Main wallet: 0xAb8281b4408035d0FDfab1929A0CC40Cf8B29Bf0
Network: mainnet

======================================================================
1. Construct Info(perp_dexs=['', 'xyz']) and inspect name_to_coin
======================================================================
  Total keys in name_to_coin: 870
  Sample core keys found: ['BTC', 'ETH', 'HYPE', 'SOL']
  xyz:* keys found: 62
  First 10 xyz keys: ['xyz:AAPL', 'xyz:ALUMINIUM', 'xyz:AMD', 'xyz:AMZN', 'xyz:BABA', 'xyz:BRENTOIL', 'xyz:BX', 'xyz:CL', 'xyz:COIN', 'xyz:COPPER']
  ✅ 'xyz:CL' IS in name_to_coin
     value: 'xyz:CL'
     coin_to_asset: 110029

======================================================================
2. info.all_mids() with NO dex arg
======================================================================
  Total keys: 537
  xyz:* keys in default all_mids: 0
  'BTC' in default all_mids: True
  'xyz:CL' in default all_mids: False
  → Conclusion: core only

======================================================================
3. info.all_mids(dex='xyz')
======================================================================
  Total keys: 62
  Sample xyz keys: ['xyz:AAPL', 'xyz:ALUMINIUM', 'xyz:AMD', 'xyz:AMZN', 'xyz:BABA', 'xyz:BRENTOIL', 'xyz:BX', 'xyz:CL', 'xyz:COIN', 'xyz:COPPER']
  'xyz:CL' in all_mids(dex='xyz'): True
  xyz:CL price: $89.7760
  xyz:GOLD price: $4,751.9500
  xyz:TSLA price: $352.2900

======================================================================
4. info.l2_snapshot('xyz:CL')
======================================================================
  ✅ Call succeeded
  coin: xyz:CL
  time: 1775942819199
  levels shape: 2 sides
  Top 3 bids: [('89.762', '64.625'), ('89.761', '2.447'), ('89.753', '83.169')]
  Top 3 asks: [('89.79', '1.891'), ('89.798', '12.836'), ('89.8', '174.358')]
  best_bid=$89.7620  best_ask=$89.7900  spread=$0.0280 (3.12 bps)

======================================================================
4b. info.l2_snapshot('xyz:GOLD')  (fallback — gold trades nearly 24h)
======================================================================
  ✅ xyz:GOLD book: bid=$4,751.9000  ask=$4,752.0000

======================================================================
5. info.query_user_dex_abstraction_state(main)
======================================================================
  Result: False
  Type: bool
  → Dex abstraction is currently: DISABLED

======================================================================
6. info.user_state(main, dex='xyz')
======================================================================
  accountValue: $0.0000
  withdrawable: $0.0000
  positions: 0

======================================================================
7. info.open_orders(main) — shape and cross-dex behavior
======================================================================
  Total orders returned: 0
  (no open orders — cannot verify cross-dex behavior yet; will verify in Phase 3D)

======================================================================
8. info.perp_dexs()  (full list of deployed dexes)
======================================================================
  [0] None  (core perps dex)
  [1] xyz        = XYZ
  [2] flx        = Felix Exchange
  [3] vntl       = Ventuals
  [4] hyna       = HyENA
  [5] km         = Markets by Kinetiq
  [6] abcd       = ABCDEx
  [7] cash       = dreamcash
  [8] para       = Paragon

======================================================================
Probe complete
======================================================================
Finished: 2026-04-11T21:27:01+00:00
