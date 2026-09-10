# Implementation Tasks: LTF FVG Partial Mitigation & Dynamic Gap Reduction

## Tasks

### Phase 1: Core Strategy & Model Updates
- [x] Task 1.1: Update `FVG` dataclass in `strategy_extreme_fvg.py` to support `original_top`, `original_bottom`, and `mitigation_count`.
- [x] Task 1.2: Implement `shrink_fvg_on_mitigation(fvg, lowest_wick, highest_wick, min_gap_pct)` helper in `strategy_extreme_fvg.py`.
- [x] Task 1.3: Update `find_unmitigated_ltf_fvgs` and `select_extreme_ltf_fvg` to accept shrunk residual FVGs with preserved structural Stop Loss.

### Phase 2: Trade Tracker Lifecycle & Re-evaluation
- [x] Task 2.1: In `extreme_trade_tracker.py`, track the deepest adverse wick penetration across all candles during `TRADE_ACTIVE` state.
- [x] Task 2.2: Upon TP hit resolution in `extreme_trade_tracker.py`, compute the remaining gap, apply `min_gap_pct`, and transition the residual FVG to `PENDING_RETRACE` if valid.

### Phase 3: Backtest Engine Integration
- [x] Task 3.1: Update `backtest_extreme_fvg.py` simulation loop to allow multiple sequential trades on partially filled residual LTF FVGs.

### Phase 4: Unit & Integration Testing
- [x] Task 4.1: Write unit tests in `test_ltf_fvg_mitigation.py` verifying boundary reduction, min gap rejection, structural SL preservation, and multi-trade execution.
- [x] Task 4.2: Run full test suite (`pytest -v`) to confirm 100% pass rate.
