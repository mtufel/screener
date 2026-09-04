# Change: Fill-Candle Exit Detection & Pending TP Completion Alignment

## Why
Live verification of Strategy 2 found one live exit-detection gap and one engine divergence: the backtester skips the entry/fill candle when resolving exits (mis-detected same-bar TP/SL), while a `PENDING_RETRACE` trade that fills and completes in the same candle is never resolved by the ledger.

## What Changes
* **Backtest Fill-Candle Evaluation**: `run_extreme_backtest` now passes the fill candle itself (`candles_ltf[k:]` instead of `candles_ltf[k + 1:]`) into `simulate_trade_execution`, matching the live discovery engine and ledger which both evaluate `timestamp >= entry_timestamp`.
* **Pending TP Completion**: `ExtremeTradeTracker` pre-entry monitor now also resolves `PENDING_RETRACE` trades to `COMPLETED_TP` when a single closed candle touches entry and reaches the completion target (`fill + TP same-bar`), emitting `TP_HIT` and archiving to history instead of leaving a stale pending record.
* No changes to live `evaluate_ltf_setup_lifecycle` (verified spec-conformant), no changes to 2.1 double-count semantics, no changes to backtest TP-before-SL order (matches ledger and STRATEGIES.md).

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `strategy-2-extreme`: Backtest forward-simulation MUST include the entry/fill candle in exit resolution; the ledger MUST resolve pending trades that fill and complete within one closed candle.

## Impact
* `backtest_extreme_fvg.py` (`run_extreme_backtest` fill-candle slice).
* `extreme_trade_tracker.py` (`process_live_setups` pre-entry monitor branch).
* Tests: `test_backtest_extreme_fvg.py`, `test_extreme_trade_tracker.py` (2 new regression tests).
