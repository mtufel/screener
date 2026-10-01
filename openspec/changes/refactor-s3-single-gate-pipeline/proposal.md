## Why

The Strategy 3 gate pipeline is implemented **twice**, and the copies have already drifted twice:

- `strategy_liquidity_sweep_fvg.select_gated_ltf_fvg()` — the live scanner path. One caller.
- `backtest_liquidity_sweep_fvg.py` — its own inline pipeline inside the fill loop.

Neither is reachable from the other. `select_gated_ltf_fvg()` has exactly one caller (the live
path) and `fill_probe_ts_ms` is passed by nobody, so the two implementations share no code and
cannot signal each other's drift. That is precisely how these shipped:

- **F-01** — live gated the entry session on FVG *formation* time while the backtester used
  *fill* time, so every session-filtered S3 backtest number described behaviour live never had.
- **F-10** — `tp_mode` was inert in the backtester while the live engine honoured it.

Every duplicated rule is a rule that can diverge again:

| Rule | Live implementation | Backtester implementation |
|---|---|---|
| Gap band | `check_gap_band(fvg, band)` | inlined `(top-bottom)/mid*100` range test |
| Fresh sweep | `check_fresh_sweep(...)` | inlined `has_fresh_sweep(...)` |
| Entry session | `SessionFilterConfig.is_entry_valid` | `_in_entry_session()` wrapper over `is_in_session` |
| Entry weekday | inside `SessionFilterConfig` | inlined `is_weekday(fill_ts)` |
| Anchor age | `check_anchor_age(...)` | inlined `lo <= age < hi` |
| Extreme selection | `min(bottom)` / `max(top)` | `min((bottom, formed_at))` / `max((top, -formed_at))` |

The last row is a subtle one: the backtester tie-breaks on `formed_at`, the live path does not.
With equal prices the two can select different candidates.

## What Changes

The pipeline's **ordering** legitimately differs and stays, because a backtest must simulate the
fill before the fill-time gates are decidable, while live cannot know a future fill. What must
not differ is the **rules**. So the gates are consolidated by phase, and both callers evaluate the
same functions:

- **New shared phase-1 gate** `evaluate_formation_gates()` in `strategy_liquidity_sweep_fvg.py` —
  gap band + fresh sweep, the two gates decidable at FVG formation. Returns the existing
  `SweepGateResult`.
- **New shared phase-2 gate** `evaluate_fill_gates()` — entry session + entry weekday + anchor age,
  all at the fill timestamp. Returns a reject key or `None`. It calls
  `SessionFilterConfig.is_entry_valid()` as the single decision function and only *classifies* the
  failure for the counter, so no rule is reimplemented.
- **New shared selector** `select_extreme_gated_fvg()` — the one extreme-selection rule, including
  the `formed_at` tie-break, so both callers agree on which candidate wins.
- **`select_gated_ltf_fvg()`** (live) delegates to all three.
- **`backtest_liquidity_sweep_fvg.py`** deletes its inline `_passes_gates()`, its
  `_in_entry_session()` helper, and its inlined weekday / anchor-age / gap-band predicates, and
  calls the same three functions instead.
- **Reject-counter keys unify.** The backtester's `ENTRY_SESSION_FILL` becomes `ENTRY_SESSION`,
  matching the live path. No test depended on the old key.

Also folded in, because it is a one-line consequence of touching `check_fresh_sweep()`:

- **F-06 fix** — `check_fresh_sweep()` hardcoded `timeframe="5m"` when building the liquidity map,
  so any run with `ltf_timeframe != "5m"` silently gated on the wrong series. It now takes an
  `ltf_timeframe` argument and both callers pass their real LTF. Defaults to `"5m"`, so 5m
  behaviour is unchanged.

## Ordering: documented, not accidental

The two-phase split makes the one legitimate difference explicit in code comments:

| Phase | Gates | Backtester | Live |
|---|---|---|---|
| 1 — formation | gap band, sweep | filter pool → select → gate | filter per candidate |
| 2 — fill | entry session, weekday, anchor age | after simulating the fill | at fill, else deferred to `extreme_trade_tracker` |

## Capabilities

### Modified Capabilities

- `strategy-3-liquidity-sweep`: A single shared gate pipeline evaluates every Strategy 3 gate and
  the extreme-selection rule for both live and backtest, so the two paths cannot drift.

## Impact

- **Affected code**: `strategy_liquidity_sweep_fvg.py`, `backtest_liquidity_sweep_fvg.py`,
  `test_strategy3_gate_pipeline.py` (new).
- **Behaviour change (backtester only):** `ENTRY_SESSION_FILL` reject key is renamed
  `ENTRY_SESSION`; S3 liquidity pools are now built with the real `ltf_timeframe` instead of a
  hardcoded 5m (affects non-5m runs only). Live Strategy 2 paths are untouched.
- **No behaviour change** to live trading, and none to 5m backtests.
- **Regeneration advised:** S3 reports on non-5m timeframes were gated on the wrong candle series
  and should be re-run.