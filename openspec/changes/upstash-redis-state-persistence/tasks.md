# Implementation Tasks: Upstash Redis State Persistence

## Tasks

### Phase 1: Dependencies & Redis Client Module
- [x] Task 1.1: Add `redis>=5.0.0` to `requirements.txt` and install in `.venv`.
- [x] Task 1.2: Implement `redis_client.py` with async client support (`REDIS_URL` & Upstash REST) and automatic exception swallowing/fallback.

### Phase 2: Live Trade Ledger & Alert Deduplication Integration
- [x] Task 2.1: Update `ExtremeTradeTracker` in `extreme_trade_tracker.py` to sync state with Redis key `screener:extreme_trades`.
- [x] Task 2.2: Add Redis alert key tracking (`screener:alert:<symbol>:<event>:<trade_id>`) to prevent duplicate Telegram alerts.

### Phase 3: 4H FVG Cache Integration
- [x] Task 3.1: Update `HTFFVGCache` in `strategy_extreme_fvg.py` to persist and retrieve active 4H anchors from `screener:htf_cache:<symbol>:<mode>`.

### Phase 4: Server Runtime Config & Endpoints
- [x] Task 4.1: Update `main.py` to restore runtime config overrides from `screener:config` on startup.
- [x] Task 4.2: Update `/api/extreme/config` to sync runtime updates with Redis.

### Phase 5: Verification & Testing
- [x] Task 5.1: Create `test_redis_persistence.py` with unit tests for ledger persistence, alert deduplication, HTF cache, and fallback.
- [x] Task 5.2: Run full test suite (`pytest -v`) to confirm 100% pass rate.
