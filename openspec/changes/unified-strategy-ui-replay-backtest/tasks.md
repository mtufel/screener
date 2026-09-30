## 1. Virtual clock foundation (default-identical)

- [x] 1.1 Create `clock.py` with `VirtualClock` (advance-based, monotonic), `Clock.now_ms()/now()` facade reading an installed clock (contextvar) else real time, and `install_virtual_clock()` context manager
- [x] 1.2 Wire `strategy_extreme_fvg.py` wall-clock defaults (~119/321/667/686/969/975/1018) to `clock.now_ms()`
- [x] 1.3 Wire `liquidity.py` and `session_filter.py` now-defaults to `clock.now_ms()`
- [x] 1.4 Wire `extreme_trade_tracker.process_live_setups` now-stamps to `clock.now_ms()` via a private helper
- [x] 1.5 Add `test_virtual_clock.py`: default real time, install/advance, explicit param precedence, contextvar isolation across async tasks
- [x] 1.6 Run full `pytest -v` — SOT must pass unchanged

## 2. Replay data provider

- [x] 2.1 Create `replay_provider.py`: `build_replay_dataset()` (fetch via real provider, warmup margin), `ReplayMarketDataProvider(BaseMarketDataProvider)` with as-of `get_last_n_candles`, mids synthesis, `get_historical_candles_range` filter, identity `resolve_symbol`
- [x] 2.2 Add `test_replay_provider.py`: no-lookahead as-of filter, mids = replay head close, range fetch + chart path works mid-replay, warmup inclusion
- [x] 2.3 Run full `pytest -v`

## 3. Replay orchestration

- [x] 3.1 Parameterize `execute_extreme_screener_cycle(*, cfg_override, provider_override, tracker_override, broadcast=True, persist_results=True, cycle_label="")` with defaults `None` → byte-identical live behavior; PATCH-SURFACE reads preserved
- [x] 3.2 Add `data/replay/` temp-ledger support: replay tracker instance with isolated storage path and paused Redis persistence
- [x] 3.3 Add `htf_fvg_cache` replay guards: per-symbol invalidate + persist-pause flag (default False)
- [x] 3.4 Create `replay_manager.py`: `ReplayManager.start/pause/resume/abort/snapshot/report` with virtual-clock stepping at candle boundaries, speed semantics (virtual-min/sec + MAX), isolated tracker + provider + cfg injection, per-cycle WS frames
- [x] 3.5 Add `api/replay.py`: `POST /api/replay/start`, `POST /api/replay/{id}/pause|resume|abort`, `GET /api/replay/{id}`, `GET /api/replay/{id}/report`, `WS /ws/replay`
- [x] 3.6 Add `POST /api/{strategy}/activate` + `GET /api/strategies` in `api/extreme.py`
- [x] 3.7 Add `mode=replay` support to `GET /api/{strategy}/backtest` (MAX-speed run, same envelope + `engine: "replay"` + effective params)
- [x] 3.8 Add `test_replay_manager.py`: fixture replay runs to completion, ledger isolation, pause/resume semantics, abort report, no-lookahead invariant via fake dataset
- [x] 3.9 Add `test_unified_strategy_api.py`: `/api/strategies` shape, activate valid/invalid, replay start/status/report flow via TestClient
- [x] 3.10 Run full `pytest -v`

## 4. Unified dashboard UI

- [x] 4.1 Replace `#strat2Wrapper`/`#strat3Wrapper` markup with unified panel: header strategy dropdown, Live/Backtest tabs, all existing widgets preserved (setups grid, FVG map, live trade log + filters, daemon controls, chart modal, toasts)
- [x] 4.2 Generate backtest controls from `GET /api/{strategy}/info` `default_params` (type-aware rendering: bool→checkbox, number→input, session→preset select, timeframe→select) + symbol/days/speed/engine inputs
- [x] 4.3 Wire live tab to `/api/{strategy}/scan|status` and daemon endpoints (strategy-aware Scan Now, WS reuse)
- [x] 4.4 Backtest submit: analytic mode → existing envelope render; replay mode → progress view (virtual clock, head, event feed, live ledger) consuming `/ws/replay`, then report render with Pin/variant comparison
- [x] 4.5 Remove dead S2/S3-only JS; keep shared helpers; smoke-test dashboard endpoints
- [ ] 4.6 Manual smoke: live scan for both strategies; 14-day replay at MAX with pause/resume; report + variant pin

## 5. Validation & wrap-up

- [x] 5.1 Full `pytest -v` green (SOT untouched)
- [x] 5.2 Typecheck/lint pass (python -m compileall; no new lint suppressions)
- [x] 5.3 Update `project.md`/`README` (replay backtest, unified UI, new endpoints)
- [ ] 5.4 `openspec validate unified-strategy-ui-replay-backtest` (or manual checklist if CLI unavailable)
