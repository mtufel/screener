## Why

Strategy 2 backtests currently simulate trades across all hours and all days, including low-volume Asian overnight sessions and weekends where crypto markets are quieter and FVG signals are noisier. Filtering to NY session (13:00–22:00 UTC) and weekdays only aligns with the strategy's design intent — it targets the high-volume NY/London overlap where LTF FVG follow-through is strongest. Adding config-driven filters lets us A/B test the same configuration with and without session constraints to quantify the impact.

## What Changes

- Add two config-driven boolean flags to `backtest_extreme_fvg.py`:
  - `EXTREME_SESSION_FILTER_ENABLED` (env) / `--session-filter` (CLI) — NY session 13:00–22:00 UTC
  - `EXTREME_WEEKDAY_FILTER_ENABLED` (env) / `--weekday-filter` (CLI) — Monday–Friday only
- A trade setup is only counted if the LTF FVG c3 close timestamp falls within the allowed session/weekday window
- Propagate flags through `run_extreme_backtest` function signature and `print_backtest_report`
- Filter out trades formed outside session/weekday window from report totals and trade list

## Capabilities

### New Capabilities
- `session-weekday-filter`: NY session (13:00–22:00 UTC) and weekday-only filtering for Strategy 2 backtest engine, config-driven via env vars and CLI args

### Modified Capabilities
- `strategy-2-extreme`: Backtest execution now accepts session/weekday filter parameters and reports filtered trade counts

## Impact

- `backtest_extreme_fvg.py`: `run_extreme_backtest` signature, `main()` CLI args, `print_backtest_report`
- `ExtremeBacktestReport`: new fields for filter config (so report shows active filters)
- No changes to live strategy engine — filters are backtest-only for now
