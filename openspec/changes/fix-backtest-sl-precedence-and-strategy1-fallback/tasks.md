# Tasks: Strict Backtest SL Precedence and Strategy 1 Candidate Fallback

## 1. Backtesting Engine Tests & Fix (`backtest_extreme_fvg.py`)

- [x] 1.1 Add TDD test in `test_backtest_extreme_fvg.py`: same-bar collision between SL and TP (1R, 2R, 3R) must resolve as `STOPPED_OUT` with `hit_1r = hit_2r = hit_3r = False` and `realized_r_1r = realized_r_2r = realized_r_3r = -1.0`
- [x] 1.2 Add TDD test in `test_backtest_extreme_fvg.py`: Bearish mirror for same-bar SL and TP collision
- [x] 1.3 Add TDD test in `test_backtest_extreme_fvg.py`: TP1 hit on bar 1 (no SL breach) followed by SL hit on bar 2 correctly records `hit_1r = True`, `realized_r_1r = 1.0`, `realized_r_2r = -1.0`, `realized_r_3r = -1.0`, `exit_reason = "STOPPED_OUT"`
- [x] 1.4 Update `simulate_trade_execution` in `backtest_extreme_fvg.py` to enforce strict Stop Loss precedence first on every candle

## 2. Strategy 1 Candidate Search Tests & Fix (`strategy.py`)

- [x] 2.1 Add test in `test_screener.py`: verify that if first LTF FVG has invalid SL reference relative to price, `phase2_check` continues and discovers a valid subsequent LTF FVG
- [x] 2.2 Update `strategy.py:721-729` to `continue` across candidate indices on invalid SL reference

## 3. Verification & OpenSpec Updates

- [x] 3.1 Run full test suite with `.venv/bin/pytest -v` and verify 100% pass
- [x] 3.2 Update `openspec/specs/strategy-2-extreme/spec.md` with explicit Backtest SL Precedence requirement
- [x] 3.3 Mark all tasks completed in `tasks.md`
