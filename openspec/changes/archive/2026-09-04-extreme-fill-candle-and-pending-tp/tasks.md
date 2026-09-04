# Tasks: Fill-Candle Exit Detection & Pending TP Completion Alignment

## 1. Backtest Fill-Candle Evaluation
- [x] 1.1 Change `run_extreme_backtest` in `backtest_extreme_fvg.py` to pass the fill candle into forward simulation (`candles_ltf[k:]` instead of `candles_ltf[k + 1:]`).

## 2. Pending Same-Bar Fill Completion
- [x] 2.1 Extend the `PENDING_RETRACE` pre-entry monitor in `extreme_trade_tracker.py` to resolve pending trades to `COMPLETED_TP` (emit `TP_HIT`, archive to history) when a single closed candle touches entry and reaches the completion target.

## 3. Regression Tests
- [x] 3.1 Add backtest regression test in `test_backtest_extreme_fvg.py`: fill candle reaching TP2 resolves `hit_2r=True` (and mirrored SL case resolves `STOPPED_OUT`).
- [x] 3.2 Add tracker regression test in `test_extreme_trade_tracker.py`: pending trade with no emitted setup completes via single touch+TP candle, history state `COMPLETED_TP` with `realized_r = +2.0`.

## 4. Validation
- [x] 4.1 Run full test suite; all tests pass with the two new regression tests green.
