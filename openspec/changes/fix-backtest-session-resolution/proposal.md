# Proposal: Fix Backtest Session Parameter Resolution

## Problem Statement
When running backtests via the Web Dashboard or API, users configuring different session combinations (such as FVG Session: London / Entry Session: NY vs. FVG Session: NY / Entry Session: London) receive the exact same backtest results.
Root cause analysis revealed:
1. In `main.py` (`api_extreme_backtest`), `session_filter` and `entry_session_filter` are query parameters that default to `None`. When omitted by the UI, `main.py` defaulted `sess_filter` and `entry_sess_filter` to `False` from environment variables (`EXTREME_SESSION_FILTER_ENABLED`).
2. In `SessionFilterConfig.from_legacy()`, `if session_filter is False:` evaluated before checking `sessions`. Consequently, when callers (like `run_extreme_backtest(sessions="LONDON")` or `api_extreme_backtest`) defaulted `session_filter` to `False`, `from_legacy()` forcibly overrode `resolved_fvg = "ALL"` and `resolved_entry = "ALL"`.
3. Both backtests silently ran with 24/7 (ALL) sessions and only weekday filtering enabled, producing identical results (34 trades, 159 filtered out).

## Proposed Solution
1. In `SessionFilterConfig.from_legacy()`, ensure that explicit session strings (`sessions`, `entry_sessions`) take precedence over legacy boolean flags. If `sessions` is provided and non-empty, use it. Only fall back to boolean flags (`session_filter is False` -> `"ALL"`, `session_filter is True` -> `default_session`) when `sessions` is `None` or empty.
2. In `main.py` (`api_extreme_backtest` and `api_extreme_scan`), when `session_filter` is not explicitly passed as a query param, determine enablement from `sessions`: if `sessions` is provided and not `"ALL"`, `session_filter` is `True`.

## Impact
- Custom and preset sessions selected in the UI (`LONDON`, `NY`, `ASIA`, `13:30-20:00`, etc.) correctly filter historical FVG formations and entry triggers during backtests.
- Backtest results will accurately reflect the chosen session constraints.
- 100% backward compatible with legacy boolean flags and all existing test suites.
