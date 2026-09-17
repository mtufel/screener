# Tasks: Consolidate Session Logic into Unified SessionFilterConfig

- [x] 1. Core Model Implementation
  - [x] 1.1 Implement `SessionFilterConfig` in `session_filter.py` with presets, custom interval parsing, `is_fvg_valid()`, `is_entry_valid()`, and `from_legacy()`.
  - [x] 1.2 Export `is_in_ny_session(ts_ms)` and `is_weekday(ts_ms)` for backward compatibility.
  - [x] 1.3 Create unit tests in `test_session_filter.py` covering all invariants of `SessionFilterConfig`.
- [x] 2. Strategy & Tracker Layer Consolidation
  - [x] 2.1 Update `strategy_extreme_fvg.py` to accept and use `SessionFilterConfig`.
  - [x] 2.2 Update `extreme_trade_tracker.py` to replace scattered checks with `session_config.is_fvg_valid()` and `session_config.is_entry_valid()`.
- [x] 3. Backtester & Screener Layer Consolidation
  - [x] 3.1 Update `backtest_extreme_fvg.py` to accept and use `SessionFilterConfig`.
  - [x] 3.2 Update `live_screener_extreme.py` to accept and use `SessionFilterConfig`.
- [x] 4. REST API & Web Dashboard Integration
  - [x] 4.1 Update `main.py` state, endpoints (`/api/extreme/scan`, `/api/extreme/backtest`, `/api/extreme/config`, `/api/extreme/status`), and cycle runner.
  - [x] 4.2 Update `templates/index.html` dropdowns and JS handlers.
- [x] 5. Automated Verification
  - [x] 5.1 Run full test suite (`pytest -v`) to ensure 100% pass rate across all existing and new tests.
