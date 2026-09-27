## Why

The dashboard hard-codes one section per strategy (`#strat2Wrapper`, `#strat3Wrapper`), so every new strategy needs hand-built UI, and the strategy dropdown the user asked for does not exist. Backtests run through separate, re-implemented engines (`backtest_extreme_fvg.py`, `backtest_liquidity_sweep_fvg.py`) that duplicate the live logic, so any backtest result can silently diverge from what the live screener would actually do — exactly the class of bug the user wants to surface: "backtest should also receive candle data, the only difference is the data feed; execution logic must be identical."

The strategy framework (landed in `strategy-extensibility`) already exposes `default_params` per strategy and strategy-parameterized API routes, so the UI can become config-driven. And the live pipeline (`get_extreme_setup_for_symbol` / `get_liquidity_sweep_setup_for_symbol` → `ExtremeTradeTracker.process_live_setups`) is already candle-replay-driven internally (it replays candles into the ledger for fills/exits), which means a historical data feed + a virtual clock can drive the *real* live execution path.

## What Changes

- **Unified strategy UI**: replace the per-strategy hard-coded wrappers with a single strategy dropdown that renders one panel; Live Screener and Backtest tabs are generated from each strategy's declared `default_params` (via `GET /api/strategies` + `GET /api/{strategy}/info`). All existing capabilities survive in the unified panel: 4H FVG map, live trade log, WS streaming, chart modal, daemon controls, variant pinning.
- **Replay backtest engine**: add a replay runtime that runs the *actual live screener cycle* against a historical candle feed at controllable speed — same `execute_extreme_screener_cycle` orchestration, same `find_setups` calls, same `ExtremeTradeTracker` state machine, same payload builders — with only the data provider swapped and wall-clock reads virtualized.
- **Virtual clock**: introduce a clock module (`clock.py`) with a process-wide injectable time source; wire the finite set of wall-clock call sites (engines, tracker, session filter) to it. Default behavior is unchanged (real time).
- **Replay provider**: `ReplayMarketDataProvider` implementing `BaseMarketDataProvider`, serving candles from a pre-fetched historical dataset strictly as-of the virtual clock (no lookahead), with a replay-head that advances candle-by-candle.
- **Watch mode**: replay runs as a managed background task with speed multiplier (1×/10×/100×/MAX), pause/resume/abort, progress snapshots over a WebSocket channel (`/ws/replay`) and a final aggregated report; the dashboard shows candles, setups, and trades appearing live during replay.
- **Active strategy endpoint**: `POST /api/{strategy}/activate` sets `state["extreme_active_strategy"]` so the dropdown selection drives the daemon.
- **Backtest route gains replay mode**: `GET /api/{strategy}/backtest?mode=replay` returns the replay report (same trade schema as today's reports, plus ledger metrics), keeping the existing default (vector engines) untouched.

## Capabilities

### New Capabilities
- `strategy-replay-backtest`: The replay runtime — virtual clock, replay data provider, replay orchestrator, replay API/WS, and the invariant that backtest and live share one execution path.

### Modified Capabilities
- `strategy-framework`: the dashboard is strategy-agnostic (dropdown + dynamically generated panels driven by `default_params`), and the active strategy is selectable at runtime via API.

## Impact

- **Code added**: `clock.py`, `replay_provider.py`, `replay_manager.py`, `api/replay.py`, add-only tests (`test_virtual_clock.py`, `test_replay_provider.py`, `test_replay_manager.py`, `test_unified_strategy_api.py`).
- **Code modified (thin)**: `strategy_extreme_fvg.py` (pass `current_time_ms` through 4 call sites), `strategy_liquidity_sweep_fvg.py` (clock passthrough), `liquidity.py` (clock default), `extreme_trade_tracker.py` (clock-read helper + injectable `now`), `session_filter.py` (clock default), `screener_cycle.py` (optional scope/params/provider/tracker args — defaults byte-identical), `api/extreme.py` (`/api/strategies` list, `activate` endpoint, replay backtest mode), `api/system.py` (serve unified dashboard), `templates/index.html` (unified dropdown panel + replay UI).
- **Not touched**: existing backtest engines (`backtest_extreme_fvg.py`, `backtest_liquidity_sweep_fvg.py`), existing tests (SOT), ledger persistence format.
- **Risk containment**: replay uses a dedicated in-memory `ExtremeTradeTracker` instance (never the production ledger) and isolated strategy-engine caches, so replay runs cannot pollute live state.
- **Workflow**: branch `feat/unified-strategy-ui-replay-backtest` off the current feature branch (unmerged Strategy-3 work the UI depends on), merged via PR, OpenSpec apply → archive.
