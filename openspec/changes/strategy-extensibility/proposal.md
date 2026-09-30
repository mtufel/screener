## Why

Today the codebase has a single engine (`strategy_extreme_fvg.py` + `backtest_extreme_fvg.py`), invoked directly by `screener_cycle.py`, `api/extreme.py`, and `main.py`, with trade tracking hard-wired to Strategy 2 concepts in `extreme_trade_tracker.py`. There is no way to run a second strategy (e.g. the new Strategy 3 from the Atif Hussain video) without either duplicating the whole daemon/API/ledger or threading per-strategy branches through every layer. The strategy-extensibility refactor — modeled on freqtrade's `IStrategy` + `StrategyResolver` + config-reconciliation architecture — makes adding a strategy a drop-in operation (new file + register), keeping the daemon, API, dashboard, telegram, and trade ledger strategy-agnostic.

## What Changes

- Introduce a `strategies/` package with a `BaseStrategy` abstract interface (freqtrade `IStrategy` analog) that declares its own params as declarative class attributes (freqtrade pattern: `stoploss`/`timeframe`/`minimal_roi` as class attrs).
- Introduce a strategy **registry/resolver** (`strategies/registry.py`, freqtrade `StrategyResolver` analog) that loads strategies by stable name at runtime — the daemon and API only ever say `get_strategy("extreme_fvg")` or `get_strategy("strategy3_video")`, never hard-wire an engine.
- Reconcile config ↔ strategy attributes with freqtrade's precedence: **runtime config > strategy class default > built-in default**; selected strategy name comes from a new `EXTREME_ACTIVE_STRATEGY` env → `app_config.state["active_strategy"]`, defaulting to `extreme_fvg` so **today's behavior is unchanged**.
- Wrap the existing Strategy 2 engine behind `BaseStrategy` as `strategies/strategy2_extreme.py` (thin adapter — the ~1200-line FVG engine and ~830-line backtest are **not rewritten**, only adapted).
- Refactor `extreme_trade_tracker.TrackedTrade` to be strategy-agnostic: add a `strategy` name field and a `strategy_params: Dict` JSON blob (freqtrade `Trade` analog, which carries `enter_tag`/`exit_reason` but no strategy-specific columns), so one ledger serves every strategy.
- Make the daemon's `execute_extreme_screener_cycle()` resolve which strategy to run from config, keeping the existing orchestration logic (freqtrade's bot loop is strategy-agnostic).
- Parameterize API paths as `api/{strategy}/...` where supported while keeping existing Strategy 2 paths backward-compatible.
- **Planned follow-on (separate change, NOT part of this one)**: add Strategy 3 as `strategies/strategy3_video.py` once the refactor lands, and apply the already-spec'd `extreme-fvg-bias-filters` defaults. This change only builds the *framework* + the Strategy 2 adapter so it is verifiable with no behavior change.

## Capabilities

### New Capabilities
- `strategy-framework`: The pluggable strategy interface, name-keyed registry/resolver, config↔strategy reconciliation, and strategy-agnostic trade ledger / daemon / API that together let new strategies be added drop-in without touching core orchestration.

### Modified Capabilities
<!-- None: this change is additive — no existing strategy's runtime behavior or requirements change (Strategy 2 keeps identical defaults and behavior; it is only reached through a thin adapter). Field additions to the trade ledger are covered under the new strategy-framework capability. -->

## Impact

- **Code added**: `strategies/base.py`, `strategies/registry.py`, `strategies/strategy2_extreme.py`; additive `strategy`/`strategy_params` fields on `TrackedTrade` in `extreme_trade_tracker.py`.
- **Code modified (thin)**: injection point in `screener_cycle.py` `execute_extreme_screener_cycle()` to resolve strategy via registry; `EXTREME_ACTIVE_STRATEGY` env + `state["active_strategy"]` in `app_config.py`; API path parameterization in `api/extreme.py` (backward-compatible).
- **Not touched physically**: `strategy_extreme_fvg.py`, `backtest_extreme_fvg.py` internals, existing tests (SOT per CLAUDE.md).
- **Tests added (add-only)**: `test_strategy_registry.py`, `test_strategy_interface.py`; existing tests remain the source of truth and must pass unchanged.
- **Workflow**: all work on branch `feat/strategy-extensibility` off `develop`, merged via PR, following OpenSpec propose → design → tasks → apply → archive.
- **Reference**: architecture modeled on freqtrade's `freqtrade/strategy/interface.py` (`IStrategy`) and `freqtrade/resolvers/strategy_resolver.py` (`StrategyResolver`).
