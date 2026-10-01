## Why

`run_liquidity_sweep_backtest()` accepts a `tp_mode` parameter (`"LIQUIDITY"` / `"FIXED_R"`) and
records it on the returned report, but **never applies it**. The backtester calls the pure
`liquidity_take_profit()` helper directly, and that helper has no `tp_mode` parameter — it is
unconditionally liquidity-first with a fixed-R fallback.

Consequences:

1. **A requested ablation silently does nothing.** `tp_mode="FIXED_R"` produces a
   byte-identical trade list, `net_r`, and `profit_factor` to `tp_mode="LIQUIDITY"`. The report
   echoes back `"FIXED_R"` while every trade was resolved as `TP_LIQUIDITY`, so the output is
   actively misleading rather than merely inert.
2. **Backtest/live divergence.** The live engine resolves TP through
   `apply_liquidity_tp()` (`strategy_liquidity_sweep_fvg.py:226`), which *does* branch on
   `tp_mode`. Only the backtester is wrong. The backtester's entire purpose is parity with live,
   so this is a parity defect.
3. **Any conclusion drawn from the FIXED_R ablation is unsupported.** The liquidity-first TP has
   never actually been compared against a pure fixed-R TP in this codebase.

## What Changes

- **Backtester reuses the live engine's TP resolver** (`backtest_liquidity_sweep_fvg.py`): replace
  the direct `liquidity_take_profit()` call in the fill loop with `apply_liquidity_tp()`, passing
  `tp_mode`, `min_rr_for_liquidity`, `fallback_target_r`, and the buffer.
  - Single code path for TP resolution across live and backtest — parity by construction rather
    than by convention.
  - Also removes the hardcoded `buffer_pct=0.02` at the call site in favour of
    `DEFAULT_TP_BUFFER_PCT`, so the buffer is configurable in both paths.
- **`liquidity_take_profit()` (`liquidity.py`) is unchanged.** It remains the pure
  liquidity-first primitive with no `tp_mode`; its three existing tests are the Source of Truth
  and continue to pass untouched.
- **TDD coverage** in `test_backtest_liquidity_sweep_fvg.py`:
  - `tp_mode="FIXED_R"` places the TP at exactly `entry ± fallback_target_r * risk_r` and tags
    every trade `FIXED_R`, with no pool attached.
  - `tp_mode="LIQUIDITY"` still resolves to the nearest qualifying pool (regression guard).
  - The two modes produce *different* trade outcomes on the same fixture — the direct assertion
    that the parameter is no longer inert.

## Capabilities

### Modified Capabilities

- `strategy-3-liquidity-sweep`: The backtester honours `tp_mode`, sharing the live engine's
  `apply_liquidity_tp()` resolver so backtest and live agree on take-profit placement.

## Impact

- **Affected code**: `backtest_liquidity_sweep_fvg.py` (fill loop),
  `test_backtest_liquidity_sweep_fvg.py` (new).
- **Unchanged**: `liquidity.py`, `strategy_liquidity_sweep_fvg.py`, the live execution path.
- **No behaviour change to live trading.** This is a measurement-correctness fix only.
- **Known invalidation:** previously generated Strategy 3 reports that label a run
  `tp_mode="FIXED_R"` were produced with liquidity-first TP and must be regenerated.
  Affected artefacts: `strategy3_validation_report.html`.