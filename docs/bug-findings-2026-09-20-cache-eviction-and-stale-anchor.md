# Bug Findings & Architecture Report — 2026-09-20
## Cache Eviction Promotion, Historical Catch-up Alerts, and Stale 4H Anchors

> **Incident Date:** 2026-09-19 20:08:16 IST (14:38:16 UTC)  
> **Affected Symbol:** BTC-PERP (Strategy 2: Extreme LTF FVG)  
> **Symptom:** Live Telegram alert fired at 8:08 PM IST today stating `[ENTRY FILLED] BTC LONG IS NOW LIVE!` with `Fill Time: 18-Sep 07:30 PM IST` (24 hours and 38 minutes late).  
> **Primary Evidence:** Render production logs captured in `out.log`.  
> **Scope:** `strategy_extreme_fvg.py`, `screener_cycle.py`, `extreme_trade_tracker.py`, `candle_store.py`, `backtest_extreme_fvg.py`.  
> **Status:** Documented for subsequent OpenSpec implementation.

---

## 1. Incident Overview

At 20:08:16 IST on 19-Sep-2026, the live screener fired an `ENTRY_FILLED` Telegram notification:

```
🚀 [ENTRY FILLED] BTC LONG IS NOW LIVE!

• Extreme 5m FVG: [$79,298.00 - $79,903.90]
  └ Formed: 18-Sep 07:25 PM IST
• 4H Anchor: Bullish [$63,110.00 - $63,402.00]
  └ Formed: 17-Aug 01:30 PM IST | 1st Touch: 17-Aug 01:30 PM - 05:30 PM IST
• Filled At: $79,903.90
• Fill Time: 18-Sep 07:30 PM IST
• Stop Loss: $78,828.30
• Primary Target (2R): $82,055.10
• Status: 🚀 IN POSITION (Monitoring TP/SL)
```

The fill timestamp was **18-Sep 07:30 PM IST**, yet the alert was dispatched **24 hours and 38 minutes later** on 19-Sep at 08:08 PM IST.

---

## 2. Root Cause Analysis

The incident was caused by three compounding architectural defects:

### 2.1 The 300-Bar Sliding Window Eviction (`CandleStore`)
* In `strategy_extreme_fvg.py` (`get_extreme_setup_for_symbol`), the LTF candle query is hardcoded to `n=300`:
  $$\text{Memory Duration} = 300 \times 5\text{ minutes} = 1,500\text{ minutes} = 25\text{ hours}$$
* On the evening of 18-Sep (between 19:10 and 19:30 IST), BTC rallied violently from ~$78,000 to ~$80,000, leaving four consecutive 5m FVGs in a staircase:
  1. `18-Sep 19:10 IST`: `[$78,142.80 - $78,304.90]` (Entry: $78,304.90) — **True Extreme (Lowest)**
  2. `18-Sep 19:15 IST`: `[$78,376.80 - $78,828.30]` (Entry: $78,828.30)
  3. `18-Sep 19:20 IST`: `[$78,940.90 - $79,196.20]` (Entry: $79,196.20)
  4. `18-Sep 19:25 IST`: `[$79,298.00 - $79,903.90]` (Entry: $79,903.90)
