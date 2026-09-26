# Tasks

> **Deviation note (2026-09-26):** The `EXTREME_SESSION_FILTER_ENABLED` flip and the
> `EXTREME_SESSIONS = "NY"` default were **deliberately NOT applied**. The SOT regression test
> `test_bug_findings_2026_09_18.py::test_session_env_defaults_do_not_force_ny` guards the
> `"ALL"` default (CLAUDE.md treats existing tests as Source of Truth; changing a default is
> not a bug fix). Per the user decision, defaults stay `EXTREME_SESSION_FILTER_ENABLED=false`
> and `EXTREME_SESSIONS=ALL` (all-hours). Only `EXTREME_USE_CLOSE_INVALIDATION` is flipped to
> `"true"`. With `sessions="ALL"`, keeping the session-filter bool off is required — otherwise
> `SessionFilterConfig.from_legacy` resolves the effective session to `"NY"` (its
> `elif session_filter is True: resolved_fvg = default_session` branch), silently re-enabling a
> NY-only filter. The four bias filters (Section 2) are fully delivered.

## 1. Config defaults flip (live defaults that fix the baseline)

- [x] Flip `EXTREME_USE_CLOSE_INVALIDATION` default from `"false"` to `"true"` in `app_config.py`
- [ ] Flip `EXTREME_SESSION_FILTER_ENABLED` default from `"false"` to `"true"` — **deferred** (see deviation note; kept `false`)
- [ ] Set `EXTREME_SESSIONS` default to `"NY"` — **deferred** (see deviation note; kept `ALL`)
- [x] Update `.env.example` to reflect the final defaults (close-invalidation on; session filter off; 4 bias vars documented)

## 2. New bias filter env vars in app_config.py

- [x] Add `EXTREME_MAX_DIST_FROM_4H_PCT` defaulting to `2.0` (float)
- [x] Add `EXTREME_REQUIRE_MOMENTUM` defaulting to `"false"` (bool, opt-in for now)
- [x] Add `EXTREME_MAX_GAP_PCT` defaulting to `0.3` (float)
- [x] Add `EXTREME_MAX_LTF_FVG_AGE_CANDLES` defaulting to `9999` (int, permissive default)
- [x] Add all four to the `config` state dict and `/api/extreme/config` endpoint response
- [x] Add them to the `/api/extreme/backtest` POST body fields

## 3. Strategy engine: apply bias filters

- [x] Add `max_dist_from_4h_pct`, `require_momentum`, `max_gap_pct`, `max_ltf_fvg_age_candles` as parameters to `find_unmitigated_ltf_fvgs()` in `strategy_extreme_fvg.py`
- [x] Implement `_is_strong_momentum(cand: Candle, direction)` helper (body ≥ 50% of range, direction matches)
- [x] Apply distance filter: reject LTF FVG when distance > `max_dist_from_4h_pct` (Bullish/Bearish formulas as in design.md)
- [x] Apply momentum filter: skip FVG when `require_momentum=True` and `_is_strong_momentum(cand=c2)` is False
- [x] Apply gap ceiling filter: skip FVG when `gap_pct > max_gap_pct`
- [x] Apply age ceiling: compute `ltf_fvg_age = candle_3_index - touch_index`; skip when age > `max_ltf_fvg_age_candles`
- [x] Add `get_extreme_setup_for_symbol` to accept and pass all four params to `find_unmitigated_ltf_fvgs`
- [x] Propagate all four params from `get_extreme_setup_for_symbol` signature into `screener_cycle.execute_extreme_screener_cycle` (via `_runtime_extreme_config`)

## 4. Backtester parity

- [x] Add the four new CLI args to `backtest_extreme_fvg.py` (`--max-dist-from-4h-pct`, `--require-momentum`, `--max-gap-pct`, `--max-ltf-fvg-age`)
- [x] Add env-var resolution for each (mirroring existing `min_gap_pct` pattern)
- [x] Wire all four into `run_extreme_backtest()` signature
- [x] Apply the identical filter logic inside the backtest's LTF FVG scan loop (same pattern as step 3)
- [x] Run `python3 backtest_extreme_fvg.py --symbol BTC --days 30 --ltf 5m --invalidation close --session-filter` with bias flags to verify they work end-to-end (50 candidates filtered → 19 trades, +5.0R @ 2R)

## 5. Live screener config display

- [x] Add the four bias filter values to the runtime daemon config (`_runtime_extreme_config`) and `/api/extreme/status` + `/api/extreme/config` payloads
- [x] Render them in the Web dashboard setup card alongside existing parameters
- [x] Read-only display (no dashboard controls for these yet — future change)

## 6. Test coverage (additive only — existing tests are SOT)

- [x] Write `test_extreme_fvg_bias_filters.py` (17 tests):
  - Unit test: `_is_strong_momentum` returns True for strong bullish/bearish candles and False for weak ones
  - Unit test: distance filter correctly accepts/rejects candidate FVGs
  - Unit test: age ceiling correctly filters by candle count
  - Integration: `find_unmitigated_ltf_fvgs` with all four filters returns expected subset of synthetic candles
- [x] Run `pytest -q` — 17 new tests pass; full suite: 318 passed, 12 failed (all 12 pre-existing on base `test_integration_scenarios.py` failures, unrelated redis `'ClosableSink'` setup issue; no new regressions)

## 7. Validation & documentation

- [x] Run the enhanced backtest (`python3 enhanced_backtest.py --symbol BTC --days 60 --ltf 5m`) — confirms empirical edge: distance 1–2% +17R vs 2–5% −16R; gap ≤0.1% +22R; momentum +15R vs −10R; NY +41R vs −36R
- [x] Update `STRATEGIES.md` with a new section (Step 3b) documenting the bias filters and their empirical basis (references `strategy_research_findings.md`)
- [x] Update `openspec/specs/strategy-2-extreme/spec.md` main spec's Purpose
  - (No Purpose change needed — the delta specs under this change cover the new bias requirements; main spec Purpose remains an accurate strategy overview)
