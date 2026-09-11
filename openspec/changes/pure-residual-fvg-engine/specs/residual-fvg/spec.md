# Specification: Modular Residual FVG Requirements & Invariants

## Invariant 1: Config-Driven Modularity
- Partial mitigation MUST be toggleable globally via `EXTREME_PARTIAL_MITIGATION_ENABLED`, via REST API `/api/extreme/config`, in live screener parameters, and in backtest execution.
- When `partial_mitigation=False`, the engine MUST strictly adhere to classic 1-trade-per-FVG semantics with zero boundary shrinkage.
- When `partial_mitigation=True`, the engine MUST evaluate dynamic boundary shrinkage via `create_residual_fvg()`.

## Invariant 2: Structural Stop Loss Immutability
For any residual FVG derived via `create_residual_fvg()`:
- `c1`, `c2`, `c3` MUST remain identical to the original formation candles.
- Bullish Stop Loss MUST strictly equal `min(c1.low, c2.low, c3.low)`.
- Bearish Stop Loss MUST strictly equal `max(c1.high, c2.high, c3.high)`.
- The Stop Loss level MUST NOT move when boundaries shrink.

## Invariant 3: Boundary Shrinkage & Mitigation Count
- For a Bullish FVG `[bottom, top]` with deepest adverse wick `deepest_wick` during a profitable trade:
  - If `deepest_wick <= bottom`, the zone is 100% mitigated $\to$ return `None`.
  - If `deepest_wick < top`, residual `top = deepest_wick`, residual `bottom = bottom`.
  - `mitigation_count = fvg.mitigation_count + 1`.
- For a Bearish FVG `[bottom, top]` with deepest adverse wick `deepest_wick` during a profitable trade:
  - If `deepest_wick >= top`, the zone is 100% mitigated $\to$ return `None`.
  - If `deepest_wick > bottom`, residual `bottom = deepest_wick`, residual `top = top`.
  - `mitigation_count = fvg.mitigation_count + 1`.

## Invariant 4: Non-Mutating Pure Operations
- `create_residual_fvg()` MUST return a newly allocated `FVG` instance and MUST NOT mutate the input `fvg`.
- `evaluate_ltf_setup_lifecycle()` MUST NOT mutate the input `ltf_fvg` in place.
