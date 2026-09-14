# OpenSpec Design: Fix Real-Time WebSocket Streaming & Monitoring Bugs

## Architecture & Data Flow

### 1. Binance WS Mids Merging (Bug 1)
```
Binance WS (!miniTicker@arr) 
   │
   ▼
handle_message() -> mids dict (symbols updated in current batch)
   │
   ▼
CandleStore.set_cached_mids(provider_name, mids, merge=True)
   │
   ▼
Store merges incoming mids into existing mids cache (preserves quiet coins)
```

### 2. Provider Injection in Strategy 2 Exit Cycle (Bug 3)
```
execute_extreme_screener_cycle()
   │
   ├── provider = get_market_data_provider(state["data_provider"])  (WS-connected runtime provider)
   │
   ├── Formation: get_extreme_setup_for_symbol(..., client=provider)  ✅
   │
   ├── Exit Monitor: get_last_n_candles(..., client=provider)         ✅ (Fixed: passes provider)
   │
   └── Chart Generation: get_last_n_candles(..., client=provider)     ✅ (Fixed: passes provider)
```

### 3. CCXT WebSocket Connection State Machine (Bug 4)
- When `start_websocket()` is called:
  - `_ws_running = True`
  - `_ws_connected = False` (remains `False` until connection verified)
- When any watcher loop (`_watch_tickers_loop`, `_watch_single_ticker_loop`, `_watch_ohlcv_loop`) yields a valid payload:
  - Sets `_ws_connected = True`
- When any loop catches an exception or disconnects:
  - Sets `_ws_connected = False`

### 4. Background Task Lifecycle & Concurrency in WS Clients (Bug 5)
- In `BinanceWSClient` and `HyperliquidWSClient`:
  - `self._background_tasks: Set[asyncio.Task] = set()`
  - `self._send_lock = asyncio.Lock()`
  - `update_subscriptions()`:
    - Schedules `task = asyncio.create_task(self._send_subscriptions())`
    - Adds `task` to `self._background_tasks`
    - Attaches `task.add_done_callback(self._background_tasks.discard)`
  - `_send_subscriptions()`:
    - Serializes message dispatch across concurrent calls using `async with self._send_lock:`
  - `stop()`:
    - Iterates over and cancels all pending tasks in `self._background_tasks`

### 5. Concurrent Non-Blocking Dashboard Broadcast (Bug 6)
- In `DashboardWSManager.broadcast(message)`:
  - Dispatches `asyncio.wait_for(ws.send_json(message), timeout=2.0)` concurrently across all active clients via `asyncio.gather(*[...], return_exceptions=True)`.
  - Collects client references that failed or timed out and removes them from `self.active_connections`.
