## Context

The deployed Strategy 2 Extreme engine runs the **worst** discovered configuration: 5m + wick invalidation + no session filter (−86R net across BTC/ETH/SOL in 60 days). Research backtesting (see `strategy_research_findings.md`) quantifies the specific, actionable fixes. The user's target is the Atif Hussain "4H FVG Strategy" from YouTube; its rules (4H anchor → LTF FVG entry → SL at forming-candle extreme → 2R) largely match the existing engine, but the live defaults lack the quality gates that research proves matter.

## Goals / Non-Goals

**Goals:**
- Flip the three broken live defaults: close invalidation on, session filter on, LTF timeframe stays 5m.
- Wire in the four new bias filter parameters (`max_dist_from_4h_pct`, `require_momentum`, `max_gap_pct`, `max_ltf_fvg_age_candles`) — configurable, with sensible defaults from backtesting.
- Ensure the backtester (`backtest_extreme_fvg.py`) and the live engine (`live_screener_extreme.py` / daemon) use identical filter logic.
- **Keep the completion target at 2R**; the video's 3R advice is empirically worse (−49R vs +2R aggregate).
- Do not change the "extreme/deepest" LTF FVG selection rule — it is a wash vs "first-FVG" and the existing code is correct.

**Non-Goals:**
- No per-symbol hard-coded thresholds (bias filters are configurable per coin).
- No new chart indicators or Telegram changes.
- No changes to the existing immutable-trade-ledger resolution logic (SL-first precedence is already correct).
- Do not force a 3R target (research clearly rejects it).

## Decisions

### 1. Default-flip: close invalidation ON, session filter ON

The current defaults are the losing configuration. Flipping them is a one-line change in `main.py` that also requires updating the spec (delta spec) and `.env.example`. No structural changes needed.

```python
# main.py defaults — BEFORE
EXTREME_USE_CLOSE_INVALIDATION = os.getenv("EXTREME_USE_CLOSE_INVALIDATION", "false")...
EXTREME_SESSION_FILTER_ENABLED = os.getenv("EXTREME_SESSION_FILTER_ENABLED", "false")...

# AFTER
EXTREME_USE_CLOSE_INVALIDATION = os.getenv("EXTREME_USE_CLOSE_INVALIDATION", "true")...
EXTREME_SESSION_FILTER_ENABLED = os.getenv("EXTREME_SESSION_FILTER_ENABLED", "true")...
EXTREME_SESSIONS = os.getenv("EXTREME_SESSIONS", "NY")...
```

### 2. Bias filter parameters: where they live and how they flow

The four new parameters are simple scalar floats/ints. They flow identically to how `min_gap_pct` and `session_filter` already do:

```
.env
  → main.py: EXTREME_MAX_DIST_FROM_4H_PCT, EXTREME_REQUIRE_MOMENTUM, EXTREME_MAX_GAP_PCT, EXTREME_MAX_LTF_FVG_AGE_CANDLES
  → strategy_extreme_fvg.py: passed into find_unmitigated_ltf_fvgs() and applied in select_extreme_ltf_fvg() as a post-pool filter.
  → live_screener_extreme.py: ScreenerConfig constructor.
  → backtest_extreme_fvg.py: argparse + env-var resolution + passed to run_extreme_backtest().
  → API state dict + frontend JS (read-only display of filter values).
```

No new dataclass fields or Redis schema changes are needed — these are just extra arguments in existing function call chains.

### 3. Applying bias filters in the strategy engine

The cleanest insertion point is in `find_unmitigated_ltf_fvgs` (which already loops over candidate LTF FVGs). After building the `unmitigated` list but before returning, filter by:

- **Distance**: `(fvg.bottom - anchor.bottom) / anchor.bottom * 100 > max_dist_pct` for Bullish (and the inverse for Bearish). Skip FVG if rejected.
- **Momentum**: inspect `c2.running_body / c2.range >= 0.5` and direction matches.
- **Gap ceiling**: `fvg.gap_pct > max_gap_pct` → reject.
- **Age ceiling**: `(c3_close_index - touch_index) > max_ltf_fvg_age_candles` → reject.

All four are simple scalar comparisons on data already in the `FVG` dataclass. No new data structures.

```python
# In find_unmitigated_ltf_fvgs — after building unmitigated list:
unmitigated: List[FVG] = []
for i in range(...):
    # ...existing gap/min logic...
    # Apply bias filters BEFORE adding to unmitigated:
    if max_dist_from_4h_pct < 999.0:
        dist = ...
        if dist > max_dist_from_4h_pct: continue
    if require_momentum and not _is_strong_momentum(cand):
        continue
    if max_gap_pct < 999.0 and cand.gap_pct > max_gap_pct:
        continue
    unmitigated.append(cand)
```

### 4. Backtest parity

`backtest_extreme_fvg.py`'s `run_extreme_backtest` gets the same four new args and applies the identical filter logic inside its LTF FVG scan loop (same pattern as the live engine). This guarantees backtest-to-live fidelity for the new parameters.

### 5. Default values for the new bias parameters

From backtesting (marginal analysis, BTC 60d 5m, close invalidation):
- `max_dist_from_4h_pct = 2.0` — eliminates the −15R "2–5% dead zone" while keeping 90%+ of trade volume.
- `require_momentum = True` — the impulse-candle filter has clean marginal signal (+27R vs −9R) and is robust in confirm.
- `max_gap_pct = 0.3` — drops the 0.3–0.6% losers (-6R) while keeping the 0.05–0.1% winners.
- `max_ltf_fvg_age_candles = 40` — per-trade quality improves (PF up), trades cut ~75%. This is a user-facing dial (not a hard default) — expose it but default to a permissive value.

### 6. Existing test suite

The existing 87 tests in the repo are the SOT and MUST NOT be modified. New test coverage for the bias filters will be added in a new test file (`test_extreme_fvg_bias_filters.py`) — these are additive only.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Changing live defaults (close invalidation) alters the active-4H-FVG cache, potentially changing which zones are active. | Run the full pytest suite; compare live daemon output with existing behaviour on a known historical window. |
| Over-filtering with all bias gates on reduces trade count dramatically (1,916 → ~450). | Make bias parameters individually configurable with lenient defaults. Users can dial them in. |
| ETH is structurally weak in all variants. Adding filters does not fix it. | Recommend making ETH a lower-priority or opt-in symbol; document the weakness. |
| The age ceiling (40 candles) with session filter cuts the already-small ETH sample to ~18 trades. | Cap the ceiling only when session filter is on; let them be independent. |

## Migration Plan

1. **Branch**: `git checkout -b feat/extreme-fvg-bias-filters` off `develop`.
2. **Config defaults** in `main.py` + `.env.example` (one commit).
3. **Bias filter params** through the call chain: env → `strategy_extreme_fvg.py` → `find_unmitigated_ltf_fvgs` (one commit per layer).
4. **`backtest_extreme_fvg.py`** bias params — wire through to simulation loop.
5. **Live screener** bias config display (UI reads-only; bias values shown in the setup card).
6. **New tests** (`test_extreme_fvg_bias_filters.py`) — run in CI.
7. **`pytest -v`** — all 87 existing tests pass; new tests pass.
8. **PR to `develop`**; once merged, update `.env` on the live server.

**Rollback**: revert the env-default lines in `main.py` and `.env.example` — no schema or data migration needed.