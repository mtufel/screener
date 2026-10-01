# Proposal: Add Strategy 3 — Video FVG (4H Anchor + LTF Entry)

## Why

Strategy 3 provides a structurally simpler, higher-timeframe-confirmed FVG system that differs meaningfully from Strategy 2 (Extreme FVG) in entry trigger and risk target. It was sourced from a video source transcript and validated through backtest. Adding it tests the pluggable strategy framework (`feat/strategy-extensibility`) end-to-end while delivering a complementary strategy with a different risk profile (3:1 minimum target vs Strategy 2's 2:1).

## What Changes

- New strategy adapter: `strategies/strategy3_video_fvg.py` — `Strategy3VideoFVG(BaseStrategy)` plugging into the existing registry with `name="video_fvg"`.
- New engine module: `strategy_video_fvg.py` — setup generation and backtest engine for the 4H-anchor + LTF-entry FVG system.
- New backtest entry point: `backtest_video_fvg.py` — CLI + `run_video_fvg_backtest()` mirroring `backtest_extreme_fvg.py`.
- Strategy 3 registered in `strategies/__init__.py`.
- `EXTREME_ACTIVE_STRATEGY` can be set to `"video_fvg"` to run Strategy 3 as the active daemon strategy.
- Ledger captures `strategy="video_fvg"` and the effective `strategy_params` blob on every trade.
- Existing Strategy 2 (`extreme_fvg`) is unchanged and fully backward compatible.

## Capabilities

### New Capabilities

- `video-fvg`: Strategy 3 — two-timeframe FVG system using a 4H FVG as directional anchor and an LTF (1m/5m/15m) FVG as the entry trigger. Introduces its own engine (`strategy_video_fvg.py`), adapter (`Strategy3VideoFVG`), and backtest CLI (`backtest_video_fvg.py`). Plugged into the `BaseStrategy` framework.

### Modified Capabilities

- *(none — Strategy 3 is additive; no existing spec requirements change)*

## Impact

- **New files**: `strategies/strategy3_video_fvg.py`, `strategy_video_fvg.py`, `backtest_video_fvg.py`, `test_strategy3_video_fvg.py`
- **Modified files**: `strategies/__init__.py` (register `Strategy3VideoFVG`)
- **API**: New `/api/video_fvg/backtest` and `/api/video_fvg/status` routes auto-registered via the strategy-parameterized API (added by `strategy-extensibility`)
- **Ledger**: Trades opened by Strategy 3 record `strategy="video_fvg"` and the full params blob — fully queryable via `get_filtered_trades(strategy="video_fvg")`