* Throughout the night, `select_extreme_ltf_fvg()` correctly chose the lowest (#1) FVG ($78,304.90). Since BTC stayed above $80,000, price never retraced to it. The setup remained dormant as `PENDING_RETRACE`.
* Exactly 25 hours later (19-Sep 19:58–20:08 IST), each new live 5m candle pushed the oldest candle out of the 300-bar buffer:
  * **19:58 IST (`14:28:10 UTC`):** The 19:10 FVG fell out of memory. The scanner promoted the 19:15 FVG ($78,828.30).
  * **20:03 IST (`14:33:11 UTC`):** The 19:15 FVG fell out of memory. The scanner promoted the 19:20 FVG ($79,196.20).
  * **20:08 IST (`14:38:12 UTC`):** The 19:20 FVG fell out of memory. The scanner promoted the **19:25 FVG ($79,903.90)**.

### 2.2 Blind In-Memory Promotion in Tracker (`extreme_trade_tracker.py`)
Lines 400–428 in `extreme_trade_tracker.py` govern setup refresh:
```python
existing_formed = existing_pending.ltf_fvg.get("formed_at", 0) or 0
if fvg_formed_at > existing_formed:
    # Overwrite existing pending trade with the "newer" emission
```
The tracker already held the true lowest FVG ($78,304.90). However, because the scanner lost the 19:10 candle from cache, it emitted the 19:25 FVG whose `formed_at` timestamp was 15 minutes newer. The tracker blindly replaced the pending trade with a higher, worse entry price, misinterpreting cache eviction as a "fresh emission."

### 2.3 Historical Candle Replay Emits Live Alert
Once the 19:25 FVG was adopted, `_monitor_pending_trade()` replayed the post-formation candles:
* On the very next candle (**18-Sep 19:30 IST**), `candle.low <= 79,903.90` $\rightarrow$ `filled = True`.
* Over the next 24 hours, price stayed between $79,903.90 and $81,500:
  - Stop Loss ($78,828.30) was never breached.
  - 2R Target ($82,055.10) was never touched.
* The tracker transitioned the trade to `TRADE_ACTIVE` and queued an `ENTRY_FILLED` event.
* Because `trade_id = "BTC:1789739400000:79903.90"` was newly synthesized, Redis had no deduplication key.
* The screener sent a live Telegram push alert for a trade filled 25 hours earlier.

### 2.4 Stale 4H Anchor from a Different Price Era
* The 4H anchor was `Bullish [63,110.00 - 63,402.00]`, formed on **17-August 13:30 IST** (33 days ago).
* Because BTC never dipped below $63,110, the anchor was never invalidated.
* Because BTC rallied without pulling back into newer 4H zones, no newer 4H zone was touched.
* The strategy anchored 5-minute day trades at $81,000 to a $63,000 zone touched over a month ago, completely violating the intraday reaction intent of the strategy.

---

## 3. Live vs. Backtest Parity Gap

This behavior **cannot occur in the backtest engine** (`backtest_extreme_fvg.py`):

| Characteristic | Live Screener | Backtest Engine |
|---|---|---|
| **Candle Memory Limit** | Fixed 300 bars (~25h) rolling window | Full history (e.g. 30–60 days, 8,640+ bars) |
| **Candle Eviction** | Oldest bars drop out every 5m | No eviction; full persistence |
| **Candidate Pool** | Lost the $78,142 FVG at hour 25 | Retained $78,142 FVG indefinitely |
| **Setup Promotion** | Promoted $79,903 FVG after 25h | Never promoted $79,903 (shadowed by $78,142) |
| **First Touch Resolution** | Falls back to 4H bar if touch > 25h ago | Exact 5m candle across the whole period |
| **Historical Entry Alert** | Dispatched Telegram push alert | Evaluates forward chronologically at bar $i+1$ |

---

## 4. Architectural Fix Plan (4 Layers)

### Fix 1: Stale / Catch-up Alert Suppression (Immediate Guard)
In `screener_cycle.py`, suppress `ENTRY_FILLED` alerts if the actual fill occurred outside a recent real-time window:
```python
# If the entry fill occurred more than 15 minutes ago, suppress live push notification
MAX_LIVE_ALERT_LATENCY_MS = 15 * 60 * 1000  # 15 minutes (3 LTF bars)
if evt_type == "ENTRY_FILLED" and tr.entry_timestamp:
    if (now_ms - tr.entry_timestamp) > MAX_LIVE_ALERT_LATENCY_MS:
        logger.info(
            "Suppressed stale ENTRY_FILLED alert for %s (fill at %s is >15m old)",
            tr.symbol, tr.entry_filled_at_ist
        )
        continue
```

### Fix 2: Maximum LTF Setup Age Filter (Discovery Guard)
In `strategy_extreme_fvg.py` (`find_unmitigated_ltf_fvgs`), disallow candidate 5m FVGs formed too far in the past from entering the candidate pool:
```python
MAX_LTF_FVG_AGE_MS = 6 * 3600 * 1000  # 6 hours (72 5m candles)
if (now_ms - c3_close_ts) > MAX_LTF_FVG_AGE_MS:
    continue
```
*Day-trading 5m setups that have not triggered entry within 6 hours should not be treated as fresh setups.*

### Fix 3: In-Memory Pending Setup Eviction Protection
In `extreme_trade_tracker.py`, when a scanner emits a candidate setup for a symbol with an existing pending trade:
1. Verify that the new FVG is **genuinely fresh** (formed within the active session / last 2 hours).
2. Do **not** replace a pending trade with a higher (for bullish) or lower (for bearish) price level if the existing pending FVG has not been invalidated by market price.

### Fix 4: Maximum Lookback on 4H Anchor Touches
In `strategy_extreme_fvg.py` (`get_most_recent_touched_4h_fvg`):
* An anchor touch must have occurred within a maximum lookback window:
  $$\text{MAX\_ANCHOR\_TOUCH\_AGE\_DAYS} = 7 \text{ to } 14 \text{ days}$$
* If a 4H anchor's most recent touch is older than 14 days, mark it **EXPIRED / STALE**.
* This prevents anchoring live day trades to price levels from completely different market regimes (e.g. $63k support while trading at $81k).

---

## 5. Implementation Roadmap

When approved for implementation:
1. **Branch:** Create `fix/stale-anchor-and-catchup-alerts` off `develop`.
2. **OpenSpec Proposal:** Create `openspec/changes/fix-stale-anchor-catchup-alerts/` with `proposal.md`, `design.md`, `specs/strategy-2-extreme/spec.md`, and `tasks.md`.
3. **Tests:**
   - Unit test asserting that `screener_cycle.py` suppresses Telegram alerts for fills older than 15 minutes.
   - Unit test asserting `find_unmitigated_ltf_fvgs` rejects FVGs formed $>6\text{ hours}$ ago.
   - Unit test asserting `get_most_recent_touched_4h_fvg` discards anchor touches $>14\text{ days}$ ago.
   - Regression test in `test_extreme_trade_tracker.py` ensuring buffer rollover does not overwrite better pending setups.
4. **Verification:** Full `pytest -v` run passing 100%.
