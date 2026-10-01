## 1. Engine: 4H FVG Detection

- [x] 1.1 Create `strategy_video_fvg.py` with `VideoFVGSetup` dataclass (includes `anchor: Dict` field for 4H metadata, `ltf_fvg: Dict`, `direction`, `entry_price`, `stop_loss`, `risk_r`, `tp_1r/2r/3r`, `completion_target`, `ltf_timeframe`, `formed_at`). Verify: `python3 -c "from strategy_video_fvg import VideoFVGSetup; s = VideoFVGSetup(...); print(s.anchor)"`
- [x] 1.2 Implement `find_4h_fvgs(candles_4h) -> List[Dict]` — 3-candle imbalance detection on 4H bars, returns list of `{bottom, top, formed_at, direction}`. Verify: add unit test with 3 synthetic 4H candles producing a gap.
- [x] 1.3 Implement `select_anchor_4h(fvgs) -> Dict` — select most recent closed 4H FVG as anchor. Verify: test with two FVG candidates returns the one with latest `formed_at`.
- [x] 1.4 Implement `is_htf_respected(anchor, candles_4h) -> bool` — True when price has returned to the anchor zone and the next 4H candle closes with directional body ≥ 50% of range. Verify: add unit test with a bullish respect candle and a non-respect (small body) candle.

## 2. Engine: LTF FVG Entry Logic

- [x] 2.1 Implement `find_first_ltf_fvg(candles_ltf, direction, min_gap_pct, formed_after) -> Optional[Dict]` — scan LTF candles for first FVG in the correct direction after `formed_after` timestamp. Reuses gap detection algorithm from Strategy 2 with configurable `min_gap_pct`. Verify: test with synthetic LTF candles produces one FVG at the expected position.
- [x] 2.2 Implement `calc_entry_params(ltf_fvg, direction) -> (entry_price, stop_loss, tp_1r, tp_2r, tp_3r, risk_r)` — entry at FVG boundary, stop at formation wick, 3:1 target. Verify: unit test with Bullish FVG bottom=10000, top=10050, wick-low=9980 gives entry=10000, SL=9980, risk=20, TP3R=10060.
- [x] 2.3 Implement `get_video_setup_for_symbol(symbol, provider, ltf, min_gap_pct, htf_confirm_body_pct) -> Optional[VideoFVGSetup]` — two-phase detection: (1) find 4H anchor, (2) confirm HTF respect, (3) find first LTF FVG after confirmation, (4) calculate params. Returns None if any phase is not satisfied. Verify: integration test against BTC 30-day data produces a setup or None deterministically.

## 3. Engine: Backtest

- [x] 3.1 Create `backtest_video_fvg.py` with `run_video_fvg_backtest(symbol, days, ltf_timeframe, min_gap_pct, completion_target, session_filter, ...) -> BacktestReport` — identical report shape to `run_extreme_backtest`. Verify: report has all required fields: `symbol`, `days`, `total_trades`, `wins_1r/2r/3r`, `losses`, `net_pnl_1r/2r/3r`, `profit_factor_2r`, `max_drawdown_r`, `win_rate_2r`.
- [x] 3.2 Backtest forward-simulation: same candle-resolution rules as Strategy 2 — fill candle included in exit evaluation, SL takes precedence over TP on same-bar collision, all candles evaluated in chronological order. Verify: add test scenario where fill candle simultaneously hits TP and SL — must resolve STOPPED_OUT.
- [x] 3.3 Add CLI entry point: `python3 backtest_video_fvg.py --symbol BTC --days 30 --ltf 5m --min-gap-pct 0.03` with same output format as `backtest_extreme_fvg.py`. Verify: `python3 backtest_video_fvg.py --symbol BTC --days 14 --ltf 5m` prints JSON summary.

## 4. Strategy Adapter

