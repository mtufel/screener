# Implementation Tasks: Extreme Session and Weekday Filters (Live & Backtest)

## Tasks

### Phase 1: Shared Filter Core (`strategy_extreme_fvg.py`)
- [ ] Task 1.1: Implement/centralize `is_in_ny_session(timestamp_ms)` and `is_weekday(timestamp_ms)` in `strategy_extreme_fvg.py`.
- [ ] Task 1.2: Add `session_filter` and `weekday_filter` parameters to `get_extreme_setup_for_symbol`.

### Phase 2: Live Trade Tracker Filtering (`extreme_trade_tracker.py`)
- [ ] Task 2.1: Add `session_filter_enabled`, `weekday_filter_enabled`, `entry_session_filter_enabled`, `entry_weekday_filter_enabled` properties to `ExtremeTradeTracker`.
- [ ] Task 2.2: Enforce formation filters when ingesting `PENDING_RETRACE` setups.
- [ ] Task 2.3: Enforce entry fill filters in `process_live_setups` when evaluating entry touch timestamp before transitioning to `TRADE_ACTIVE`.

### Phase 3: Application Server & Configuration (`main.py`, `live_screener_extreme.py`)
- [ ] Task 3.1: Read live filter environment variables in `main.py` and store in `state`.
- [ ] Task 3.2: Update `execute_extreme_screener_cycle()` to supply filter parameters.
- [ ] Task 3.3: Expose filter settings in `/api/extreme/config` GET and POST endpoints.
- [ ] Task 3.4: Add CLI flags in `live_screener_extreme.py`.

### Phase 4: Backtest Engine Integration (`backtest_extreme_fvg.py`)
- [ ] Task 4.1: Support all four filter parameters in `run_extreme_backtest()`, CLI arguments, and `ExtremeBacktestReport`.
- [ ] Task 4.2: Expose all four filter flags in `/api/extreme/backtest` API.

### Phase 5: Web UI & Dashboard Controls (`templates/index.html`)
- [ ] Task 5.1: Update Strategy 2 Backtest controls with FVG and Entry session/weekday dropdowns.
- [ ] Task 5.2: Update Live Scanner configuration badges / controls.

### Phase 6: Test Suite Verification
- [ ] Task 6.1: Add unit tests for `ExtremeTradeTracker` live session and entry fill filtering.
- [ ] Task 6.2: Add unit tests for `get_extreme_setup_for_symbol` formation filtering.
- [ ] Task 6.3: Run full test suite (`pytest -v`) to confirm 100% pass rate.
