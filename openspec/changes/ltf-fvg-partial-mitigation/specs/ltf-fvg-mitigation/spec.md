# Capability Specification: LTF FVG Mitigation & Residual Gap Re-entry

## Requirements

### Requirement 1: Non-Invalidation on TP Completion
- When a trade completes successfully by reaching its target (1R, 2R, or 3R TP), the underlying LTF FVG MUST NOT be marked invalid if an unfilled portion of the gap remains.

### Requirement 2: Exact Boundary Reduction
- Upon trade completion at TP:
  - For Bullish FVGs, `top` MUST be set to `min(all low wicks observed during trade execution)`.
  - For Bearish FVGs, `bottom` MUST be set to `max(all high wicks observed during trade execution)`.

### Requirement 3: Enforce Minimum Residual Gap Constraint
- After truncation, the residual gap size MUST satisfy:
  - Bullish: `((top - bottom) / bottom) * 100.0 >= min_gap_pct`
  - Bearish: `((top - bottom) / top) * 100.0 >= min_gap_pct`
- If the remaining gap size is strictly less than `min_gap_pct`, the FVG MUST be marked as fully mitigated / inactive.

### Requirement 4: Structure Stop Loss Preservation
- For any subsequent trade executed on a residual FVG, the Stop Loss MUST be calculated from the original 3-candle sequence:
  - Bullish: `stop_loss = min(c1.low, c2.low, c3.low)`
  - Bearish: `stop_loss = max(c1.high, c2.high, c3.high)`

### Requirement 5: Invalidation Invariants
- An LTF FVG MUST be permanently invalidated if:
  1. Price wicks through the opposite boundary (`low <= bottom` for Bullish, `high >= top` for Bearish).
  2. The parent 4H anchor FVG becomes invalidated.
