## Tasks

- [x] 1. Confirm scope: the tracker (`extreme_trade_tracker.py:551`, `:600`, `:659`, `:678`)
      already enforces the entry session **at fill**, and does so correctly — out-of-session
      touches are ignored, not deferred. So the defect causes under-trading and mis-alerting, not
      unauthorised entries.
- [x] 2. Confirm the backtester was already correct
      (`backtest_liquidity_sweep_fvg.py:438` uses `fill_ts`), so the gap was live-only.
- [x] 3. Confirm the sibling anchor-age gate (`:206-209`) already probes the fill — the two gates
      disagreed about which timestamp mattered.
- [x] 4. Add failing TDD coverage in `test_strategy3_entry_session_gate.py` (6 tests): pending
      out-of-session formation survives; active out-of-session fill is rejected; active in-session
      fill is accepted; explicit `fill_probe_ts_ms` decides pending candidates; `ALL` is inert;
      survivors never carry an out-of-session entry timestamp.
- [x] 5. Fix `select_gated_ltf_fvg()` to probe the fill: `TRADE_ACTIVE` uses
      `fvg.entry_timestamp`, `PENDING_RETRACE` defers to the tracker, `fill_probe_ts_ms` overrides
      both.
- [x] 6. Update the `select_gated_ltf_fvg()` docstring to state the fill-time semantics and the
      tracker's downstream enforcement.
- [x] 7. Run the full suite — 444 passed, no existing test modified.
- [x] 8. Quantify impact on real data (16 as-of snapshots, 45d, BTC/ETH/SOL, `entry_sessions=NY_KZ`):
      candidates surviving the gate rise **15 -> 43 (+187%)**; the old gate rejected 44 of 59
      candidates (75%), the fill-time gate rejects 16 (27%). See `docs/source-videos.md` F-01.
- [x] 9. Update `docs/source-videos.md` (F-01 → FIXED) and `STRATEGIES.md`.

## Follow-up (not in this change)

The S3 gate pipeline is implemented **twice** — inline in `backtest_liquidity_sweep_fvg.py` and in
`strategy_liquidity_sweep_fvg.select_gated_ltf_fvg()`. `select_gated_ltf_fvg()` has exactly one
caller (the live path), and `fill_probe_ts_ms` is passed by nobody, so the two implementations can
drift silently again — which is precisely how this bug arose. Consolidating them onto one shared
pipeline should be its own change.