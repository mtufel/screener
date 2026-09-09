# Implementation Tasks: Environment Segregation

## Tasks
- [x] Task 1: Update `redis_client.py` with environment-aware key prefixing (`APP_ENV`, `REDIS_KEY_PREFIX`, `get_key`).
- [x] Task 2: Update `extreme_trade_tracker.py` to use environment-isolated storage file defaults and namespaced Redis keys.
- [x] Task 3: Update `strategy_extreme_fvg.py` to use namespaced Redis HTF cache keys.
- [x] Task 4: Update `telegram_client.py` and `main.py` with `TELEGRAM_ENABLED`, dev chat routing, and `[LOCAL]`/`[DEV]` message tagging in non-production environments.
- [x] Task 5: Add unit tests in `test_redis_persistence.py` verifying namespace isolation and environment switching.
- [x] Task 6: Run full pytest suite to verify 100% pass rate.
