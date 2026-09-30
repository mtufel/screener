## Context

The user wants: (1) a strategy dropdown in the UI that selects the strategy to run; (2) each strategy opens its own live screener and backtest; (3) backtest parameters fully config-driven so different combinations can be tested; (4) backtest must use the live screener — "only the data feed should change, not the execution logic… backtest should also receive candle data; the only difference is that backtest can control the data speed. That's the right way to find the bugs and see real performance." User decisions: unified dynamic panel (controls generated from `default_params`), watch-mode replay with speed control and WS progress, branch off the current feature branch.

Current architecture facts (verified by reading the code):

- **Strategy framework is in place** (`strategies/base.py`, `strategies/registry.py`): `BaseStrategy.default_params` + `resolve_params()`, name-keyed registry, `/api/{strategy}/scan|backtest|status|info` routes, `state["extreme_active_strategy"]`. Two registered strategies: `extreme_fvg`, `liquidity_sweep_fvg`.
- **The live pipeline is already candle-replay-driven.** `ExtremeTradeTracker.process_live_setups()` ingests setups and then *replays recent closed candles* through `_monitor_pending_trade` / `_monitor_active_trade` to fill entries and resolve TP/SL. Fills/exits come from candle extremes, not from wall-clock timers. The two `datetime.now()` calls in `process_live_setups` only stamp display strings / absent-expiry bookkeeping.
- **Engines are mostly time-injectable**: `filter_closed_candles(..., current_time_ms=...)`, `find_unmitigated_ltf_fvgs(..., current_time_ms=...)`, `update_fvg_lifecycle_state`-style checks already accept `current_time_ms`. Remaining hard `time.time()` sites: `strategy_extreme_fvg.py` lines ~119/321/1018 (default to now when param is None), ~667/686 (touch ts "live" fallback), ~969/975 (live-mid fill ts), plus `liquidity.py` (`now_ms` default) and `session_filter.py` (now default).
- **The module-level 4H cache is the lookahead hazard**: `htf_fvg_cache = HTFFVGCache()` is a process global keyed by `symbol:mode`. Its incremental `update_delta` assumes monotonically advancing time. A naive replay would feed it future 4H candles (lookahead) and corrupt live state. However, `get_active_4h_fvgs_for_symbol(..., force_bootstrap=True)` re-derives FVGs strictly from provided candles, and `HTFFVGCache.invalidate_cache(symbol)` clears keys — both are enough to isolate replay without touching the class internals.
- **The tracker is a singleton** (`extreme_trade_tracker`) persisted to `data/extreme_live_trades.json` + Redis. `ExtremeTradeTracker.__init__(storage_path=...)` allows a second, in-memory instance.
- **`execute_extreme_screener_cycle()`** hard-wires: config from `_runtime_extreme_config()` (state), provider from `state["data_provider"]`, the global tracker, and WS broadcast of `state` counters. It resolves the active strategy from config and calls `strategy.find_setups(sym, provider, params)` — the seam the replay needs.
- **PATCH-SURFACE CONTRACT**: `screener_cycle` services are read via `_svc()` (the `main` facade) so tests can patch. New code must not break bare-name reads.
- Existing tests are SOT (CLAUDE.md) and must pass unchanged; all changes additive or default-identical.

## Goals / Non-Goals

**Goals:**
- One execution path: replay backtest = live screener cycle + historical provider + virtual clock + controllable speed.
- Strategy dropdown drives the whole dashboard; panels (live controls, backtest controls) are generated from each strategy's `default_params` so future strategies need zero UI work.
- Watch mode: replay streams progress (virtual time, candle head, events, ledger) over WS with pause/resume/speed/abort.
- Replay isolation: replay can never corrupt the live ledger, Redis, or the live HTF cache semantics.
- Existing behavior byte-identical when not replaying; all existing tests pass untouched.

