# Design: Fill-Candle Exit Detection & Pending TP Completion Alignment

## Context
Live verification against STRATEGIES.md confirmed the live discovery state machine and the ledger's `TRADE_ACTIVE` resolution are spec-conformant, but two gaps remain (see proposal.md - Why):
1. The backtester starts forward-simulation at `candles_ltf[k + 1:]`, skipping the fill candle `k`.
2. The ledger's pre-entry monitor only invalidates `PENDING_RETRACE` trades; a pending trade that fills and completes in the same closed candle is never resolved because the scanner classifies the FVG as `COMPLETED` and stops emitting the setup.

## Goals / Non-Goals
**Goals**
- Backtest exit resolution includes the fill candle (parity with live `timestamp >= entry_timestamp` semantics).
- Ledger resolves pending same-bar fill+completion to `COMPLETED_TP` with a `TP_HIT` event.

**Non-Goals**
- No change to Finding 2.1 double-count semantics (user-accepted as designed).
- No change to backtest TP-before-SL same-bar order: it matches the ledger's TRADE_ACTIVE resolution (`extreme_trade_tracker.py` checks TP first via `if effective_high >= target_tp` / `elif effective_low <= stop_loss`) and STRATEGIES.md Step 6 ordering.
- No change to `evaluate_ltf_setup_lifecycle` (live discovery) — verified correct.

## Decisions
1. **Single-slice fix in `run_extreme_backtest`**: change `subsequent = candles_ltf[k + 1:]` to `subsequent = candles_ltf[k:]`. `simulate_trade_execution` already evaluates each candle's high/low against TP and SL levels without assuming entry fill timing within the bar, so passing the fill candle is safe and mirrors the live ledger exactly. Alternative (recomputing TP/SL on the fill candle inside the loop) rejected — duplicates resolution logic.
2. **Pending completion check in the pre-entry monitor branch**: inside the `PENDING_RETRACE` block, before the invalidation check, evaluate `min(curr_px, recent_low)` / `max(curr_px, recent_high)` (Bullish: low <= entry AND high >= target_tp; Bearish mirrored). On completion: set state `COMPLETED_TP`, `realized_r` to target multiplier, `status_detail` `TP <target> HIT (+X.R)`, stamp `entry_filled_at_ist`/`entry_timestamp` and `closed_at_ist`/`closed_timestamp`, append `("TP_HIT", trade)` to `to_close`, and `continue`. This mirrors the field updates of the `PENDING_RETRACE -> TRADE_ACTIVE` transition plus the `TRADE_ACTIVE` TP-hit block. Invalidation keeps priority when both could match (SL/anchor breach is the stronger signal that the fill never happened favorably).
3. **Regression tests**: synthetic candles (no network) — backtest test asserts a fill candle that reaches TP2 yields `hit_2r=True`; tracker test asserts a pending trade completes via a single touch+TP candle when no setup is emitted, landing in history as `COMPLETED_TP`.

## Risks / Trade-offs
- [Backtest stats shift] Fixing the fill-candle skip changes historical reports vs prior runs → Expected: shortens detected trade durations and may flip some 1R/2R/3R hits; this is corrected behavior, not a regression.
- [Pending TP on anchor breach candles] A candle that spans both entry and target while also breaching the anchor completes rather than invalidates → Acceptable: touch+target in one candle is the spec-defined fill+completion; invalidation still wins where SL/anchor breach occurs without the entry touch.

## Migration Plan
Pure in-process logic change; no persistence migration (trade schema unchanged). Rollback = revert the two code sites.

## Open Questions
None.
