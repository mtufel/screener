## Why

The Strategy 2 (Extreme LTF FVG) engine is underperforming in live use because it runs the
worst configuration discovered by research: **5m + wick invalidation + no session filter**,
which backtests show is a consistent loser (−86R net across BTC/ETH/SOL over 60 days). Research
consistently showed that close invalidation, a NY-session filter, and new bias filters
(distance from the 4H zone, momentum impulse candle, gap ceiling, and an age-of-FVG ceiling)
materially improve edge — but those winning settings were never wired into the live defaults.

## What Changes

- **Live default config fixes (BREAKING runtime behavior):** switch `EXTREME_USE_CLOSE_INVALIDATION`
  to `true` (close invalidation beats wick in every backtest) and enable the NY session filter
  (`EXTREME_SESSION_FILTER_ENABLED=true`, `EXTREME_SESSIONS=NY`) for LTF FVG formation.
- **New bias filters (configurable, off-by-default or default-on where robust):**
  - **Distance-from-4H cap** (~2%): reject LTF FVGs whose top/bottom is more than ~2% outside the
    closest boundary of the selected 4H anchor zone (removes a strongly negative −15R dead zone).
  - **Momentum impulse-candle filter**: require the LTF FVG impulse candle (c2) to be a strong,
    in-trade-direction body (body ≥ ~50% of candle range), dropping weak-impulse setups.
  - **Gap ceiling** (~0.3%): reject oversized LTF FVG gaps while keeping the existing 0.05% floor.
  - **Age-of-FVG ceiling** (~20–40 LTF candles after the 4H first touch): drop stale, decayed
    imbalances. **Explicitly not** a "freshest-is-best" filter — capping below ~10 candles adds noise.
- **Target stays 2R** — the video's "target 3R" advice is empirically worse (see research, section 7);
  do not switch the completion target.
- **No change to LTF selection rule** — "first FVG" vs "deepest/extreme" selection is a wash
  (they pick the same trade); the existing extreme ranking stays.
- **Symbol policy**: reconsider ETH in the live whitelist (structurally weaker: even the best stack
  leaves it at PF ~0.88), or make bias thresholds per-symbol.

## Capabilities

### New Capabilities
- `strategy-biases`: Introduces configurable trade-selection bias filters (distance-from-4H,
  momentum impulse, gap ceiling, and LTF-FVG age ceiling) shared by the live screener engine,
  trade tracker, and backtester, with full runtime configuration and dashboard sync.

### Modified Capabilities
- `strategy-2-extreme`: Update the LTF FVG selection requirement to (a) default the invalidation
  mode and session filter to the research-backed winning values, and (b) apply the new bias
  filters during post-touch LTF FVG discovery/ranking.

## Impact

- **Code**: `strategy_extreme_fvg.py` (bias filter application in `get_extreme_setup_for_symbol`
  / `find_unmitigated_ltf_fvgs` / `select_extreme_ltf_fvg`), `backtest_extreme_fvg.py`
  (backtest the new filters), `live_screener_extreme.py`, `main.py` (env config, runtime state,
  dashboard sync), `.env.example`.
- **Config**: new env vars (`EXTREME_MAX_DIST_FROM_4H_PCT`, `EXTREME_REQUIRE_MOMENTUM`,
  `EXTREME_MAX_GAP_PCT`, `EXTREME_MAX_LTF_FVG_AGE_CANDLES`) and flipped defaults for existing
  invalidation/session vars.
- **Tests**: new unit + backtest coverage for each bias filter (existing tests remain SOT).
- **Docs/specs**: delta spec for `strategy-2-extreme`; design + tasks below.
