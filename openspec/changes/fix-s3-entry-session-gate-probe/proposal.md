## Why

`select_gated_ltf_fvg()` gates candidates with `is_entry_valid(formed_close_ts)` — the **LTF FVG
formation close** (`strategy_liquidity_sweep_fvg.py:186`).

That is the wrong predicate. The source is explicit that the constraint applies to the fill, not
the formation: *"It's completely fine for this fair value gap to form outside of the session, but
the entry has to be during the key time of day."*

Three problems follow:

1. **It rejects valid setups.** Any FVG that forms outside the session is discarded before it can
   fill in one — precisely the case the source endorses.
2. **It contradicts its own sibling gate.** The anchor-age guard 25 lines below
   (`:206-209`) correctly probes the *fill* timestamp, so the two gates disagree about which
   timestamp matters.
3. **It contradicts the backtester.** `backtest_liquidity_sweep_fvg.py` implements its own gate
   pipeline inline and tests `_in_entry_session(fill_ts, ...)` (`:438`). So every Strategy 3
   session-filtered backtest number describes a behaviour the live daemon does not implement.

Mitigating context: `extreme_trade_tracker.py` already enforces the session **at fill** and does so
correctly — on ingest of an active setup (`:551`), on the PENDING→TRADE_ACTIVE transition (`:600`),
and during pending fill detection (`:659`, `:678`, where out-of-session touches are *ignored rather
than deferred*). So this bug causes **under-trading and mis-alerting, not unauthorised entries**.

## What Changes

- **Entry-session gate probes the fill, not the formation** (`strategy_liquidity_sweep_fvg.py`):
  - `TRADE_ACTIVE` candidate → evaluate the real `fvg.entry_timestamp` (falling back to
    `fill_probe_ts_ms`).
  - `PENDING_RETRACE` candidate → the fill has not happened yet, so the session is **not yet
    decidable**. Skip the gate and let `extreme_trade_tracker` enforce it at fill, which it already
    does. `fill_probe_ts_ms` still forces evaluation for backtests/replays that know the fill time.
  - The reject counter keeps its `ENTRY_SESSION` key.
- **Docstrings updated** on `select_gated_ltf_fvg` and `get_liquidity_sweep_setup_for_symbol` to
  state that the session constraint is fill-time and is enforced downstream by the tracker.
- **TDD coverage** in `test_strategy3_entry_session_gate.py`:
  - A `PENDING_RETRACE` FVG that **forms** out-of-session **survives** the gate.
  - A `TRADE_ACTIVE` candidate whose **fill** was out-of-session is **rejected**.
  - A `TRADE_ACTIVE` candidate whose fill was in-session is **accepted**.
  - An explicit `fill_probe_ts_ms` decides the gate for a pending candidate.

## Capabilities

### Modified Capabilities

- `strategy-3-liquidity-sweep`: The entry-session gate constrains the fill timestamp rather than
  the FVG formation timestamp, matching the source model, the sibling anchor-age gate, the
  backtester, and the tracker's own fill-time enforcement.

## Impact

- **Affected code**: `strategy_liquidity_sweep_fvg.py`, `test_strategy3_entry_session_gate.py` (new).
- **Unchanged**: `extreme_trade_tracker.py` (already correct), `backtest_liquidity_sweep_fvg.py`,
  `liquidity.py`, and every Strategy 2 code path.
- **Behaviour change**: Strategy 3 will now emit setups it previously suppressed. Expect a **rise in
  alert volume** and a **rise in filled trades** for `liquidity_sweep_fvg` whenever an entry-session
  filter is enabled. Previously-reported "ENTRY_SESSION reject" counts are not comparable to
  post-fix counts.
- **Known duplication (not addressed here):** the S3 gate pipeline exists twice — inline in the
  backtester and in `select_gated_ltf_fvg` — which is the root cause of this drift. Consolidating
  them is tracked separately; see `docs/source-videos.md` §4 F-01.