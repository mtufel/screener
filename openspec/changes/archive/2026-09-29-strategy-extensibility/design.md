## Context

See proposal.md - Why. The motivating problem: a single engine (`strategy_extreme_fvg.py` + `backtest_extreme_fvg.py`) is invoked by direct imports at multiple layers (`screener_cycle.execute_extreme_screener_cycle`, `api/extreme.py` endpoints, `main.py` legacy routes), and `extreme_trade_tracker.TrackedExtremeTrade` is a Strategy-2-shaped dataclass. Adding Strategy 3 today would mean duplicating the daemon/API/ledger or threading per-strategy branches through every layer.

The reference is freqtrade's extensibility model, specifically `freqtrade/strategy/interface.py` (`IStrategy`) and `freqtrade/resolvers/strategy_resolver.py` (`StrategyResolver`).

**Key constraints observed in the current code:**
- `screener_cycle.py` has a documented **PATCH-SURFACE CONTRACT**: qa_harness and pytest suites patch services as attributes of the `main` module via `_svc()`/`_patchable_services`. The daemon reads config through `_runtime_extreme_config()` (a single snapshot dict) rather than touching modules directly.
- `TrackedExtremeTrade.from_dict()` already filters to known dataclass fields (`valid_keys`), so **adding fields is backward-compatible** with persisted trade JSON.
- `strategy_extreme_fvg.get_extreme_setup_for_symbol(...)` and `backtest_extreme_fvg.run_extreme_backtest(...)` are the two strategy engines; both are already `async`, accept rich parameter sets, and are called with keyword args.
- Existing tests are Source of Truth (CLAUDE.md) — must not be modified; the refactor must keep Strategy 2 behavior byte-identical so existing tests pass unchanged.

## Goals / Non-Goals

**Goals:**
- Allow a new strategy to be added without touching daemon, API, ledger, dashboard, telegram, or Redis code — only a new file + registration.
- Keep today's Strategy 2 behavior and defaults unchanged (verify via existing tests passing untouched).
- Adopt freqtrade's proven extensibility patterns: declarative class-attr params, name-keyed runtime registration, config↔strategy reconciliation with config-over-strategy-over-default precedence, strategy-agnostic trade ledger.
- Reuse the existing engine implementations as-is (no reimplementation of FVG math).

