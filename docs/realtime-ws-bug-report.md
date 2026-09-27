# Real-Time WebSocket Trade Entry / Exit / Formation — Bug Report

**Date:** 2026-09-14
**Branch:** `feat/telegram-alert-threading`
**Scope:** WebSocket handling for real-time trade **entry**, **exit**, and **formation**.
**Baseline:** `test_websocket_streaming.py` + `test_trade_lifecycle.py` = 47 passing. None of the bugs below are covered by tests.

---

## Data flow (as wired)

```
WS client (market_data/{hyperliquid_ws,binance_ws,ccxt_provider}) ─► CandleStore (candle_store.py)
        │  set_cached_mids(...) / merge_candles(...,"t"=open ts)
        ▼
execute_extreme_screener_cycle (main.py:310)         provider = get_market_data_provider(state["data_provider"])
   ├─ formation/entry scan: get_extreme_setup_for_symbol(client=provider)   ← runtime provider (WS-fed)  ✅
   └─ exit monitor data:    recent_candles_map = get_last_n_candles(...)     ← module-level provider (Bug 3)  ❌
        ▼
extreme_trade_tracker.process_live_setups()  → ENTRY_FILLED / TP_HIT / SL_HIT / SETUP_INVALIDATED
```

---

## Bug 1 — HIGH: Binance WS partial ticker updates clobber the full mids cache

**Files:** `market_data/binance_ws.py:150`, `market_data/binance.py:129`

The Binance miniTicker stream (`!miniTicker@arr`) only pushes the symbols that **changed** each
frame — not the entire book. The client writes them with default **replace** semantics:

```python
# market_data/binance_ws.py:150
self.store.set_cached_mids(self.provider_name, mids)   # merge=False
```

Then the provider trusts that (now shrunk) cache whenever the socket is connected, without a
REST fallback:

```python
# market_data/binance.py:129
cached = self._store.get_cached_mids(self.name, ignore_ttl=self.is_websocket_connected)
if cached is not None:
    return cached
```

**Impact**
- Each tick **replaces** the cached symbol→price map with only the recently-touched symbols.
- Quieter/less-volatile coins fall out of the cache; their `curr_px` becomes `0.0`.
- `main.py:341/393` then falls back to `entry_price`, producing wrong `dist_pct`, `floating_r`,
  and TP/SL evaluations for those symbols in real time.

**Why this is clearly a bug**
The codebase already handles partial tickers correctly in the ccxt provider
(`market_data/ccxt_provider.py:468` uses `merge=True`), and Hyperliquid `allMids` is a full
snapshot so replace is fine there. Binance is the inconsistent case.

**Fix:**
```python
self.store.set_cached_mids(self.provider_name, mids, merge=True)
---

## Bug 2 — HIGH: Exit monitor cannot see the open (in-progress) candle

**Files:** `strategy.py:447`, `main.py:455`, `extreme_trade_tracker.py:442-534, 588-616, 660-696`

`strategy.get_last_n_candles` deliberately drops the live/open candle:

```python
# strategy.py:447
finished_raw = raw[:-1] if len(raw) > 1 else raw
```

`main.py:455` builds `recent_candles_map` from that finished-only series, and the tracker's
candle-based fill / TP / SL loops read only those **closed** bars. Meanwhile the WebSocket *is*
streaming the live candle's evolving high/low into `CandleStore` (via `merge_candles` keyed on
`"t"`), but that data is discarded on this path.

The only real-time fallback is the live-`curr_px` check (`extreme_trade_tracker.py:660-696`),
which compares the *current price* — not the bar's wick.

**Impact (directly answering the real-time entry/exit concern)**
- **Entry:** a fill that happens inside the current bar isn't seen until that bar closes (up to
  one LTF bar later).
- **Exit:** a TP or SL that is wicked *and then pulled back* inside an open bar is **not**
  resolved by the live-price check (it only compares `curr_px`). The exit is only recorded on
  the next closed bar — delayed by 5m / 15m / 1h.

**Fix:** feed the monitor the in-progress candle as well (or expose it from the store) and base
exit detection on its `high`/`low` in addition to `curr_px`.

---

## Bug 3 — MEDIUM: Exit monitoring reads a different provider than the one running the WebSocket

**Files:** `main.py:332, 382, 455`, `strategy.py:441`
**Related:** `main.py:738` (lifespan WS start)

- **Formation/entry** uses the runtime provider: `main.py:382-391` passes `client=provider` —
  the instance whose WebSocket was started in `main.py:738`. → WS-fed.  ✅
- **Exit monitor data** `recent_candles_map` is built with `get_last_n_candles(symbol=..., timeframe=...)`
  **without a client** (`main.py:455`) → `strategy.py:441` falls back to the **module-level**
  `market_data_provider` singleton (from env `DATA_PROVIDER`).

When `state["data_provider"]` (runtime/Redis, default `"binance"`) differs from the `DATA_PROVIDER`
env (e.g. `hyperliquid`), you get two distinct provider instances. The exit/entry candle data then
comes from a provider whose WebSocket was never started (REST-only) and reads a different
`CandleStore` namespace (provider name differs), so all live WS candle data is bypassed for exit
detection.

**Impact:** the pipeline becomes internally inconsistent — **formation/entry = live WS**,
**exit (TP/SL/fill) = REST/cached or stale**.

**Fix:** pass the runtime `provider` into those `get_last_n_candles` calls, same as the setup scan.
---

## Bug 4 — LOW: CcxtProvider reports the socket connected before any frame arrives

**File:** `market_data/ccxt_provider.py:424`

```python
self._ws_running = True
self._ws_connected = True   # optimistic — set before watch_tickers/watch_ohlcv yields
```

`is_websocket_connected` therefore returns `True` even if the pro exchange silently fails, and
callers then skip REST fallback (`get_all_mids(..., ignore_ttl=True)`).

---

## Bug 5 — LOW: Fire-and-forget subscription updates can be dropped

**Files:** `market_data/hyperliquid_ws.py:80`, `market_data/binance_ws.py:68`

```python
if self.is_connected:
    asyncio.create_task(self._send_subscriptions())
```

No reference is kept and no lock is held. A symbol added right at a reconnect boundary can race
with `_reconnect_loop()` and never get sent until the next full reconnect.

---

## Bug 6 — LOW: Dashboard broadcast is serial and can block the scan cycle

**File:** `main.py:37-44` (`DashboardWSManager.broadcast`)

```python
for ws in list(self.active_connections):
    await ws.send_json(message)   # sequential await per client
```

One slow/blocked client stalls every broadcast and adds latency to the scan cycle / trade events.

---

## Recommended fix priority

1. **Bug 1** — one-line `merge=True`; eliminates wrong real-time prices for quiet symbols.
2. **Bug 2** — include the live candle in the monitor path; restores same-bar entry/exit reaction.
3. **Bug 3** — pass the runtime provider through; makes formation *and* exits use the same WS-fed data.
4. Bugs 4–6 — hardening, lower urgency.
```