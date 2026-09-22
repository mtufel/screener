# Tasks — refactor-core-python-structure

- [x] 0. Hygiene (constants/imports, lookup_mid top) — branch has this
- [x] 1. Phase 0: drop unused imports, consolidate IST/TIMEFRAME_MS
- [x] 2. Phase 1: create models.py + re-exports (Candle/FVG union)
- [x] 3. Phase 2: tracker extract (_ingest, _monitor_pending, _monitor_active), freeze event tuples
- [ ] 4. Verify pytest unchanged (242 pass; 9 ccxt pre-existing)
