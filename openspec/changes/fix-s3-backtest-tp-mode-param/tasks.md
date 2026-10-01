## Tasks

- [x] 1. Reproduce: confirm `tp_mode="FIXED_R"` yields an identical trade list, `net_r`, and
      `profit_factor` to `tp_mode="LIQUIDITY"` on a fixed BTC 90d fixture, while the report
      echoes `"FIXED_R"`.
- [x] 2. Confirm the live path is already correct (`apply_liquidity_tp` branches on `tp_mode`),
      scoping the defect to the backtester only.
- [x] 3. Add failing TDD coverage in `test_backtest_liquidity_sweep_fvg.py` asserting
      `FIXED_R` places the TP at `entry ± fallback_target_r * risk_r` and that the two modes
      differ in outcome.
- [x] 4. Replace the direct `liquidity_take_profit()` call in the backtester fill loop with
      `apply_liquidity_tp()`, threading `tp_mode`, `min_rr_for_liquidity`,
      `fallback_target_r`, and `DEFAULT_TP_BUFFER_PCT`.
- [x] 5. Re-run the reproduction: assert `FIXED_R` now differs from `LIQUIDITY`, and that
      `LIQUIDITY` output is unchanged from the pre-fix baseline (+8.17R, PF 1.58, n=28 on BTC 90d).
- [x] 6. Run the full test suite (`pytest -v`) — all tests green, no existing test modified.
- [x] 7. Note the invalidation of previously generated FIXED_R-labelled reports in
      `STRATEGIES.md` and `docs/source-videos.md` (F-10).