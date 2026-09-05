# Tasks: Strategy-Based TDD Test Suite

## 1. Discovery & FVG math (`test_ltf_fvg_discovery.py`)

- [x] 1.1 Bullish/bearish FVG boundary math exact (zone = `[c1.high, c3.low]` / `[c3.high, c1.low]`), `gap_pct` present
- [x] 1.2 Equality edge `c3.low == c1.high` (and bearish mirror) produces NO FVG (strict inequality)
- [x] 1.3 Overlapping candle ranges produce no FVG in either direction
- [x] 1.4 Min-gap filter boundary: just below 0.05% rejected, at/above accepted
- [x] 1.5 No discovery before first anchor touch (no lookahead)
- [x] 1.6 FVG formation must be post-touch; formation exactly at touch timestamp is included
- [ ] 1.7 FVG outside anchor zone rejected
- [x] 1.8 No post-formation touch → `PENDING_RETRACE` with no entry time; entry price = zone outer boundary
- [x] 1.9 Post-formation touch → `TRADE_ACTIVE` with entry timestamp of the touching candle (never the formation candle)
- [x] 1.10 SL breach before entry discards that FVG (later FVG may still be discovered)

## 2. Extreme ranking & anchors (`test_extreme_ranking.py`)

- [x] 2.1 Bullish: deepest bottom wins; Bearish: highest top wins
- [ ] 2.2 Last-3-candle window on unbalanced candle sets
- [x] 2.3 Untouched FVG → None; touch = first post-formation close inside zone bounds (inclusive)
- [x] 2.4 First touch = earliest qualifying close, not the latest
- [x] 2.5 HTF formation time = c3 close; `first_touch_time_ist` IST formatting

## 3. Execution params & pipeline (`test_execution_params.py`)

- [x] 3.1 Bullish: entry = zone top, SL = min(c1,c2,c3 lows), targets = +1R/+2R/+3R; bearish mirror
- [x] 3.2 SL wick scan covers all three formation candles (c2-extreme variant)
- [x] 3.3 `risk_r` exact; TP ladder monotonic per direction
- [x] 3.4 End-to-end `discover_setups`: anchor→setup wiring, per-symbol isolation, deterministic repeat calls
- [x] 3.5 Currently-inside anchor preferred over deeper untouched anchor

## 4. Ledger lifecycle — pending (`test_trade_lifecycle.py`)

- [x] 4.1 `NEW_SETUP` once; duplicate re-emissions produce no events
- [x] 4.2 Newer `formed_at` replaces pending in place; older emission ignored; no duplicate rows
- [x] 4.3 Pre-entry invalidation: SL breach → `SETUP_INVALIDATED`, symbol freed for next setup
- [x] 4.4 Anchor breach invalidates pending; absent-expiry after N cycles (monkeypatched), presence resets counter
- [x] 4.5 Same-bar fill+TP and fill+SL from pending resolve in one cycle
- [x] 4.6 Pre-formation candles never complete a pending setup (formed_at floor — PAXG spam regression)
- [x] 4.7 Opposite-direction pendings coexist; live-mid fill path sets entry timestamp to now

## 5. Ledger lifecycle — active

- [x] 5.1 Happy path to `COMPLETED_TP` (+2.0R default target); archived; symbol freed
- [x] 5.2 SL-before-TP precedence on one candle; chronological scan across candles (earlier SL beats later TP — PAXG regression)
- [x] 5.3 Per-target completion: 1R/2R/3R produce realized +1.0/+2.0/+3.0
- [x] 5.4 MFE tracking (peak before retrace to SL); `duration_min`/`closed_timestamp` from candle evidence; IST exit formatting
- [x] 5.5 Active immutability: re-emitted setup with different params never overwrites an active trade
- [x] 5.6 JSON persistence round-trip across tracker restart; `get_summary()` math (win rate, net R, avg MFE)
- [x] 5.7 Event discipline: each lifecycle event fires exactly once across repeated cycles

## 6. Charts, Telegram, Strategy 1 tracker

- [ ] 6.1 `test_chart_generation.py`: non-empty PNGs for all states (both strategies), empty-candle guards, `output_path` writes
- [ ] 6.2 `test_telegram_formatting.py`: content correctness, ≤4096 chunking, empty input, missing-credentials no-send
- [ ] 6.3 `test_trade_tracker_stage2.py`: milestone alerts once, 2R/SL exits once, closed never re-checked, persistence round-trip, HTF-occupancy suppression

## 7. Wrap-up

- [ ] 7.1 Full `pytest -v` green; update test-count references in `openspec/project.md`
