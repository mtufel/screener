# Design: Phase 0 hygiene → Phase 1 models → Phase 2 tracker

Phase 0: Drop SYM_ALIASES leftover imports; move lookup_mid to top of process_live_setups; consolidate IST/TIMEFRAME_MS.
Phase 1: models.py with Candle, FVG, TIMEFRAME_MS, IST; strategy_extreme_fvg and strategy import and re-export.
Phase 2: Extract _ingest_setups, _monitor_pending, _monitor_active from process_live_setups; preserve event tuple format.
