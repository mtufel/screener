# Proposal: extreme-pending-touch-scoping-and-expiry

## Why

A monitored live run of the daemon (2026-09-04, 19:38–20:00 IST) surfaced five production bugs, all rooted in how the live tracker interprets the `recent_candles_map` window (last 20 closed LTF candles) during `PENDING_RETRACE` monitoring:

1. **Fake same-bar completion loop (critical, live):** The pending monitor's fill/TP check scans the whole 20-candle window without restricting to candles formed after the FVG. Pre-formation candles always cross the entry level (the zone boundary *is* defined by their extremes), so every newly emitted `PENDING_RETRACE` setup is instantly "filled + TP-completed", archived, and re-alerted on the next cycle. Observed: **35 duplicate `COMPLETED_TP` records + ~70 Telegram alerts** for one PAXG setup in 22 minutes.
2. **Fake invalidations (latent, live):** The same unscoped window feeds the pending breach check (`recent_high`/`recent_low`), so a pre-formation extreme can invalidate a perfectly healthy pending setup the moment the scanner briefly stops emitting it.
3. **Stale pending expiry never implemented (live, 26+ h):** `PENDING_RETRACE` records persist indefinitely when the anchor stays quiet — BTC/ETH/SOL records from 03-Sep are still "waiting" with `floating_r: 0.0` forever. `docs/strategy2-findings.md` (2.2) flagged absence-based expiry; only breach-based invalidation was implemented.
4. **Stale-anchor pairings in the ledger (live):** The ETH record pairs a 03-Sep 15m FVG (entry 2399.3) with a 19-Aug 4H anchor `[1905–1910]` — an anchor from a different price era. Anchor selection cannot return a fresh anchor while a stale pending record exists, so invalidation geometry and alert charts are wrong for the trade's whole life.
5. **Degenerate backfill timestamps (cosmetic, live):** Catch-up completions carry `duration_min: 1`, `entry_filled_at_ist = closed_at_ist = created_at_ist`, and `mfe_r: 0.0` despite `max_favorable_price` proving the full move — corrupting `duration_min`/`avg_mfe_r` KPIs whenever the daemon starts after the market already resolved the trade.

Daemon was paused via `/api/extreme/toggle-daemon?enable=false` to stop the alert spam; ledger had grown to 35 garbage history rows.

## What Changes

* **Pending monitor window scoping:** `ExtremeTradeTracker.process_live_setups` must scope *all* pending-monitor candle checks (fill/TP and breach/invalidation) to candles with `timestamp >= ltf_fvg.formed_at`, mirroring the `timestamp >= entry_timestamp` scoping the `TRADE_ACTIVE` path already applies.
* **Absent-setup expiry:** A `PENDING_RETRACE` record whose scanner setup is absent for N consecutive cycles (default 40 ≈ 20 min at 30 s cadence) transitions to `INVALIDATED` with a distinct `status_detail`, emits `SETUP_INVALIDATED`, and archives.
* **Fresh-anchor refresh on re-emission:** When the scanner re-emits a setup for a symbol whose pending record references an older FVG `formed_at` (or a different anchor zone), the tracker refreshes the pending record's anchor/target metadata to the latest emission instead of keeping the stale pairing.
* **Honest backfill stats:** Catch-up resolutions must derive fill/exit times and MFE/duration from candle evidence (first touching candle's timestamp, actual post-fill extreme) instead of stamping `duration_min: 1` / `mfe_r: 0.0`.
* **Backtest parity:** The backtest fill-candle evaluation window is re-verified against the now-scoped live semantics (no behavioral change expected; regression tests assert parity).
* No changes to scanner discovery, `evaluate_ltf_setup_lifecycle`, target matrix, or TP-before-SL ordering.

## Impact

* **Affected specs:** `strategy-2-extreme` (Modified: *Immutable Active Trade Ledger & Stale Pending Invalidation*; Added: *Pending Monitor Candle Scoping*; Added: *Stale Pending Expiry & Anchor Refresh*).
* **Affected code:** `extreme_trade_tracker.py` (pending monitor scoping, expiry counter, anchor refresh, backfill stats), `main.py` (nothing structural — daemon cycle already passes candles; alert path unchanged), tests for both tracker and backtest parity.
* **Operational:** Ledger requires one-time cleanup of the 35 duplicate garbage rows; daemon can be resumed after fix lands.