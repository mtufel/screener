# Design: Remove Strategy 1, keep Strategy 2

## Decisions
1. **Hard delete, not a feature flag.** No `ENABLE_STRATEGY_1` shim and no `strategy_1_enabled` JSON fields. Existing tests that assert those fields are updated or removed because the product no longer has Strategy 1.
2. **`get_last_n_candles` moves to `strategy_extreme_fvg.py`** with identical semantics (drop the in-progress bar via `raw[:-1]`). Do not “fix” open-candle exclusion in this change.
3. **Dashboard:** Strategy 2 is the only view. Strategy 1 tab/wrapper is hidden/removed; scan button always hits Extreme scan.
4. **Telegram test endpoint** uses `generate_extreme_setup_chart` instead of Strategy 1 `generate_setup_chart`.
5. **Refactor after deletion:** extract pending/active monitors from `process_live_setups`; do not change event names or SL-before-TP order.

## Test policy
- Delete tests whose sole subject is Strategy 1.
- Update tests that only mention `strategy_1_enabled` as an API key.
- Do not change Strategy 2 behavioral tests.
