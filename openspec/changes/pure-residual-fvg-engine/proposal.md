# Proposal: Pure Functional Residual FVG Engine (Config-Driven & Modular)

## Problem Statement
The initial implementation of LTF FVG partial mitigation lacked modularity, relied on in-place object mutations across candle loops, differed in `gap_pct` mathematical formulations between scanner and tracker modules, missing `mitigation_count` propagation during trade ingestion, and could not be selectively enabled/disabled at runtime or in backtests.

## Proposed Solution
Re-architect the residual FVG mechanism around a **modular, config-driven, pure functional factory model**:
1. **Config-Driven Feature Toggle (`EXTREME_PARTIAL_MITIGATION_ENABLED`)**:
   - Controlled via environment variable `EXTREME_PARTIAL_MITIGATION_ENABLED=true/false` (default: `true`).
   - Configurable at runtime via `/api/extreme/config?partial_mitigation=true/false` and live UI dropdown.
   - Selectable in CLI & programmatic backtester via `--partial-mitigation` / `run_extreme_backtest(..., partial_mitigation=True/False)`.
   - When `disabled`, the system runs classic 1-trade-per-FVG behavior; when `enabled`, it seamlessly evaluates dynamic residual boundaries.
2. **Pure Function `create_residual_fvg(fvg, deepest_wick, min_gap_pct)`**:
   - Computes and returns a brand-new `FVG` instance with shrunk boundary, incremented `mitigation_count`, and strictly preserved structural `c1, c2, c3` Stop Loss without mutating input objects.
3. **Unified `gap_pct` Formula**: Align all modules (`strategy_extreme_fvg.py`, `extreme_trade_tracker.py`, `backtest_extreme_fvg.py`) to use `FVG.gap_pct` (`width / midpoint * 100.0`).
4. **Full Backtester Re-Entry Execution**: Dynamically re-queue valid residual FVGs upon TP resolution in `backtest_extreme_fvg.py` when toggle is enabled.
5. **Live Ledger Deduplication & Metadata Synchronization**: Ensure updated residual entries replace matching pending setups in-place without creating duplicate pending records.
6. **Immutable Lifecycle State Evaluation**: Ensure `evaluate_ltf_setup_lifecycle` operates purely on local scalar variables.

## Impact & Benefits
- 100% modular and switchable on/off anytime.
- Zero object mutation side-effects.
- 100% parity between live scanner, live trade tracker, and historical backtester.
