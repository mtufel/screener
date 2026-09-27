# Change: Remove Strategy 1 and refactor around Strategy 2

## Why
Strategy 1 (2-stage standard FVG) has been disabled in production (`ENABLE_STRATEGY_1=false`) and is unused. Keeping a second engine, tracker, backtester, dashboard tab, and API surface doubles maintenance cost and hides the live Extreme LTF path. The user directed that Strategy 1 is dead code and should be removed, then the remaining Strategy 2 core should be refactored for maintainability without behavior change.

## What Changes
- Delete Strategy 1 production modules: `strategy.py`, `trade_tracker.py`, `backtest.py`.
- Remove Strategy 1 FastAPI routes, daemon, config flags, and dashboard tab.
- Move shared helpers still required by Strategy 2 (`get_last_n_candles`) into `strategy_extreme_fvg.py`.
- Drop Strategy 1 Telegram formatters; keep `send_telegram_alert` / `send_telegram_photo`.
- Remove Strategy 1 tests (`test_screener.py`, `test_strategy_spec.py`) and S1-only cases in shared tests.
- Extract helpers from oversized Strategy 2 routines (`process_live_setups`) without changing public APIs.

## Impact
- Affected specs: `strategy-1-standard` (retired), `strategy-2-extreme` (unchanged behavior).
- Callers: `main.py`, dashboard, Telegram, charts, README/CLAUDE.
- Strategy 2 live scan, ledger, backtest, and extreme APIs stay the same.
