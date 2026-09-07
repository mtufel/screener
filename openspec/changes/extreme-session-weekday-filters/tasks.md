## Implementation Tasks

### Task 1: Add Filter Helper Functions to backtest_extreme_fvg.py
- **What**: Implement `is_in_ny_session(timestamp_ms)` and `is_weekday(timestamp_ms)` helper functions
- **Where**: New functions in `backtest_extreme_fvg.py` after imports
- **Details**: 
  - `is_in_ny_session`: Check UTC hour is 13-21 (13:00 inclusive, 22:00 exclusive)
  - `is_weekday`: Check datetime.weekday() is 0-4 (Mon-Fri)
  - Both accept millisecond timestamps from FVG c3 close
  - Returns boolean; False for any parse failure

### Task 2: Modify run_extreme_backtest Signature and Logic
- **What**: Add `session_filter: bool = False` and `weekday_filter: bool = False` parameters
- **Where**: `run_extreme_backtest` function definition (line 312)
- **Details**:
  - Add parameters after `min_gap_pct`
  - Apply filters after `find_unmitigated_ltf_fvgs` returns candidates
  - Before trade simulation, check `is_in_ny_session` and `is_weekday` on `ltf_fvg.close_timestamp`
  - If filter enabled and timestamp fails check, skip the trade setup (continue to next iteration)
  - Track filtered_count separately for reporting

### Task 3: Update ExtremeBacktestReport with Filter Fields
- **What**: Add filter status fields to `ExtremeBacktestReport` dataclass
- **Where**: `ExtremeBacktestReport` dataclass (line 159)
- **Details**:
  - Add `session_filter_enabled: bool = False`
  - Add `weekday_filter_enabled: bool = False`
  - Add `trades_filtered_out: int = 0` for trades rejected by filters
  - Update `to_dict()` to include filter fields

### Task 4: Update print_backtest_report to Show Filter State
- **What**: Display active filters in report output
- **Where**: `print_backtest_report` function
- **Details**:
  - Print "Session Filter: NY (13:00-22:00 UTC) [ENABLED]" or "[DISABLED]"
  - Print "Weekday Filter: Mon-Fri [ENABLED]" or "[DISABLED]"
  - Print "Trades filtered out: X" if any filters enabled

### Task 5: Update main() CLI and Env Var Propagation
- **What**: Add CLI arguments and env var reading in `main()`
- **Where**: `main()` function (line 578)
- **Details**:
  - Read env vars: `EXTREME_SESSION_FILTER_ENABLED`, `EXTREME_WEEKDAY_FILTER_ENABLED`
  - Add CLI args: `--session-filter` (action='store_true'), `--weekday-filter` (action='store_true')
  - CLI args override env vars (if passed, True; else use env; else default False)
  - Pass filter booleans to `run_extreme_backtest` calls

### Task 6: Run BTC Backtest with Filters
- **What**: Execute backtest to validate filter behavior
- **Details**:
  - Run BTC backtest without filters (baseline)
  - Run BTC backtest with session filter only
  - Run BTC backtest with weekday filter only
  - Run BTC backtest with both filters enabled
  - Compare trade counts and metrics across runs
  - Verify trades formed outside session/weekday are excluded

### Task 7: Update .env and Validate Deployment
- **What**: Set filter env vars in `.env` if needed
- **Details**:
  - Add `EXTREME_SESSION_FILTER_ENABLED=false` (default, disabled)
  - Add `EXTREME_WEEKDAY_FILTER_ENABLED=false` (default, disabled)
  - No changes needed if filters remain opt-in
  - Push to Render only if env vars need updating
