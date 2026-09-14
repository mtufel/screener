# OpenSpec Change Proposal: Fix Real-Time WebSocket Streaming & Monitoring Bugs (1, 3, 4, 5, 6)

## Problem Statement
The real-time WebSocket infrastructure and screener monitoring pipeline contain five confirmed bugs documented in `docs/realtime-ws-bug-report.md`:
1. **Bug 1 (High):** Binance WebSocket miniTicker stream frames (`!miniTicker@arr`) push only active symbols. Ingesting these frames with `set_cached_mids(self.provider_name, mids)` replaces the entire mid prices cache (`merge=False`), discarding inactive symbols and defaulting their current price to `0.0` or `entry_price`.
2. **Bug 3 (Medium):** Strategy 2 exit monitoring and event chart generation in `main.py` call `get_last_n_candles` without passing `client=provider`, falling back to the module-level singleton provider. This disconnects exit evaluation from the active WebSocket-connected provider instance and its `CandleStore` cache.
3. **Bug 4 (Low):** `CcxtProvider.start_websocket()` prematurely sets `self._ws_connected = True` before any connection is made or incoming frames are received, falsely suppressing REST fallbacks on connection failures.
4. **Bug 5 (Low):** `update_subscriptions` in `binance_ws.py` and `hyperliquid_ws.py` spawns `asyncio.create_task(self._send_subscriptions())` without retaining task references, risking task garbage collection mid-flight and unsynchronized concurrent writes across websocket frames.
5. **Bug 6 (Low):** `DashboardWSManager.broadcast` awaits `ws.send_json(message)` sequentially inside a synchronous loop over browser clients. A single slow or buffering connection delays the entire screener scan and trade execution cycle.

*(Note: Bug 2 regarding in-progress open candle wick detection is excluded from this change per user instruction).*

## Proposed Solution
1. **Bug 1 Fix:** Call `self.store.set_cached_mids(self.provider_name, mids, merge=True)` in `market_data/binance_ws.py`.
2. **Bug 3 Fix:** Pass `client=provider` to `get_last_n_candles()` in `main.py` lines 455 and 484 during Strategy 2 execution.
3. **Bug 4 Fix:** Initialize `_ws_connected = False` in `CcxtProvider` and only set it to `True` once a ticker or candle frame has been received and processed in the watcher loops.
4. **Bug 5 Fix:** Maintain a `_background_tasks` set with completion callbacks and a `_send_lock = asyncio.Lock()` in `binance_ws.py` and `hyperliquid_ws.py`.
5. **Bug 6 Fix:** Make `DashboardWSManager.broadcast` broadcast concurrently to all clients using `asyncio.gather(*[...], return_exceptions=True)` with a 2-second timeout per client.

## Impact
- Mid-price caching for Binance WebSocket streaming is persistent and reliable.
- Strategy 2 exit monitoring reads from the active runtime provider running WebSockets.
- CCXT Pro connection state accurately reflects real data flow.
- Background subscription tasks and WebSocket writes are memory-safe and concurrency-safe.
- Dashboard pushes are fast, isolated, and cannot block screener cycles.
