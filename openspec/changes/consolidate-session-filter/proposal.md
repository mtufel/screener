# Proposal: Consolidate Session and Weekday Filtering into Unified SessionFilterConfig

## Problem Statement
Currently, session and weekday filtering logic is fragmented across multiple modules (`strategy_extreme_fvg.py`, `extreme_trade_tracker.py`, `backtest_extreme_fvg.py`, `live_screener_extreme.py`, and `main.py`).
Every function accepts up to 6 separate primitive parameters:
- `session_filter: bool`
- `weekday_filter: bool`
- `entry_session_filter: bool`
- `entry_weekday_filter: bool`
- `sessions: str`
- `entry_sessions: str`

This causes parameter bloat, repetitive boilerplate checks `if (use_entry_sess_filter and not is_in_ny_session(...)) or ...` duplicated across 5 different files, and confusion over having both an `enabled: bool` toggle AND a session string selector.

## Proposed Solution
1. Introduce a single, cohesive domain model `SessionFilterConfig` in `session_filter.py`.
2. Encapsulate validation methods `is_fvg_valid(ts_ms)` and `is_entry_valid(ts_ms)` within `SessionFilterConfig`.
3. Eliminate the need for redundant `enabled: bool` flags by treating `sessions = "ALL"` as unconstrained/disabled.
4. Support legacy callers and test suites seamlessly via `SessionFilterConfig.from_legacy(...)` and backward-compatible helper exports (`is_in_ny_session`, `is_weekday`).
5. Update `strategy_extreme_fvg.py`, `extreme_trade_tracker.py`, `backtest_extreme_fvg.py`, `live_screener_extreme.py`, `main.py`, and `templates/index.html` to use `SessionFilterConfig`.

## Impact
- **Simplicity:** 6 loose primitive arguments collapsed into one well-typed config object.
- **Maintainability:** All session parsing, weekday checks, presets, and validation logic live in `session_filter.py`.
- **Zero SOT Regressions:** 100% backward compatible with existing test cases.
