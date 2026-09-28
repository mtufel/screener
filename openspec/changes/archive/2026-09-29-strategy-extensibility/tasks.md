## 1. Branch & Framework Scaffolding

- [x] 1.1 Create branch `feat/strategy-extensibility` off `develop` and verify `git branch --show-current` reports it
- [x] 1.2 Create `strategies/` package with `strategies/__init__.py` and verify the package imports (`python3 -c "import strategies"`)
- [x] 1.3 Implement `strategies/base.py` with `BaseStrategy` (ABC: `name`, `display_name`, `interface_version`, `default_params`, `resolve_params()`, abstract `find_setups()`, abstract `backtest()`) and verify a minimal concrete subclass can import and call `resolve_params()` with precedence runtime > default
- [x] 1.4 Implement `strategies/registry.py` with `register` decorator, `get_strategy(name)` (raises with available-list on unknown), and `list_strategy_names()`; verify a registered fake strategy is resolvable and an unknown name raises

## 2. Strategy 2 Adapter & Config Reconciliation

- [x] 2.1 Implement `strategies/strategy2_extreme.py`: a `Strategy2Extreme(BaseStrategy)` with `name="extreme_fvg"`, `default_params` matching current Strategy 2 defaults, `find_setups()` wrapping `get_extreme_setup_for_symbol(...)`, and `backtest()` wrapping `run_extreme_backtest(...)`; verify adapter resolves and is registered in the registry
- [x] 2.2 Add `EXTREME_ACTIVE_STRATEGY` env → `state["extreme_active_strategy"]` (default `"extreme_fvg"`) in `app_config.py`; verify a fresh import sets `state["extreme_active_strategy"] == "extreme_fvg"` with no env set
- [x] 2.3 Add `"active_strategy"` to `_runtime_extreme_config()` in `screener_cycle.py` and merge the active strategy's `resolve_params(...)` so existing keys (`ltf`, `target`, `min_gap`, `use_close`, `sess_filter`, ...) are unchanged; verify `_runtime_extreme_config()` returns identical Strategy 2 keys/values as before the change

## 3. Strategy-Agnostic Trade Ledger

- [x] 3.1 Add `strategy: str = "extreme_fvg"` and `strategy_params: Dict = field(default_factory=dict)` to `TrackedExtremeTrade` in `extreme_trade_tracker.py`; verify `to_dict()` emits both and `from_dict()` of an old record (no new keys) still loads with defaults
- [x] 3.2 Add an optional `strategy` filter to ledger query/report (`get_filtered_trades` / summary) and verify filtering by `strategy="extreme_fvg"` returns the expected trade set

## 4. Daemon & API Wiring

- [x] 4.1 Replace the direct `from strategy_extreme_fvg import get_extreme_setup_for_symbol` call in `screener_cycle.execute_extreme_screener_cycle()` with `get_strategy(cfg["active_strategy"]).find_setups(symbol, provider, cfg)`; verify existing pytest suite passes unchanged (`pytest -v`)
- [x] 4.2 Add strategy-parameterized API routes (`/api/{strategy}/backtest|status|info`) that resolve via registry while keeping all existing `/api/extreme/*` routes unchanged; a queried-strategy `status` response includes `is_active` so consumers know whether it is the daemon's running strategy. `/api/{strategy}/scan` and `/api/{strategy}/config` were deferred out of scope (Strategy 3 follow-on). Verify existing API tests (`test_extreme_fastapi_daemon.py` etc.) pass unchanged and a new route for `extreme_fvg` returns the same payload as its existing counterpart

## 5. Tests & Verification

- [x] 5.1 Add `test_strategy_registry.py` covering: enumerate names (contains `extreme_fvg`), resolve by name, unknown-name error with available-list, and `resolve_params` precedence
- [x] 5.2 Add `test_strategy_interface.py` covering the strategy-agnostic ledger fields (`strategy`, `strategy_params` round-trip and backward-compatible load) and that the daemon resolves the active strategy by name
- [x] 5.3 Add a Strategy-2 adapter equivalence test asserting `Strategy2Extreme().backtest(symbol, days, ...)` produces the same report as a direct `run_extreme_backtest(...)` call with equivalent params
- [x] 5.4 Run the full test suite (`pytest -v`) and confirm all pre-existing tests pass unchanged with zero modifications to them