- [x] 4.1 Create `strategies/strategy3_video_fvg.py` with `Strategy3VideoFVG(BaseStrategy)`: `name="video_fvg"`, `display_name="Video FVG (4H Anchor)"`, `interface_version=1`, `default_params` matching production defaults (ltf_timeframe="5m", min_gap_pct=0.03, htf_confirm_body_pct=0.5, completion_target="3R"). Verify: `get_strategy("video_fvg")` returns `Strategy3VideoFVG` instance.
- [x] 4.2 Implement `find_setups(sym, provider, params) -> List` — late import `from strategy_video_fvg import get_video_setup_for_symbol` inside method body, call with correct kwargs from `params`. Verify: SOT test `patch("strategy_video_fvg.get_video_setup_for_symbol")` intercepts correctly and provider is passed through.
- [x] 4.3 Implement `backtest(symbol, days, ltf, ...) -> BacktestReport` — late import `from backtest_video_fvg import run_video_fvg_backtest` inside method body. Verify: `Strategy3VideoFVG().backtest("BTC", 14, "5m", min_gap_pct=0.03, completion_target="3R")` returns a valid `BacktestReport`.
- [x] 4.4 Add `from strategies.strategy3_video_fvg import Strategy3VideoFVG` to `strategies/__init__.py` (self-registers via `@register`). Verify: `list_strategy_names()` includes `"video_fvg"`.

## 5. Ledger & Daemon Wiring

- [x] 5.1 Verify Strategy 3 setups flow into the ledger with `strategy="video_fvg"` and `strategy_params` containing the effective params. Integration test: run a daemon scan cycle with `EXTREME_ACTIVE_STRATEGY=video_fvg` and confirm `extreme_trade_tracker.get_filtered_trades(strategy="video_fvg")` returns setup records. Verify: `get_filtered_trades(strategy="video_fvg")["filters"]["strategy"] == "video_fvg"`.
- [x] 5.2 Verify HTF anchor metadata is captured in the trade record. After a Strategy 3 trade is opened, `TrackedExtremeTrade.to_dict()` includes `htf_anchor` block from the setup's `anchor` attribute. Verify: `s.anchor` attribute on `VideoFVGSetup` is a dict with keys `bottom`, `top`, `formed_at`, `direction`.

## 6. API Routes

- [x] 6.1 Verify `/api/video_fvg/backtest?symbol=BTC&days=30&ltf=5m` returns a valid backtest report. Verify: HTTP 200 with JSON body containing `total_trades` and `profit_factor_2r`.
- [x] 6.2 Verify `/api/video_fvg/status` returns `strategy="video_fvg"` and `is_active` reflecting whether `video_fvg` is the configured active strategy. Verify: when `EXTREME_ACTIVE_STRATEGY=video_fvg`, `is_active=True`.
- [x] 6.3 Verify `/api/video_fvg/info` returns `name="video_fvg"`, `display_name="Video FVG (4H Anchor)"`, and `default_params` with `completion_target="3R"`.

## 7. Tests

- [x] 7.1 Add `test_strategy3_video_fvg.py` covering: registry resolves `video_fvg`, `Strategy3VideoFVG` has correct name/display/params, `find_setups` calls engine with correct kwargs (mocked), `backtest` returns `BacktestReport` shape, and `to_dict` round-trip (setup serialization).
- [x] 7.2 Add unit tests for 4H FVG detection and HTF respect confirmation logic in `strategy_video_fvg.py`. Cover: bullish respect, bearish respect, non-respect (small body), no FVG found.
- [x] 7.3 Run full strategy test suite: `pytest test_strategy_registry.py test_strategy_interface.py test_strategy_adapter_equivalence.py test_strategy_api_routes.py test_strategy3_video_fvg.py -v` — all pass.
- [x] 7.4 Run full test suite: `pytest --ignore=.pytrans -q` — confirm zero regressions (335 existing + new Strategy 3 tests all pass).

## 8. Backtest Validation

- [x] 8.1 Run Strategy 3 backtests for BTC, ETH, SOL across 14d and 30d windows. Compare `net_pnl_2r` and `net_pnl_3r` to assess 3R vs 2R performance differential. Verify: results written to `strategy3_backtest_results.json`.
- [x] 8.2 Compare Strategy 3 results to Strategy 2 results for the same symbols/days/ltf — confirm both strategies produce valid (non-zero) results and neither crashes.