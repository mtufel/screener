# Design: extreme-pending-touch-scoping-and-expiry

## Context

Live monitoring of the daemon exposed that the `PENDING_RETRACE` monitor in `ExtremeTradeTracker.process_live_setups` treats the 20-candle `recent_candles_map` window as if it were "evidence since the setup existed". It is not — it is a rolling market snapshot that pre-dates the setup. Any predicate evaluated over the unscoped window is answered mostly by candles that formed *before* the FVG existed, which by construction cross the FVG boundaries (the completing candle's wick defines one boundary; earlier trending candles cross the other).

## Goals / Non-Goals

**Goals**
- Pending fill/TP and breach/invalidation checks use only candles at or after the FVG's formation timestamp.
- Pending records expire when the scanner stops offering them, instead of living forever.
- Pending records track the freshest anchor/emission when the scanner re-emits with newer metadata.
- Catch-up (backfill) completions report honest entry/exit times, duration, and MFE.

**Non-Goals**
- Changing scanner discovery, ranking, lifecycle classification, or the immutable TRADE_ACTIVE ledger contract.
- Changing alert message formats or the alert-emission path in `main.py`.
- Re-architecting persistence or adding new storage fields beyond optional bookkeeping on `TrackedExtremeTrade`.

## Decisions

### D1 — Single scoping constant, applied in one place
The pending monitor computes one `window_floor = trade.ltf_fvg.get("formed_at", 0)` and builds `post_formation_candles` exactly like the active path builds `subsequent_candles` (`c_ts >= floor`). Both `recent_high`/`recent_low` (breach check) and the fill/TP predicate consume only that list. No second copy of the predicate logic is introduced; the same-bar fill+TP block and the invalidation block share the scoped extremes.

*Alternative considered:* scoping at the `main.py` caller by pre-filtering `recent_candles_map`. Rejected — the manual `/api/extreme/scan` path and future callers would silently reintroduce the bug; the tracker owns the invariant.

### D2 — Absence expiry with hysteresis (default 40 cycles)
`TrackedExtremeTrade` gains an `absent_cycles` counter (persisted for transparency). In `process_live_setups`: any pending record whose symbol is not offered by the scanner this cycle increments the counter; symbols that are offered reset it to 0. At `>= 40` consecutive absent cycles the record transitions to `INVALIDATED` with `status_detail = "Expired (setup absent from scanner for 40 cycles)"`, emits `SETUP_INVALIDATED`, and archives. 40 cycles ≈ 20 minutes at the default 30 s cadence — long enough to ride scanner flakiness (HTF cache bootstrap, transient API gaps), short enough to clear same-session rot. The threshold is a module-level constant so tests can shrink it.

*Why not time-based expiry:* cycle-count is resilient to daemon downtime (a daemon that was off for hours must not nuke all pending records on its first cycle back — and indeed the first cycle back is exactly when the scanner hasn't been asked yet; the counter only advances on cycles that actually ran a scan).

### D3 — Refresh stale pending metadata on newer emissions
When the scanner emits a setup whose `trade_id` differs from the existing pending record's `trade_id` *for the same symbol* (newer `formed_at` → new entry price → new id), the tracker treats the newer emission as authoritative for a pending (non-filled) trade: it updates the record's `trade_id`, `ltf_fvg`, `entry_price`, `stop_loss`, `risk_r`, `risk_pct`, `tp_*`, `completion_target`, `htf_anchor`, `ltf_timeframe`, and `created_at_ist` in place (keeping the same ledger slot), and logs the refresh. TRADE_ACTIVE records remain strictly immutable per the existing spec. This kills the ETH-style `[1905–1910]` anchor pairing: as soon as the scanner emits a fresher anchor/FVG pair, the pending record follows it.

*Alternative considered:* invalidate the stale record and create a new one. Rejected — produces an extra archived row and an extra Telegram alert per refresh for no informational gain; in-place refresh keeps one row per live setup.

### D4 — Evidence-derived backfill stats
When a completion (TP or same-bar fill+TP) is resolved from candle evidence rather than a live transition, the tracker sets `entry_timestamp` to the open time of the first scoped candle whose extreme touches `entry_price` (falling back to the completing candle when fill and TP share one candle), sets `closed_timestamp` to the open time of the first scoped candle reaching the target, recomputes `duration_min` from those timestamps (min 1), and recomputes `max_favorable_price`/`mfe_r` from post-fill candle extremes so `avg_mfe_r` KPIs stay truthful. Live transitions keep current behavior — `now` timestamps are correct there.

### D5 — No dedup/debounce on alerts (explicitly out of scope)
The spam was a symptom of the fake-completion loop; with D1 the loop cannot occur — a pending record can only complete once, and a re-admission requires a *new* `formed_at` emission, which is legitimately a new setup. Alert dedup would mask future state-machine bugs and is left out.

## Risks / Trade-offs

- **D2 expiry too aggressive / too lax:** mitigated by a module-level constant + boundary tests; operators retune one number.
- **D3 in-place refresh mutates `trade_id`:** acceptable for pending records (no fills to trace); `created_at_ist` update marks the refresh.
- **D4 heuristics on which candle "filled":** worst case off by one candle open when fill and TP share a candle — duration still honest to the minute for 5m/15m candles.
- **Ledger migration:** one-time manual cleanup of the 35 duplicate rows (operational step after the fix lands).

## Migration Plan

1. Land tracker changes + tests (daemon stays paused).
2. Clean `data/extreme_live_trades.json` duplicate rows (all rows created 19:38–20:00 IST; they never represented real trades).
3. Resume daemon, monitor ≥ 3 full cycles including a fresh pending emission: exactly one NEW_SETUP alert, no instant TP_HIT, floating_r live-updating.

## Open Questions

- None. The threshold (40) is tunable post-launch without a spec change — the scenario wording is "N consecutive cycles", N documented as default 40.