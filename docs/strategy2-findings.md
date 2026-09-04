# Strategy 2 (Extreme LTF FVG) — Findings & Bug Analysis

> Generated: 2026-09-04
> Scope: **Strategy 2 only** (Extreme LTF FVG engine, live daemon, trade ledger,
> backtester, live scanner, and related endpoints/charts).
> All source reviewed: `strategy_extreme_fvg.py`, `extreme_trade_tracker.py`,
> `backtest_extreme_fvg.py`, `live_screener_extreme.py`, plus Strategy-2 paths in
> `main.py`, `chart_generator.py`, `hyperliquid_client.py`, `telegram_client.py`.
> Test suite: **87 passed** (existing tests do NOT cover any of the issues below).

---

## 1. How Strategy 2 Works (mental model)

### Pipeline
1. **4H FVG Cache** — `HTFFVGCache` (`strategy_extreme_fvg.py`):
   - Bootstrap scans 200 closed 4H candles, detects all FVGs from 3-candle
     sequences (`Bullish: c3.low > c1.high`, `Bearish: c3.high < c1.low`).
   - Delta updates: invalidates FVGs on subsequent breach (wick by default,
     close if `use_close_invalidation`), detects newly formed FVGs on new closed
     candles only. Cache keyed per `{symbol}:{mode}`.
2. **4H Anchor Selection** — `get_most_recent_touched_4h_fvg`:
   - Only FVGs touched strictly **after** they closed count (`c.timestamp >=
     fvg.close_timestamp`; the forming candle can never be its own touch).
   - First touch (LTF scan start) + most recent touch timestamps recorded.
   - Single anchor = most recently touched (currently-inside sorts first).
3. **LTF (15m) FVG Discovery** — `find_unmitigated_ltf_fvgs`:
   - Only FVGs formed **after** the anchor's first touch, on **fully closed**
     candles, matching the anchor direction, with `gap_pct >= min_gap_pct`.
   - Lifecycle evaluated per FVG: `PENDING_RETRACE` -> `TRADE_ACTIVE` ->
     `COMPLETED` / `STOPPED_OUT` / `INVALIDATED` (see `evaluate_ltf_setup_lifecycle`).
   - Retains only `PENDING_RETRACE` and `TRADE_ACTIVE` for the setup pool.
4. **#1 Extreme FVG** — `select_extreme_ltf_fvg`:
   - Bullish -> lowest `bottom`; Bearish -> highest `top` (deepest FVG).
5. **Trade Setup** — `build_extreme_trade_setup`:
   - Entry: outer boundary (`top` bullish / `bottom` bearish).
   - SL: extreme wick across the 3 forming candles.
   - Targets: 1R / 2R / 3R. `completion_target` (default 2R) from config.

### Runtime components
- `main.py` — FastAPI app; `extreme_screener_background_worker` runs
  `execute_extreme_screener_cycle` every `EXTREME_SCAN_INTERVAL_SECONDS` (30s).
- `extreme_trade_tracker.ExtremeTradeTracker` — immutable single-position ledger
  per symbol, persisted to `data/extreme_live_trades.json`; emits
  `NEW_SETUP` / `ENTRY_FILLED` / `TP_HIT` / `SL_HIT` events.
- `live_screener_extreme.py` — standalone CLI scanner (same strategy engine).
- `backtest_extreme_fvg.py` — chronological simulation over historical candles.
- `chart_generator.generate_extreme_setup_chart` — TradingView-style charts with
  ---

## 2. Confirmed Bugs (Strategy 2)

### 2.1 🔴 Extreme backtest counts the same trade as BOTH a win and a loss
**File:** `backtest_extreme_fvg.py`

**Where:** `simulate_trade_execution` (lines ~214-251).

**Problem:** `hit_1r/hit_2r/hit_3r` flags are sticky and the loop only breaks on
TP3 or SL. A trade that touches 1R/2R and later reverses into the stop loss ends
with `hit_2r=True` (counted in `wins_2r` at line ~464) **and**
`exit_reason="STOPPED_OUT"` (counted in `losses` at line ~467) —
`realized_r_2r = +2.0`.

**Verified with synthetic candles:**
```
exit_reason: STOPPED_OUT
hit_1r: True hit_2r: True hit_3r: False
realized_r_2r:  +2.0
report wins_2r = 1 | report losses = 1   -> same trade is a win AND a loss
```

**Impact:** `wins_2r + losses > total_trades`; win-rate / loss tables in
`/api/extreme/backtest` and the console report are internally inconsistent.

**Fix:** resolve each trade at the chosen `completion_target` (break/settle at
that target), or compute `losses` per target (e.g. `STOPPED_OUT and not hit_2r`).

---

### 2.2 🔴/🟠 Stale `PENDING_RETRACE` ledger entries never resolve or expire
**File:** `extreme_trade_tracker.py`

**Where:** `process_live_setups` (monitor loop, lines ~207-210; close loop
~293-297).

**Problem:**
- `to_close` is only populated for `TRADE_ACTIVE` trades. PENDING entries are
  never monitored, never expired, never transition to `"INVALIDATED"` — even
  when the 4H anchor FVG is invalidated by a price breach.
- PENDING trades accumulate forever in `active_trades` (persisted to
  `data/extreme_live_trades.json`, shown in `/api/extreme/live-history`,
  inflate `pending_now` / `total_tracked_trades` in `get_summary`).
