# OpenSpec Capability Specification: Real-Time WebSocket Fixes

## Invariants

### Invariant 1: Binance WS Mid Price Merging
- Ingestion of a miniTicker array message SHALL call `CandleStore.set_cached_mids` with `merge=True`.
- Symbols already present in the mids cache for that provider SHALL NOT be removed or zeroed out when an update contains an unrelated subset of symbols.

### Invariant 2: Strategy 2 Exit Monitor Provider Consistency
- In `execute_extreme_screener_cycle()`, `get_last_n_candles` calls for building `recent_candles_map` and for trade alert charts SHALL receive `client=provider`.
- Exit monitoring candle data SHALL originate from the same provider instance utilized for formation scanning.

### Invariant 3: CCXT Provider Connection State
- `CcxtProvider.is_websocket_connected` SHALL evaluate to `False` immediately following `start_websocket()`.
- `CcxtProvider.is_websocket_connected` SHALL evaluate to `True` only after at least one ticker or OHLCV frame is received.

### Invariant 4: Robust Subscription Task Management
- In `BinanceWSClient` and `HyperliquidWSClient`, any task spawned by `update_subscriptions` SHALL be retained in a background tasks collection until completion.
- `_send_subscriptions()` SHALL acquire an `asyncio.Lock` before sending messages over the WebSocket protocol.
- Calling `stop()` SHALL cancel all pending background subscription tasks.

### Invariant 5: Non-blocking Dashboard Broadcast
- `DashboardWSManager.broadcast()` SHALL transmit updates to all connected browser sockets concurrently.
- If a socket stalls, `broadcast()` SHALL time out after 2.0 seconds for that socket and proceed without stalling other clients or blocking caller execution.
- Disconnected or timed-out sockets SHALL be pruned from `active_connections`.
