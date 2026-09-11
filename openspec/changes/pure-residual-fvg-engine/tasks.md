# Implementation Tasks: Modular Residual FVG Engine

### Phase 1: Pure Residual Factory & Core Strategy Refactoring
- [x] Task 1.1: Implement pure factory `create_residual_fvg(fvg, deepest_wick, min_gap_pct)` in `strategy_extreme_fvg.py`.
- [x] Task 1.2: Refactor `evaluate_ltf_setup_lifecycle()` to support `partial_mitigation: bool` without in-place object mutations.
- [x] Task 1.3: Update `find_unmitigated_ltf_fvgs()` and `get_extreme_setup_for_symbol()` with `partial_mitigation: bool` parameter.
- [x] Task 1.4: Update `fvg_to_dict()` and `fvg_from_dict()` for safe residual metadata serialization.

### Phase 2: Configuration & API Modularity
- [x] Task 2.1: Add `EXTREME_PARTIAL_MITIGATION_ENABLED` configuration in `main.py`, `.env`, and app state.
- [x] Task 2.2: Expose `partial_mitigation` toggle in `/api/extreme/config` (GET & POST) and `/api/extreme/status`.
- [x] Task 2.3: Wire runtime configuration toggle to Web UI in `templates/index.html`.

### Phase 3: Live Trade Tracker Deduplication & Alignment
- [x] Task 3.1: Refactor `_compute_residual_fvg()` in `extreme_trade_tracker.py` to delegate to `create_residual_fvg()`.
- [x] Task 3.2: Pass `mitigation_count` into `TrackedExtremeTrade` instantiation in `process_live_setups()`.
- [x] Task 3.3: Fix pending setup refresh logic (`fvg_formed_at >= existing_formed`) to prevent duplicate pending rows.

### Phase 4: Backtester Sequential Residual Re-Entry Execution
- [x] Task 4.1: Add `--partial-mitigation` / `--no-partial-mitigation` CLI flags to `backtest_extreme_fvg.py` and `partial_mitigation: bool = True` argument to `run_extreme_backtest()`.
- [x] Task 4.2: Implement dynamic re-queueing of residual FVGs upon TP hit when `partial_mitigation=True`.
- [x] Task 4.3: Ensure classic 1-trade semantics when `partial_mitigation=False`.

### Phase 5: Unit & Integration Tests
- [x] Task 5.1: Write unit tests in `test_ltf_fvg_mitigation.py` verifying enabled vs disabled partial mitigation, pure factory construction, tracker deduplication, and backtest multi-trade execution.
- [x] Task 5.2: Run full test suite (`pytest -v`) to verify 100% pass rate.
