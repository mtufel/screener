## Why
Live daemon tracked trades log (extreme_trade_tracker.py) has no filtering or pagination — hard to find active/completed trades by symbol/state or browse large history.

## What Changes
- Add filter methods to ExtremeTradeTracker: by state, symbol, direction, session, date range
- Add pagination: page/per-page/offset for history
- Config-driven: env vars or config dict
- Keep persistence (data/extreme_live_trades.json)

## Non-Goals
- Changing trade tracking logic
- Removing existing session/weekday filters
- Changing persistence format
