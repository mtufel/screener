# QA Live-Simulation Report — Real-Time Trade Entry / Exit / Formation

**Date:** 2026-09-14
**Engineer role:** QA test engineering
**Approach:** Drive the REAL `main.execute_extreme_screener_cycle()` (the exact function the
Strategy-2 background worker calls) with a scripted "live" provider serving candle closes per
scan, plus a live-market WebSocket ingestion smoke test. All notification side-effects
(Telegram, Redis dedup, dashboard WS) are captured. Harness: `qa_live_sim.py`.

---

## 1. Live WebSocket ingestion smoke test (real market)

Ran `HyperliquidProvider.start_websocket()` against the live feed (`wss://api.hyperliquid.xyz/ws`)
for 8s:

```
supports_ws True
ws_connected True
mids sample {'BTC': 77608.5, 'ETH': 2514.75, 'SOL': 101.125, 'GOLD': 4327.45}
btc 5m candles 20 latest: {t.. o:"77660.0", h:"77662.0", l:"77608.0", c:"77608.0", i:"5m" ...}
```
**Result: PASS** — allMids + candle streams flow; real-time mids and candles are written to
`CandleStore`; REST bootstrap works; reconnect loop is healthy. The ingestion layer is sound.

---

## 2. Simulated live-scan lifecycle scenarios (real worker)

Each scenario appends LTF candles per scan and runs one `execute_extreme_screener_cycle()`,
then reads the tracked trade state and recorded notifications.

### S1–S3 BTC: formation -> pending -> fill -> TP (win)
```
[scan 0] PENDING_RETRACE  "Waiting for Retrace"     -> NEW_SETUP fired
[scan 1] TRADE_ACTIVE     "Active (+0.03R)"         -> ENTRY_FILLED fired
[scan 2] TRADE_ACTIVE     "Active (+1.73R)"         <- TP wick present, NOT acted on
[scan 3] (closed)                                    -> TP_HIT fired
```
- ✅ Formation detection, PENDING_RETRACE, NEW_SETUP alert all correct & prompt.
- ✅ Entry fill (retrace into entry) and ENTRY_FILLED alert correct & prompt.
- ⚠️ **TP exit is ONE LTF bar LATE**: the candle whose high (2460) already exceeded TP2
  (2459), closing at 2455, was not acted on during its own scan (only the closing price was
  used -> +1.73R). The exit fired only after a *subsequent* candle opened, i.e. the wick bar
  finally read as closed. Confirms **Bug 2**.

### S4 ETH: formation -> pending -> fill -> SL (loss)
```
[scan 0] PENDING_RETRACE
[scan 1] TRADE_ACTIVE     "Active (+0.03R)"  -> ENTRY_FILLED
[scan 2] TRADE_ACTIVE     "Active (-0.93R)"  <- SL wick present, NOT acted on
[scan 3] (closed)                            -> SL_HIT fired
```
- ⚠️ **SL exit is ONE LTF bar LATE** for the same reason (open-bar wick ignored; only close
  price used). Confirms **Bug 2**.

### S5 SOL: formation -> invalidated (SL breach before entry)
```
[scan 0] PENDING_RETRACE
[scan 1] PENDING_RETRACE   (breach candle appended but still "open")
[scan 2] (trade removed -> history)     no SETUP_INVALIDATED alert recorded
```
- ✅ The invalidation was **tracked** (trade moved to history).
- ❌ **No notification** is emitted: `main.py` has **no `SETUP_INVALIDATED` branch** in the
  event-notification loop (grep returns nothing). No Telegram alert and no Redis dedup mark;
  only a silent dashboard broadcast fires. Invalidations are effectively silent while
  NEW_SETUP / ENTRY_FILLED / TP_HIT / SL_HIT all alert.

---

## 3. Findings

| # | Severity | Finding | Evidence |
|---|----------|---------|----------|
| F1 | **HIGH** | Exits (TP/SL) are detected **+1 LTF bar late**; the open/in-progress candle's high/low is never used, only `curr_px` (close). | BTC TP_HIT & ETH SL_HIT fire on scan3, not scan2, in scenario runs. Root cause = `strategy.py:447` drops the open candle (`raw[:-1]`) feeding `recent_candles_map`, so `extreme_trade_tracker.process_live_setups` can't see the live wick. |
| F2 | **HIGH** | Dashboard floating-R is **understated / stale during an open bar**: BTC showed `+1.73R` (from close 2455) while the bar's high (2460) already implied `+2.07R` and TP2. | scan2 state `'Active (+1.73R)'` while TP wick present. |
| F3 | **MED** | Invalidation is tracked but **silent** — no `SETUP_INVALIDATED` notification path exists in `main.py`. | `grep -n SETUP_INVALIDATED main.py` -> no matches; SOL removed with only a dashboard broadcast. |
| F4 | INFO | Formation + entry-fill (the "pending retrace -> state change -> notification" flow the user cares about) work **correctly and promptly**. | NEW_SETUP / ENTRY_FILLED fire on the expected scans; correct state/status_detail. |

**Root cause of F1/F2:** the exiting bar's wick is discarded (`strategy.get_last_n_candles`
drops the current candle), so the time-series the exit monitor iterates contains only closed
bars *and lags by one bar*; the only real-time signal is `curr_px`, which cannot represent a
wick-and-pullback and which underestimates floating R.

**Il marqueur:** 4 of the 6 reported bugs are fixed & verified earlier. Bug 2 (F1/F2) is STILL
OUTSTANDING and is precisely what causes the observed real-time exit delay — unchanged in
`strategy.py` / `extreme_trade_tracker.py`.

---

## 4. Repro commands
```
# Deterministic lifecycle simulation (offline, real worker)
.venv/bin/python qa_live_sim.py
# logs captured to /tmp/qa_live_sim.log, per-run console out to /tmp/qa_run.out
```
Live WS smoke test and full pytest regression were also run (270 passed).

## 5. Clean-up note
The harness writes simulated trades to `data/extreme_live_trades_local.json` (untracked);
reset it to empty after a run. Done for this report.