# Design: Extreme Session and Weekday Filters (Live Scanner & Backtest)

## Context
See `proposal.md` for motivation. Both backtests and live execution pipelines require identical filtering semantics to ensure live forward-testing matches backtested expectations.

## Goals / Non-Goals

**Goals:**
- Config-driven independent filtering for:
  1. FVG formation timestamp (`session_filter`, `weekday_filter`)
  2. Entry fill timestamp (`entry_session_filter`, `entry_weekday_filter`)
- Unified helper functions shared across live scanner and backtester.
- Live scanner gating:
  - If formation filter fails, do not emit `PENDING_RETRACE` or send `NEW_SETUP` alert.
  - If entry fill filter fails, do not transition to `TRADE_ACTIVE` or send `ENTRY_FILLED` alert.
- Backtester gating:
  - Exclude filtered setups/trades and tally `trades_filtered_out`.
- Full configuration access via `.env`, CLI flags, FastAPI query/body params, and Web UI.

**Non-Goals:**
- Custom timezones other than UTC for session boundaries (NY session is standardized as 13:00–22:00 UTC).
- Dynamic session definitions beyond NY hours.

## Architectural Decisions

### 1. Unified Time Helpers
Place `is_in_ny_session(timestamp_ms: int) -> bool` and `is_weekday(timestamp_ms: int) -> bool` in `strategy_extreme_fvg.py` so they are accessible by `backtest_extreme_fvg.py`, `extreme_trade_tracker.py`, and `main.py`.

- `is_in_ny_session(ts)`: `datetime.fromtimestamp(ts / 1000, tz=timezone.utc).hour` in `range(13, 22)`.
- `is_weekday(ts)`: `datetime.fromtimestamp(ts / 1000, tz=timezone.utc).weekday() < 5`.

### 2. Live Scanner & Ledger Gating in `extreme_trade_tracker.py`
- When ingesting a newly discovered setup into `PENDING_RETRACE`:
  - Check `session_filter` and `weekday_filter` against `setup.ltf_fvg.close_timestamp` (or `formed_at + dur_ms`).
  - If invalid, skip ingestion.
- When evaluating a pending setup for fill against incoming candle stream (`low <= entry` for Bullish / `high >= entry` for Bearish):
  - Check `entry_session_filter` and `entry_weekday_filter` against the fill candle's `timestamp`.
  - If invalid, do not activate the trade (remain pending or expire/invalidate based on config).

### 3. Backtester Gating in `backtest_extreme_fvg.py`
- Check FVG formation at candidate selection: `best_ltf.close_timestamp`.
- Check Entry fill at trigger candle: `candles_ltf[k].timestamp`.
- Tally `trades_filtered_out` and record all 4 filter flags in `ExtremeBacktestReport`.

### 4. Application Configuration in `main.py`
- Read env vars:
  - `EXTREME_SESSION_FILTER_ENABLED`
  - `EXTREME_WEEKDAY_FILTER_ENABLED`
  - `EXTREME_ENTRY_SESSION_FILTER_ENABLED`
  - `EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED`
- Expose in `state` dictionary and `/api/extreme/config` endpoints.
- Pass runtime values to `execute_extreme_screener_cycle()`.