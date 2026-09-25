# Design: Strategy 3 — Video FVG (4H Anchor + LTF Entry)

See proposal.md for the "why". This document covers how.

## Context

The `feat/strategy-extensibility` change delivered a pluggable strategy framework: `BaseStrategy` ABC, name-keyed registry, `Strategy2Extreme` adapter, and strategy-agnostic daemon orchestration in `screener_cycle.py`. Strategy 3 must plug into this framework without modifying core orchestration.

Existing infrastructure to reuse:
- `strategies/base.py` — `BaseStrategy` ABC with `name`, `default_params`, `resolve_params()`, `find_setups()`, `backtest()`
- `strategies/registry.py` — `@register` decorator, `get_strategy(name)`, `list_strategy_names()`
- `screener_cycle.py` — `execute_extreme_screener_cycle()` with `strategy.find_setups(sym, provider, params)`
- `extreme_trade_tracker.py` — `TrackedExtremeTrade` with `strategy` + `strategy_params` fields
- `backtest_extreme_fvg.py` — backtest report dataclass (`BacktestReport`) and `get_historical_candles_range()` for data
- `_find_fvg()` — existing LTF FVG detection function in Strategy 2's engine (can be parameterized for gap threshold)
- `get_historical_candles()` — multi-provider candle fetch used by both backtest and daemon

## Goals

- Implement the 4H anchor + LTF entry system as described in the video source transcript.
- Plug into the existing `BaseStrategy` framework with zero changes to core orchestration.
- Produce a backtest report identical in shape to Strategy 2's `BacktestReport` so the ledger and dashboard work without modification.
- Register with `name="video_fvg"` so `/api/video_fvg/backtest`, `/api/video_fvg/status`, `/api/video_fvg/info` auto-exist via the strategy-parameterized API routes added by `strategy-extensibility`.
- Capture full two-timeframe context (4H anchor + LTF FVG) in the trade record for post-archive analysis.

## Non-Goals

- Do not modify Strategy 2 or the `strategy_extreme_fvg.py` engine.
- Do not create a separate ledger or trade tracker — Strategy 3 shares `TrackedExtremeTrade`.
- Do not re-implement candle fetch, rate limiting, or provider abstraction — reuse the existing data layer.

## Decisions

### D1: New engine module `strategy_video_fvg.py` (not shared with Strategy 2)

**Decision:** `strategy_video_fvg.py` is a self-contained engine with its own setup detection and backtest logic, separate from `strategy_extreme_fvg.py`.

**Rationale:** Strategy 3 has fundamentally different selection logic (first LTF FVG after HTF respect vs. deepest unmitigated FVG), a different target (3:1 vs 2:1), and no mitigation requirement. Sharing code would require complex parameterization that obscures the logic in both strategies. Separate modules keep each strategy's intent clear.

**Alternative considered:** Parameterize Strategy 2's engine with a `selection_mode` flag (`"extreme"` vs `"first"`). Rejected because it would require Strategy 2's `find_setups()` to handle `selection_mode=first` correctly, and the 4H respect confirmation logic (unique to Strategy 3) has no natural home in Strategy 2's pipeline.

### D2: 4H FVG detection in `strategy_video_fvg.py`

**Decision:** The engine implements its own 4H FVG detection using the same 3-candle imbalance algorithm as Strategy 2, but selects the **most recent** FVG rather than the most recent **touching/containing** FVG.

**Rationale:** The video transcript describes marking "the first 4H fair value gap that you can see" — i.e., the most recently formed zone, not the one containing current price. This is simpler than Strategy 2's anchor selection logic and appropriate for Strategy 3's directional filter use case.

**Alternative considered:** Reuse `build_4h_fvg_cache()` from Strategy 2. Rejected because that function selects anchors by recency-of-touch, not recency-of-formation — a meaningfully different priority that would require a variant or parameterization.

### D3: HTF respect confirmation as a state in setup detection

**Decision:** The engine tracks a two-phase state per symbol: **(1) awaiting HTF respect**, **(2) scanning for LTF FVG after confirmation**. Phase transition occurs when the most recent 4H candle (at or after the anchor formed) closes with a strong directional body toward the LTF side.

**Rationale:** The video describes waiting for price to return to the 4H FVG and "watch how it instantly and aggressively shoots off in the opposite direction." This is a specific candle-closure event, not an instantaneous price crossing. Using candle-close as the confirmation boundary is consistent with the existing FVG detection approach and prevents whipsaw from intra-bar price noise.

**Threshold:** The HTF respect confirmation requires the confirmation candle's body to close in the directional direction with a body size ≥ 50% of the candle range (i.e., `abs(close - open) / (high - low) >= 0.5`). This is a configurable parameter (`htf_confirm_body_pct`, default `0.5`).

### D4: `Strategy3VideoFVG` adapter follows `Strategy2Extreme` pattern exactly