**Non-Goals:**
- Do NOT implement Strategy 3 in this change (separate follow-on once this framework lands).
- Do NOT flip Strategy 2 production defaults or apply the research bias filters here (that is the separately-spec'd `extreme-fvg-bias-filters` change).
- Do NOT build freqtrade's full dataframe/populate_indicators vectorized signal machinery — our strategies are async per-symbol setup generators, not dataframe vectorizers. We keep freqtrade's loading/registry/reconciliation model, not its indicator pipeline.
- Do NOT move `strategy_extreme_fvg.py`/`backtest_extreme_fvg.py` or rewrite their internals.

## Decisions

### D1. `strategies/base.py` — `BaseStrategy` interface (freqtrade `IStrategy` analog)
```python
class BaseStrategy(ABC):
    name: str = ""                      # stable registry key, e.g. "extreme_fvg"
    display_name: str = ""              # human label
    interface_version: int = 1          # freqtrade INTERFACE_VERSION analog
    # Declarative defaults (freqtrade class-attr pattern: stoploss/timeframe/minimal_roi).
    default_params: Dict[str, Any] = {}

    def resolve_params(self, runtime_overrides: Dict[str, Any]) -> Dict[str, Any]:
        """Merge with precedence runtime_overrides > self.default_params > base."""
    @abstractmethod
    async def find_setups(self, symbol: str, provider, params: Dict[str, Any]) -> List[Any]:
        """Uniform setup generator (wraps get_extreme_setup_for_symbol for Strategy 2)."""
    @abstractmethod
    async def backtest(self, symbol: str, days: int, provider, params: Dict[str, Any]):
        """Uniform backtest runner (wraps run_extreme_backtest for Strategy 2)."""
```
Rationale: mirrors freqtrade's `IStrategy` (ABC, declarative class attributes, `INTERFACE_VERSION`). **Alternative considered**: a duck-typed protocol with no base class. Rejected — an ABC documents the contract, enables registry type-checks, and gives strategy authors a checklist.

### D2. `strategies/registry.py` — name-keyed registry (freqtrade `StrategyResolver` analog)
```python
STRATEGIES: Dict[str, Type[BaseStrategy]] = {}
def register(cls: Type[BaseStrategy]) -> Type[BaseStrategy]: ...   # decorator on cls.name
def get_strategy(name: str) -> BaseStrategy: ...                    # raises KeyError w/ available list
def list_strategy_names() -> List[str]: ...
```
Strategies self-register via `@register` at import time; `screener_cycle`/`api` import the registry module once. Daemon/API only ever say `get_strategy("extreme_fvg")`.
Rationale: freqtrade loads from disk by search path via `StrategyResolver._load_strategy`. We use an explicit in-process registry keyed by the same stable name — simpler, and the "available by name" UX is identical. **Alternative**: dynamic file-graph scanning (freqtrade's `recursive_strategy_search`) — deferred; not needed for two strategies, adds path/discovery complexity.

### D3. Config ↔ strategy reconciliation (freqtrade `_override_attribute_helper`)
freqtrade precedence is **config > strategy attr > default**; its resolver also pushes resolved values back into config so the rest of the bot is strategy-agnostic. We mirror this:
- `app_config.state["active_strategy"]` ← `EXTREME_ACTIVE_STRATEGY` env (default `"extreme_fvg"`).
- `_runtime_extreme_config()` gains `"active_strategy"` and merges the strategy's `resolve_params(...)` into the dict the daemon already passes around — so Strategy 2's current keys (`ltf`, `target`, `min_gap`, `use_close`, `sess_filter`, …) continue to map 1:1, and Strategy 3 can introduce its own params without the daemon knowing them.
Rationale: keeps the daemon's parameter-flow contract intact (the `_runtime_extreme_config` snapshot dict stays the single source for orchestration), exactly like freqtrade keeps the config dict as the single source for its bot loop.

### D4. Strategy 2 adapter — `strategies/strategy2_extreme.py`
A thin adapter, not a rewrite:
- `find_setups(symbol, provider, params)` → builds kwargs and `await get_extreme_setup_for_symbol(...)`.
- `backtest(...)` → `await run_extreme_backtest(...)`.
- `default_params` declares the current Strategy 2 defaults (`ltf_timeframe=5m`, `completion_target=2R`, `min_gap_pct=0.05`, `use_close_invalidation=False`, `session_filter=False`, `sessions="ALL"`, …) so reconciliation yields today's behavior.
Rationale: freqtrade also separates strategy (signals) from indicators/backtest libs; we deliberately **do not** reimplement the ~1200-line engine. The adapter makes Strategy 2's behavior observable through the uniform interface with zero engine churn.

### D5. Strategy-agnostic ledger (freqtrade `Trade` analog)
In `extreme_trade_tracker.py`, add two fields to `TrackedExtremeTrade`:
```python
strategy: str = "extreme_fvg"                 # originating strategy name
strategy_params: Dict[str, Any] = field(default_factory=dict)   # effective params at open
```
`to_dict()`/`from_dict()` already tolerate this (`from_dict` filters to known fields → persisted records without the new keys still load). Ledger queries gain an optional `strategy` filter.
Rationale: freqtrade's `Trade` object is strategy-agnostic and carries `enter_tag`/`exit_reason` rather than strategy-specific columns. Here `strategy` + a JSON `strategy_params` blob gives the same property: one ledger, dashboard, telegram, and Redis model serve every strategy.

### D6. Daemon injection point
In `screener_cycle.execute_extreme_screener_cycle()`:
- Resolve `strategy = get_strategy(cfg["active_strategy"])`.
- Replace the direct `from strategy_extreme_fvg import get_extreme_setup_for_symbol` call with `strategy.find_setups(symbol, provider, cfg)`.
This is the single behavioral seam. **Alternative**: a separate daemon per strategy — rejected (freqtrade runs one strategy-agnostic bot loop; duplicating the daemon defeats the point).

### D7. API addressing
Keep all existing `/api/extreme/*` routes as-is (backward-compatible, satisfying spec req), and add strategy-parameterized routes `/api/{strategy}/...` (scan/backtest/config/status) that resolve the strategy via registry. The existing `api/extreme.py` handlers delegate to the registry for the active strategy.
Rationale: mirrors freqtrade's config-driven strategy selection while guaranteeing existing clients (dashboard) keep working.

## Risks / Trade-offs

- [Strategy 2 behavior regression] → The adapter passes the *current* defaults, so resolved params equal today's. Existing test suites (SOT) must pass unchanged after the refactor; run the full pytest suite as the acceptance gate.
- [PATCH-SURFACE contract break] → The daemon still reads services via `_svc()`/`_patchable_services` and config via `_runtime_extreme_config()`; we only swap the *strategy call*, not the service-read pattern. Keep all bare-name reads intact.
- [Registry import-time coupling / circular imports] → Registry module imports strategy modules lazily inside `get_strategy` (or strategies register on import of the package `__init__`), avoiding `screener_cycle` ↔ strategy cycles.
- [Additive fields on a persisted ledger] → `from_dict` already filters unknown keys, so old persisted records load with defaults (`strategy="extreme_fvg"`, empty `strategy_params`). No migration needed.
- [Over-engineering risk for two strategies] → The ABC + registry + resolver add ~150 lines. Accepted: this is the explicit goal (easy new strategy), and freqtrade proves the pattern scales.

## Migration Plan

1. Branch: `feat/strategy-extensibility` off `develop`.
2. Land additive pieces first so behavior is unchanged at every commit: `BaseStrategy`, `registry`, `strategy2_extreme` adapter, ledger fields, `EXTREME_ACTIVE_STRATEGY` env → `state["active_strategy"]`.
3. Wire the daemon injection (defaults to `extreme_fvg` → identical behavior). Verify existing tests pass unchanged (`pytest -v`).
4. Add strategy-parameterized API routes (backward-compatible).
5. Add-only tests: `test_strategy_registry.py`, `test_strategy_interface.py`, strategy2-adapter equivalence test asserting `run_extreme_backtest` via adapter == direct call.
6. Merge via PR; follow OpenSpec apply → archive.
- **Rollback**: since Strategy 2 defaults are unchanged and routing defaults to `extreme_fvg`, reverting the branch restores the previous wiring with no state migration.

## Open Questions

- Whether the strategy-parameterized API routes should supersede or parallel the existing `/api/extreme/*` paths long-term (deferred — both will coexist; the dashboard only uses existing paths for now).
- Whether `EXTREME_ACTIVE_STRATEGY` should also be runtime-toggleable via the config/status API in addition to env (deferred — env + `state` is sufficient for the first version; API toggling can be layered on without spec change).
