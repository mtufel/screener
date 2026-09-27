# Tasks: Fix Backtest Session Parameter Resolution

- [x] 1. Update `SessionFilterConfig.from_legacy()` in `session_filter.py`
  - [x] 1.1 Give explicit `sessions` non-empty string precedence over `session_filter=False`.
  - [x] 1.2 Give explicit `entry_sessions` non-empty string precedence over `entry_session_filter=False`.
- [x] 2. Update `main.py` API Endpoints
  - [x] 2.1 In `api_extreme_backtest`, infer `sess_filter` and `entry_sess_filter` from `sessions` and `entry_sessions` when boolean query parameters are `None`.
  - [x] 2.2 In `api_extreme_scan`, infer `sess_filter` from `sessions_str` when `session_filter` is `None`.
- [x] 3. Automated Verification
  - [x] 3.1 Add unit tests verifying precedence when `session_filter=False` but `sessions` is provided.
  - [x] 3.2 Add integration test verifying that different backtest session parameters produce different filtering results.
  - [x] 3.3 Run full test suite (`pytest -v`) to confirm 100% pass rate.