**Decision:** `Strategy3VideoFVG` mirrors `Strategy2Extreme`:
- `name = "video_fvg"`, `display_name = "Video FVG (4H Anchor)"`
- `default_params` with 4H/HTF-specific keys plus shared keys (ltf_timeframe, min_gap_pct, completion_target="3R")
- `find_setups()` wraps `get_video_setup_for_symbol()` — late import inside method body
- `backtest()` wraps `run_video_fvg_backtest()` — late import inside method body
- `_build_session_config()` helper (session filter logic shared with Strategy 2)

**Rationale:** Exact structural parity makes the strategy framework trivial to extend and test. Adding Strategy 4 requires copying one file and changing three lines. The late import inside the method body is critical for SOT tests: `patch("strategy_video_fvg.get_video_setup_for_symbol")` intercepts correctly (same pattern that works for Strategy 2 in existing tests).

### D5: Backtest report shape identical to Strategy 2

**Decision:** `run_video_fvg_backtest()` returns a `BacktestReport` (or compatible dataclass) with all the same fields as Strategy 2's report: `symbol`, `days`, `ltf_timeframe`, `total_trades`, `wins_1r/2r/3r`, `losses`, `net_pnl_1r/2r/3r`, `profit_factor_2r`, `max_drawdown_r`, `win_rate_2r`, `avg_trade_duration_min`, `completion_target`.

**Rationale:** The dashboard, ledger, and all downstream consumers expect this shape. Adding `video_fvg`-specific fields (e.g., `htf_anchor_confirmed`) is additive and non-breaking. The ledger already supports strategy-specific fields via `strategy_params`.

### D6: 4H anchor metadata stored in setup object, serialized to ledger

**Decision:** The setup dataclass for Strategy 3 includes `htf_anchor: Dict` (bottom, top, formed_at, direction) in addition to the standard setup fields. This is included in the setup payload via `_extreme_setup_payload`'s getattr guards (already added for non-FVG strategies in the review-fix pass), and recorded in `TrackedExtremeTrade` via the existing `htf_anchor` field.

**Rationale:** The review-fix pass to `_extreme_setup_payload` already added `getattr(setup, "anchor", None)` — so any setup object with an `anchor` attribute will serialize correctly to the ledger. Strategy 3's setup dataclass should include this field.

## Risks / Trade-offs

**[Risk] HTF FVG detection diverges from Strategy 2's cache**
The 4H FVG cache in Strategy 2 (`build_4h_fvg_cache`) is not shared with Strategy 3's standalone 4H detection. If both strategies run simultaneously (not supported — `EXTREME_ACTIVE_STRATEGY` picks one), they'd each maintain independent caches. Not a live risk, but a design divergence to document.
→ **Mitigation:** Document that simultaneous multi-strategy daemon scanning is out of scope. Backtests can run independently.

**[Risk] 3:1 target significantly reduces win rate**
With a 3:1 target, many trades that would hit 2R will instead extend to 3R and potentially retrace. Backtest results may show lower WR than Strategy 2. The video source recommends targeting 3:1 as a lifestyle choice ("after 5 years, just target 3:1") — implying lower signal rate is acceptable.
→ **Mitigation:** Backtest with both 2R and 3R completion targets exposed in the report so users can compare. Default completion_target="3R" in params.

**[Risk] HTF respect confirmation requires a full 4H candle close**
During a live scan (30s cadence), the daemon may evaluate 4H candles that haven't closed yet. The confirmation logic must use only closed 4H candles.
→ **Mitigation:** `get_historical_candles_range` already provides closed candles. The daemon cycle uses the same data path as backtesting. The confirmation condition is evaluated against the most recently closed 4H candle only.

## Migration Plan

No migration required. Strategy 3 is purely additive:
1. New files added: `strategy_video_fvg.py`, `strategies/strategy3_video_fvg.py`, `backtest_video_fvg.py`, `test_strategy3_video_fvg.py`.
2. `strategies/__init__.py` adds one import line for `Strategy3VideoFVG`.
3. No existing files modified (framework already supports plug-in strategies).
4. Rollback: remove the three new files and the one import line. No data migration.

## Open Questions

**Q1: Share 4H FVG detection code with Strategy 2?**
If Strategy 2's `build_4h_fvg_cache` is refactored into a shared utility (`fvg_detection.py`), both strategies could use it. This is a future optimization — not blocking for initial Strategy 3 implementation. Defer to the Strategy 4 integration pass.

**Q2: Support 1m as LTF in live daemon?**
The video mentions 1m, 5m, 15m. 1m produces a very high signal rate that may overwhelm the daemon (30s scan on 1m = 120 candles/symbol/cycle). The spec supports 1m; the implementation will work but live daemon on 1m may require additional rate-limit consideration. Defer 1m live daemon validation to a follow-on task.