- Deterministic `trade_id` (`{sym}:{formed_at}:{entry:.2f}`) means a stale
  PENDING record permanently suppresses re-alerting the same FVG setup.
- `"INVALIDATED"` is declared in `TrackedExtremeTrade.state` but never set
  anywhere — confirming the intent was to age these out.

**Fix:** monitor PENDING trades in step 2; expire/mark `INVALIDATED` when the
anchor no longer appears in scan results (e.g. absent for N consecutive cycles)
---

### 2.3 🟠 Manual `/api/extreme/scan` silently resets the daemon runtime config
**File:** `main.py` (`api_extreme_scan`, lines ~998-1023).

**Where:**
```python
if ltf:                              state["extreme_ltf"] = ltf          # always true
if target:                            state["extreme_target"] = target
if invalidation:                      state["extreme_use_close"] = ...
if min_gap_pct is not None:          state["extreme_min_gap"] = min_gap_pct
```

**Problem:** `ltf`, `target`, `invalidation`, `min_gap_pct` have non-None Query
defaults (`"15m"`, `"2R"`, `"wick"`, `0.05`). A plain
`GET /api/extreme/scan` (a "scan now" call) unconditionally overwrites the 30s
background daemon's config that was set via `/api/extreme/config`.

**Fix:** default the query params from `state` (or only persist when the caller
explicitly provided them — like `symbols` already is).

---

### 2.4 🟠 Alias whitelist symbols freeze live mid-price tracking — `floating_r` at 0
**Files:** `main.py` + `extreme_trade_tracker.py`

**Where:** `execute_extreme_screener_cycle` emits setups with `"symbol": sym`
(e.g. `"GOLD"`); monitoring loop in `extreme_trade_tracker.py` (~line 212):
```python
curr_px = float(current_mids.get(trade.symbol, trade.entry_price)))  # key miss -> entry price
trade.floating_r = round((curr_px - trade.entry_price) / risk_r)     # frozen at 0.00R
```

**Problem:** `COINS_WHITELIST` defaults (`GOLD`, `SILVER`, `OIL`) are aliases;
`allMids` is keyed by raw Hyperliquid names (`PAXG`, `SILVER`, `WTIOIL`). The
lookup misses, so floating-R / MFE / live PnL stay frozen at entry for
GOLD / SILVER / OIL / XAU / etc. (TP/SL still resolve via real candle extremes).

**Fix:** store the raw symbol (`PAXG` etc.) as the trade symbol in the setup
dict, or resolve `SYMBOL_ALIASES` when looking up `current_mids`.

---

### 2.5 🟡 `"INVALIDATED"` / `"TIME_EXPIRED"` are dead-code outcomes
**Files:** `strategy_extreme_fvg.py`, `extreme_trade_tracker.py`,
`backtest_extreme_fvg.py`.

- `find_unmitigated_ltf_fvgs` discards `INVALIDATED` setups before the ledger —
  fine in isolation, but it means no ledger record ever reaches the nominal
  `INVALIDATED` lifecycle state (compounds bug 2.2).
- `simulate_trade_execution`: `exit_reason = "TIME_EXPIRED"` is only reachable
  if candle history runs out; every real trade ends at TP3 or SL, so the outcome
  class is effectively unrealized in backtest stats (and `losses` doesn't
  include them correctly anyway — see bug 2.1).

---

### 2.6 Minor — `ltf_fvg_formed_ts` timestamp semantics differ between daemon and chart
**Files:** `main.py` vs `backtest_extreme_fvg.py` vs `chart_generator.py`.

- Daemon passes `ltf_fvg_formed_ts=tr.ltf_fvg.get("formed_at")` = candle **open**
  time; `generate_extreme_setup_chart` adds `+ c_dur` to derive the post-formation
  window (correct).
- `ExtremeHistoricalTrade.to_dict` emits `fvg_formation_timestamp` = candle
  **close** time (`ltf_fvg.close_timestamp`). Feeding that into
  `/api/extreme/chart?ltf_formed_ts=...` shifts the entry-candle marker window a
  ---

## 3. Everything Checked & NOT a Bug (Strategy 2)

- `AsyncRateLimiter` + 429 cooldown/burst math — OK.
- `get_universe` caching and `allMids` handling — OK.
- 4H cache bootstrap/delta logic (`HTFFVGCache.bootstrap`/`update_delta`) — OK.
- TP-before-SL evaluation ordering in `evaluate_ltf_setup_lifecycle` — OK.
- Trade ledger immutability for `TRADE_ACTIVE` entries — OK.
- Entry/exit candle marker anchoring in `generate_extreme_setup_chart` — OK.
- All files pass `py_compile`; the odd characters seen in some read outputs were
  display artifacts, not file corruption.
- Full test suite: `pytest` -> **87 passed**.

---

## 4. Suggested Fix Priority

1. **2.1** — backtest win/loss double-count (corrupts backtest stats).
2. **2.2** — stale PENDING ledger entries (corrupts live state/UI/alerts).
3. **2.3** — manual scan resets daemon config (operator-visible behavior bug).
4. **2.4** — alias symbol mid-price freeze (usable with default whitelist).
5. **2.5 / 2.6** — dead-code outcomes + timestamp normalization (hardening).

Each fix should ship with a small regression test; existing tests do not cover
any of these paths.