# Tasks: Strategy 2 Hardening

- [x] 1. Update `extreme_trade_tracker.py` to resolve `SYMBOL_ALIASES` for live mid-price lookups.
- [x] 2. Implement pre-entry invalidation for `PENDING_RETRACE` setups in `extreme_trade_tracker.py` on SL or anchor breach.
- [x] 3. Refactor `/api/extreme/scan` in `main.py` to read query parameters with fallback to daemon `state` without mutation.
- [x] 4. Add regression unit tests in `test_extreme_trade_tracker.py` for pending invalidation and alias mid-price lookups.
- [x] 5. Verify full test suite passes with 100% test coverage (88/88 passed).