**Non-Goals:**
- Do not rewrite or remove the existing vector backtest engines (`backtest_extreme_fvg.py`, `backtest_liquidity_sweep_fvg.py`) — they remain the fast "analytic" mode and the default for `/api/{strategy}/backtest`.
- Do not build per-candle chart rendering in the replay stream (the existing chart modal remains available on demand; a replay chart view is future work).
- Do not implement freqtrade-style dataframe vectorization.
- Do not add multi-symbol parallel replay in v1 (the cycle already loops symbols; replay uses the configured whitelist as-is).

## Decisions

### D1. Virtual clock module (`clock.py`) — process-wide injectable time
```python
class VirtualClock:                      # advance-based, monotonic within a run
    def now_ms(self) -> int
    def advance_to(self, ts_ms: int)     # never backwards
class Clock:                             # module facade
    @staticmethod def now_ms() -> int    # returns virtual clock if installed else real time
    @staticmethod def now() -> datetime  # UTC datetime convenience
def install_virtual_clock(clock: Optional[VirtualClock]) -> contextmanager
```
`clock.now_ms()` reads an explicit installed `VirtualClock` (contextvar for async safety) or falls back to `time.time() * 1000`. All wiring is *mechanical*: replace `int(time.time()*1000)` defaults with `clock.now_ms()` at the enumerated call sites (engines, tracker `process_live_setups`, `session_filter`). No signature changes where a `current_time_ms`/`now_ms` param already exists — those defaults simply call the clock. Real-time behavior is unchanged when no virtual clock is installed (existing tests never install one).
Rationale: the replay must freeze "now" so closed-candle filtering, session filters, absent-expiry, and display stamps agree with the simulated time. **Alternative considered**: passing `now_ms` through every function signature — rejected (would touch dozens of call sites incl. third-party-ish internals); a contextvar keeps call sites unchanged.

### D2. `ReplayMarketDataProvider` (`replay_provider.py`)
Implements `BaseMarketDataProvider` over a pre-fetched dataset: `{symbol: {"ltf": [Candle...], "4h": [Candle...]}}` fetched once up front via the real provider's `get_historical_candles_range` (warmup included: `days + 20d` of 4H, `days` of LTF).
- `get_last_n_candles(symbol, timeframe, n)` returns the last `n` candles **strictly closed as-of the virtual clock** (`c.timestamp + dur <= clock.now_ms()`), newest-last.
- `get_all_mids()` synthesizes mids from the LTF head candle's close (the same number the engines compute as `current_price`), so scan payloads render.
- `resolve_symbol` is identity; `get_historical_candles_range` filters the in-memory set (chart modal keeps working during replay); `get_universe_coins` returns the replay symbols.
- Data-provenance guard: dataset timestamp spans are verified contiguous on construction; gaps are logged and left as-is (the cycle tolerates missing candles the same way it tolerates provider outages live).
Rationale: the provider is the *only* thing that changes between live and replay — the user's exact requirement. **Alternative**: a proxy that records/replays raw HTTP — rejected as fragile and unnecessary.

### D3. Replay isolation of the HTF cache and engine state
During a replay cycle, for each symbol the replay runner calls `get_active_4h_fvgs_for_symbol(..., force_bootstrap=True)` semantics by pre-clearing and bootstrapping from the replay provider's candles:
- The replay runner wraps each cycle with `htf_fvg_cache.invalidate_cache(sym)` before the symbol's scan (replay is the only caller doing this), then the normal code path bootstraps from replay candles *strictly as-of virtual now*. Because the provider never serves future candles, no lookahead can enter the cache.
- Replay **does not** call `load_from_redis`/`save_to_redis`: `get_active_4h_fvgs_for_symbol` only persists via a scheduled task; during replay the clock context is active and Redis save is skipped via an explicit `htf_fvg_cache` *persist-pause flag* (new attribute, default False — zero effect on live).
Rationale: incremental `update_delta` requires monotonic time; bootstrap-per-cycle (per symbol, O(200 4H candles) — cheap) is the minimal-risk correctness-preserving choice. **Alternative**: per-replay cache instances injected through the call chain — rejected (touches many signatures for no behavioral gain).

