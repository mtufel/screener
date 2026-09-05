## Why

1. **Backtest vs. Live Ledger SL Precedence Discrepancy**: In `backtest_extreme_fvg.py`, `simulate_trade_execution` evaluated TP milestones *before* Stop Loss on candidate candles. If a single candle had high volatility spanning both a TP target and the Stop Loss price, the backtester awarded an optimistic win (e.g. +3.0R on a bar hitting both TP3 and SL, or +1.0R/+2.0R on a bar hitting TP1/TP2 and SL), whereas the live ledger (`extreme_trade_tracker.py`) and live strategy engine (`strategy_extreme_fvg.py`) strictly enforce conservative Stop-Loss precedence (-1.0R loss).
2. **Strategy 1 Candidate Search Premature Abort**: In `strategy.py:721-729`, when iterating through candidate LTF FVGs, if a candidate has an invalid stop loss reference relative to the current price (`sl_ref >= current_price` for Bullish or `sl_ref <= current_price` for Bearish), the function abruptly executed `return None` when `phase1_coin.htf_touch_ts is not None`, prematurely discarding any other valid unmitigated LTF FVGs formed later in the series.

## What Changes

- **Strict SL Precedence in Backtester (`backtest_extreme_fvg.py`)**:
  - In `simulate_trade_execution`, if a candle breaches `stop_loss` (`c.low <= stop_loss` for Bullish or `c.high >= stop_loss` for Bearish), the trade is immediately marked `exit_reason = "STOPPED_OUT"`. Any TP crosses on that same candle cannot be counted as wins, preventing optimistic lookahead bias.
  - Multi-target independent policy resolution is preserved: if a milestone (e.g. 1R) was reached on a prior closed candle that did NOT breach SL, the 1R target policy retains its win (+1.0R) when a later candle subsequently stops out.
- **Strategy 1 Continuation (`strategy.py`)**:
  - In `phase2_check`, replace the premature `return None` with `continue` when evaluating candidate LTF FVGs with invalid SL references, allowing subsequent candidate FVGs in `candidate_indices` to be inspected.
- **Comprehensive TDD Tests**:
  - Add test cases in `test_backtest_extreme_fvg.py` explicitly asserting that same-bar collisions between SL and TP targets (1R, 2R, 3R) resolve as `STOPPED_OUT` (-1.0R), matching the live ledger.
  - Add test cases in `test_screener.py` verifying that Strategy 1 continues searching for valid LTF FVGs if an earlier candidate has an invalid SL reference.

## Capabilities

### Modified Capabilities

- `backtest-extreme-fvg`: Enforces strict Stop-Loss precedence on same-bar SL/TP collisions to match the live ledger's execution model.
- `strategy-standard`: Continues scanning subsequent candidate LTF FVGs when an earlier candidate has an invalid SL reference.

## Impact

- **Affected code**: `backtest_extreme_fvg.py`, `strategy.py`, `test_backtest_extreme_fvg.py`, `test_screener.py`.
- **Dependencies**: No new dependencies.
- **Performance**: Zero negative runtime or latency impact.
