## Tasks

- [x] 1. Map both implementations rule-by-rule and confirm which shared predicates already
      existed (`check_gap_band`, `check_fresh_sweep`, `check_anchor_age`) versus which were inlined
      in the backtester (gap %, entry session, entry weekday, anchor age).
- [x] 2. Identify the one substantive rule divergence beyond duplication: the backtester
      tie-broke extreme selection on `formed_at`; the live path did not.
- [x] 3. Add `evaluate_formation_gates()` — shared phase-1 gate (gap band + fresh sweep), the two
      rules decidable at FVG formation.
- [x] 4. Add `evaluate_fill_gates()` — shared phase-2 gate (entry session + weekday + anchor age) at
      the fill timestamp. Calls `SessionFilterConfig.is_entry_valid()` as the single decision
      function and only *classifies* a failure for the reject counter, so no rule is reimplemented.
- [x] 5. Add `select_extreme_gated_fvg()` — the single selection rule including the `formed_at`
      tie-break, carrying sweep metadata through untouched.
- [x] 6. Rewrite `select_gated_ltf_fvg()` (live) to delegate to all three.
- [x] 7. Delete the backtester's inline `_passes_gates()`, the `_in_entry_session()` helper, its
      inlined weekday / anchor-age / gap-band predicates, the unused `has_fresh_sweep` import, and
      its duplicate local `DEAD_ZONE_DEFAULT` (now imported from the engine).
- [x] 8. Fix **F-06**: `check_fresh_sweep()` hardcoded `timeframe="5m"` when building pools, so
      non-5m runs gated on the wrong candle series. It now takes `ltf_timeframe` and both callers
      pass their real LTF.
- [x] 9. Reject keys unify: backtester `ENTRY_SESSION_FILL` → `ENTRY_SESSION`. No test depended on
      the old key.
- [x] 10. Add TDD coverage in `test_strategy3_gate_pipeline.py` (25 tests) across the gap band,
      formation gates, fill gates, extreme selection, and pool-construction lookahead.
- [x] 11. Full suite green — 469 passed, no existing test modified.

## Measured effect

S3 backtest, 90d, 5m, close invalidation — before vs after consolidation:

| Symbol | n | Net R | PF | maxDD |
|---|---|---|---|---|
| BTC | 28 → **30** | +8.16 → **+8.65** | 1.58 → 1.58 | 3.9 → 3.9 |
| ETH | 43 → 43 | +8.79 → +8.79 | 1.38 → 1.38 | 4.0 → 4.0 |
| SOL | 48 → 48 | +16.68 → +16.68 | 1.73 → 1.73 | 5.0 → 5.0 |

Effectively behaviour-preserving; the BTC delta is +0.49R on a 90-day window.

## Lookahead removed (see `docs/source-videos.md` F-12)

The backtester's private `pools_asof()` built pool templates from the **entire** series.
`build_pool_templates` derives its cluster tolerance from the mean close of the last 50 bars it is
handed (`liquidity.py:239`), so for a candidate 45 days back that tolerance came from future
prices — measured **31.7% too wide**, merging 619 swing clusters where the correct as-of view had
150. Consolidating on `find_liquidity_pools()` (which truncates to the as-of time first) removed it.

`test_pool_construction_has_no_lookahead` now pins the invariant: appending later bars must not
change an as-of pool view.

## Follow-up

`pools_asof()` is still used by the backtester for **take-profit** pool resolution
(`backtest_liquidity_sweep_fvg.py:454`). It has the same tolerance-derivation exposure and should
be retired in favour of `find_liquidity_pools(..., now_ms=fill_ts)`. Tracked in F-12.