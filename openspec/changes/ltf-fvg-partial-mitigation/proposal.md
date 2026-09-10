# Change Proposal: LTF FVG Partial Mitigation & Dynamic Gap Reduction

## 1. Problem Statement
Previously, when a trade setup triggered from an LTF FVG and achieved its completion target (e.g. 1R, 2R, or 3R TP), the LTF FVG was permanently marked as mitigated/consumed and removed from the active candidate pool.

However, in many market structures, price only partially penetrates the Fair Value Gap (e.g., dipping 30-50% into the zone) before surging towards TP. The remaining unfilled portion of the FVG represents valid, unmitigated institutional imbalance. Discarding the entire FVG prematurely results in missing subsequent high-probability retrace entries into the residual gap.

## 2. Proposed Solution
Implement **Dynamic Gap Reduction & Partial Mitigation**:
1. **Persistent FVG Pool**: Completing a trade at TP does NOT invalidate the underlying LTF FVG unless the entire gap has been wicked through.
2. **Dynamic Boundary Truncation**:
   - **Bullish FVG**: The upper boundary (`top` / `entry_price`) shrinks down to the lowest wick point reached during previous touches: `new_top = lowest_wick_reached`.
   - **Bearish FVG**: The lower boundary (`bottom` / `entry_price`) rises up to the highest wick point reached during previous touches: `new_bottom = highest_wick_reached`.
3. **Minimum Residual Gap Constraint (`min_gap_pct`)**:
   - The shrunk gap `(new_top - bottom) / bottom` (for Bullish) must still meet or exceed `min_gap_pct` (e.g. $\ge 0.05\%$).
4. **Structural Stop Loss Preservation**:
   - Re-entry trades on reduced gaps preserve the original structural Stop Loss across the initial 3-candle sequence (`min(C1, C2, C3)` for Bullish, `max(C1, C2, C3)` for Bearish).
5. **Full Invalidation Condition**:
   - An LTF FVG is ONLY completely invalidated if a wick pierces entirely through the opposing boundary (`low < bottom` for Bullish, `high > top` for Bearish, which includes all SL hit cases) or if the parent 4H anchor FVG is invalidated.

## 3. Impact & Scope
- **Strategy Engine (`strategy_extreme_fvg.py`)**: Update FVG tracking, unmitigated filter, boundary clipping, and setup builder.
- **Trade Tracker (`extreme_trade_tracker.py`)**: Upon trade resolution (TP hit), calculate the deepest adverse wick reached and emit the residual FVG back into candidate state.
- **Backtester (`backtest_extreme_fvg.py`)**: Update backtest simulation loop to support multi-entry retrace cycles on residual gaps.
- **Dashboard & Telegram**: Display re-entry count and dynamically adjusted zone dimensions.
