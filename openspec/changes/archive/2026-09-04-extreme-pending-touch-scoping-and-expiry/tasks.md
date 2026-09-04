# Tasks: extreme-pending-touch-scoping-and-expiry

## 1. Tracker: pending monitor scoping (Bug #1/#2)

- [x] 1.1 In `extreme_trade_tracker.py`, scope the `PENDING_RETRACE` monitor to candles with `timestamp >= ltf_fvg.formed_at` (single shared scoped-candles list feeding both the same-bar fill/TP check and the breach/invalidation check).
- [x] 1.2 Add regression test: pending setup whose 20-candle window contains pre-formation candles crossing entry/target does NOT complete and does NOT invalidate (bullish + bearish variants).
- [x] 1.3 Add regression test: the exact PAXG replay (FVG formed 18:45, window contains 18:30 candle with low far below entry and later candle hitting 2R) produces zero events while the setup stays pending.

## 2. Tracker: absent-setup expiry (Bug #3)

- [x] 2.1 Add `absent_cycles` bookkeeping to `TrackedExtremeTrade` and module-level `PENDING_ABSENT_EXPIRY_CYCLES = 40`; increment on absent symbol, reset when offered, expire at threshold with distinct `status_detail`, emit `SETUP_INVALIDATED`, archive.
- [x] 2.2 Add test: pending record survives 39 absent cycles, expires on the 40th; a re-offered setup resets the counter.
- [x] 2.3 Verify daemon-restart semantics: first cycle after boot never expires records purely because the scanner was down (counter only advances on cycles that ran a scan).

## 3. Tracker: anchor/FVG refresh on newer emissions (Bug #4)

- [x] 3.1 On scanner re-emission for a symbol with an existing `PENDING_RETRACE` record under a different `trade_id` (newer `formed_at`), refresh entry/SL/targets/anchor/`ltf_fvg`/`completion_target`/`ltf_timeframe`/`created_at_ist` in place; never mutate `TRADE_ACTIVE` records.
- [x] 3.2 Add test: stale pending record (old anchor zone) is refreshed when a newer FVG emission arrives; alert metadata reflects the fresh pairing.
- [x] 3.3 Add test: `TRADE_ACTIVE` record remains immutable when the scanner offers a different setup for the same symbol.

## 4. Tracker: evidence-derived backfill stats (Bug #5)

- [x] 4.1 Derive `entry_timestamp` / `closed_timestamp` / `duration_min` / `max_favorable_price` / `mfe_r` from scoped candle evidence on catch-up completions (same-bar fill+TP path and monitor TP/SL paths).
- [x] 4.2 Add test: catch-up completion reports the touching candle as fill time and the target-touching candle as close time; `duration_min` reflects candle distance; `mfe_r` > 0 when post-fill extremes exceed the target.

## 5. Parity & cleanup

- [x] 5.1 Confirm backtest fill-candle window semantics still match live (existing tests unchanged and passing; add one parity assertion if a seam exists cheaply).
- [x] 5.2 Clean the 35 duplicate garbage rows (created 04-Sep 19:38–20:00 IST) from `data/extreme_live_trades.json`.
- [x] 5.3 Run full test suite; validate change; archive change; resume daemon and observe ≥ 3 cycles with a fresh pending emission (single NEW_SETUP alert, no instant TP_HIT).