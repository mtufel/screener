# Implementation Tasks: Fix 4H FVG Detection & Provider Fallback Delegation

- [x] Task 1: Update `market_data/binance.py` `get_last_n_candles` rate-limit guard and HTTP 418/429 handlers to check `len(cached) >= min(n, 50)` and delegate to `self.fallback_provider`.
- [x] Task 2: Update `market_data/ccxt_provider.py` `get_last_n_candles` rate-limit guard to check `len(cached) >= min(n, 50)` and delegate to `self.fallback_provider`.
- [x] Task 3: Update `api/extreme.py` `/api/extreme/4h-fvgs` endpoint to use canonical base symbol `sym` and guard against `< 3` candles overwriting `htf_fvg_cache`.
- [x] Task 4: Add comprehensive unit & integration tests in `test_4h_fvg_fallback_resilience.py`.
- [x] Task 5: Run full test suite (`.venv/bin/pytest -v`) to ensure 100% pass rate.
- [x] Task 6: Check off all tasks in `tasks.md` and verify clean working tree.