### D4. Parameterize the cycle (thin, default-identical)
`execute_extreme_screener_cycle(*, cfg_override=None, provider_override=None, tracker_override=None, broadcast=True, cycle_label="")`:
- Defaults `None` → exact current behavior (state config, `main.get_market_data_provider(...)`, global tracker, broadcast, state counters).
- Replay passes: its own cfg dict (active strategy name + resolved params), `ReplayMarketDataProvider`, an in-memory `ExtremeTradeTracker(storage_path=<temp nonexistent>)` (never `_save_local`'d to the live path — replay tracker sets `storage_path` to a temp file under `data/replay/`), and `broadcast=False` (replay has its own WS events).
- The PATCH-SURFACE is preserved: service reads stay `main.<name>`; only the injection points are added.
Rationale: one orchestration loop for live and replay — the core "same execution logic" requirement. **Alternative**: copy the cycle into a replay module — rejected (instant divergence, the exact bug class being eliminated).

### D5. `ReplayManager` (`replay_manager.py`) — watch-mode lifecycle
```python
class ReplayManager:
    async def start(strategy, params, symbols, days, speed, seed_ts=None) -> replay_id
    async def pause(replay_id) / resume(replay_id) / abort(replay_id)
    def snapshot(replay_id) -> dict           # status, virtual time, head candle, ledger summary, events tail
    def report(replay_id) -> dict             # final aggregated report (trade list + metrics)
```
Run model per replay:
1. Fetch dataset (warmup) via the real provider.
2. Install virtual clock; set it to dataset start (post-warmup).
3. Loop: advance clock to the next closed LTF candle boundary (batch: `speed` candles per real-time tick at 1× = 1 candle per LTF period; `speed>=100` or MAX = no real-time sleep, process as fast as possible with `await asyncio.sleep(0)` yields); call `execute_extreme_screener_cycle(...)` with replay injections; collect events into the replay tracker's ledger; emit `replay_progress` WS frames.
4. End: emit `replay_complete` with the report (per-strategy ledger metrics: trades, WR, net R, PF, max DD, avg MFE, duration).
Speed semantics: `speed` = virtual-minutes per real-second (1× = realtime, 10× = 10 virtual min/s, MAX = unlimited). Pause = stop advancing the clock (task sleeps, resumable); abort = cancel task, mark `ABORTED`, keep partial report.
Rationale: candle-boundary stepping matches exactly what the live daemon sees between scans; multiple cycles can occur per boundary at high speed by stepping the clock by the daemon interval instead. **Alternative**: real-time nap per cycle — rejected (90 days would take hours).

### D6. Replay API + WS (`api/replay.py`, reuse `dashboard_ws` manager patterns)
- `POST /api/replay/start` (strategy, params JSON, symbols, days, speed) → `{replay_id}`
- `POST /api/replay/{id}/pause|resume|abort`
- `GET /api/replay/{id}` → snapshot; `GET /api/replay/{id}/report` → report
- `WS /ws/replay` → frames `replay_progress` / `replay_complete` / `replay_error` (mirrors `/ws/extreme-live` conventions)
- Single active replay per process (v1): starting a new one aborts the old. Documented limitation.
Rationale: the existing dashboard already consumes WS events with the same shape conventions; a separate channel avoids disturbing the live feed.

### D7. Backtest route gains replay mode
`GET /api/{strategy}/backtest?mode=replay&speed=...&params...` runs a fire-and-collect replay (MAX speed, no WS requirement) and returns the report in the same envelope as today plus `engine: "replay"` and ledger metrics. Default (`mode=analytic`) unchanged → existing tests/UI unaffected. The unified UI's Backtest tab exposes an "Execution engine" toggle: `Replay (live-identical)` vs `Analytic (fast)`.
Rationale: the user's "backtest must use the live screener" becomes the default *option in the UI*, while the fast analytic engines remain available for sweeps.

### D8. Unified UI (`templates/index.html`)
- Header: strategy dropdown (populated from `GET /api/strategies` → name + display_name). Selection sets a global `currentStrategy`, re-renders the single unified panel, and calls `POST /api/{strategy}/activate`.
- Unified panel tabs: **Live Screener** (daemon status/interval/whitelist/Scan Now, 4H FVG map, live trade log with strategy filter — all existing widgets, now strategy-aware) and **Backtest** (controls auto-generated from `default_params`: each param key renders by value type — bool→checkbox, enum-ish string→select, number→input, timeframe/session keys→preset selects; plus symbol/days/speed/engine toggle). Run button posts to `/api/{strategy}/backtest` with the full param set (config-driven); replay runs stream into a progress view (virtual clock readout, candle head, event feed, live ledger table) and finish with the report table + "Pin run" variant comparison (S3's existing variant UX generalized).
- The legacy `#strat2Wrapper`/`#strat3Wrapper` markup and their one-off functions are removed and replaced by the unified renderers; shared helpers (`formatNumber`, `fetchWithRetry`, `showToast`, chart modal, WS handling) are kept.
Rationale: config-driven generation is what makes this "all features from UI" for *every* future strategy. **Alternative**: keep hard-coded sections and hide/show — rejected (user chose unified panel).

### D9. Active strategy endpoint
`POST /api/{strategy}/activate` sets `state["extreme_active_strategy"]` (validates via registry) and returns status. `GET /api/strategies` returns `[{name, display_name, is_active}]` from the registry + state (the dropdown source).
Rationale: the dropdown must actually drive the daemon, not just the panel.

## Risks / Trade-offs

- [Replay divergence from live] → Mitigated by construction: same cycle function, same engines, same tracker; only provider/clock injected. The add-only tests pin: (a) replay provider as-of filtering (no future candles), (b) a tiny fixture replay producing identical setups to a direct `find_setups` call with the same candles+`current_time_ms`, (c) tracker isolation (live ledger untouched).
- [Wall-clock wiring changes live behavior] → `clock.now_ms()` returns real time when nothing is installed; every wiring is a default-value swap. Existing tests (which never install a virtual clock) must pass unchanged — acceptance gate.
- [HTF cache cross-contamination live↔replay] → replay clears per symbol + bootstraps as-of virtual now + pauses Redis persistence (flag default off). Live daemon behavior untouched.
- [Replay performance (per-cycle bootstrap)] → O(200 4H + 300 LTF candles) per symbol per boundary; fine for BTC/ETH/SOL × N boundaries. MAX speed handles 90d in seconds-to-minutes. If slow, batch boundaries per cycle (already the design: one cycle per LTF close, not per 5m tick).
- [Single active replay] → acceptable v1; enforced with a clear 409 + message.
- [UI regression risk from replacing wrappers] → All existing widget IDs used by WS handlers (`extremeSetupsContainer`, history table, daemon indicators, FVG map) are preserved in the unified panel; the S2/S3 inner-tab functions are replaced by the unified tab switcher. Verified by loading the dashboard and by the add-only API tests.

## Migration Plan

1. Land `clock.py` + mechanical wall-clock wiring (default-identical) → run full pytest (SOT gate).
2. Land `replay_provider.py` + replay isolation tests (no cycle changes yet).
3. Parameterize `execute_extreme_screener_cycle` (default-identical) + `ReplayManager` + `/api/replay/*` + `POST /api/{strategy}/activate` + `GET /api/strategies` + backtest `mode=replay`.
4. Rewrite `templates/index.html` to the unified dropdown panel + replay watch UI.
5. Add-only tests; full pytest; manual dashboard smoke (live scan, replay 14d MAX speed, pause/resume, report).
6. Merge via PR; OpenSpec apply → archive. **Rollback**: revert branch; no persisted-format changes, no live-behavior changes to roll back.

## Open Questions

- Whether the replay report should also expose per-boundary cycle timings (useful for perf tuning of the live daemon) — deferred; the snapshot keeps cycle counts, timings can be added without spec change.
- Whether watch mode should render an incremental candlestick canvas — deferred (chart modal on demand is v1).
