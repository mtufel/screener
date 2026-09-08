# Proposal: Extreme Session and Weekday Filters (Live Scanner & Backtest)

## Why

Strategy 2 (Extreme LTF FVG) setups and entry fills can occur 24/7 across crypto markets, including low-volume Asian overnight sessions and weekend periods where signals frequently experience lower follow-through and higher false-breakout noise.
Providing independent, config-driven filters for:
1. **FVG Formation Window** (when the extreme LTF FVG completes formation)
2. **Entry Fill Window** (when price retraces and fills the limit entry)

across **both the Live Scanner and the Backtesting Engine** aligns real-time execution with historical backtesting, allowing systematic session-based gating (e.g. NY Session 13:00–22:00 UTC and Weekdays Monday–Friday UTC).

## What Changes

- **Central Filter Helpers**:
  - `is_in_ny_session(timestamp_ms)`: Checks whether timestamp is within 13:00–22:00 UTC (13:00 inclusive, 22:00 exclusive).
  - `is_weekday(timestamp_ms)`: Checks whether timestamp is Monday–Friday UTC (weekday 0–4).

- **Four Independent Configuration Flags** (supported via Env Vars, CLI Args, API Query Params, and Web UI):
  - `EXTREME_SESSION_FILTER_ENABLED` / `--session-filter` / `session_filter`: FVG formation NY session filter.
  - `EXTREME_WEEKDAY_FILTER_ENABLED` / `--weekday-filter` / `weekday_filter`: FVG formation weekday filter.
  - `EXTREME_ENTRY_SESSION_FILTER_ENABLED` / `--entry-session-filter` / `entry_session_filter`: Entry fill NY session filter.
  - `EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED` / `--entry-weekday-filter` / `entry_weekday_filter`: Entry fill weekday filter.

- **Backtest Engine Integration (`backtest_extreme_fvg.py`)**:
  - Filters evaluated at both FVG candidate selection and entry trigger.
  - Reports count of `trades_filtered_out` and active filter flags.

- **Live Scanner & Daemon Integration (`strategy_extreme_fvg.py`, `extreme_trade_tracker.py`, `main.py`, `live_screener_extreme.py`)**:
  - `get_extreme_setup_for_symbol`: Filters candidates based on formation session/weekday.
  - `ExtremeTradeTracker`: Evaluates entry fill timestamps before transitioning pending setups to `TRADE_ACTIVE` and firing `ENTRY_FILLED` Telegram alerts.
  - Server endpoints (`/api/extreme/scan`, `/api/extreme/config`, `/api/extreme/backtest`) and Web UI updated.

## Capabilities

### New Capabilities
- `session-weekday-filter`: Config-driven NY session (13:00–22:00 UTC) and weekday (Mon–Fri UTC) filtering evaluated independently for FVG formation and Entry fill across both backtesting and live real-time scanning.

### Modified Capabilities
- `strategy-2-extreme`: Extreme Strategy 2 engine and trade lifecycle tracker accept formation and entry session/weekday filter configurations.

## Impact
- `strategy_extreme_fvg.py`: Centralize helpers and support formation filters.
- `extreme_trade_tracker.py`: Support entry fill filters and formation validation before pending/active transition.
- `backtest_extreme_fvg.py`: Support all 4 filter flags and report metrics.
- `main.py`: Update live screener background cycle, REST endpoints, and env var defaults.
- `live_screener_extreme.py`: Support CLI flags for live daemon.
- `templates/index.html`: Support controls/badges for both live and backtest filters.
