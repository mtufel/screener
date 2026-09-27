# Specification: Market Data WebSockets & Live Streaming

## 1. Provider WebSocket Lifecycle
- Providers that support WebSockets MUST implement `start_websocket(symbols, timeframes)` and `stop_websocket()`.
- Providers MUST maintain `is_websocket_connected` property indicating current connection status.
- Providers MUST gracefully handle socket disconnects and auto-reconnect with exponential backoff.
- In-memory `CandleStore` MUST be updated directly on incoming WebSocket messages.

## 2. Transparent Fallback Invariant
- If `is_websocket_connected` is `False` or cached data in `CandleStore` is stale, `get_all_mids()` and `get_last_n_candles()` MUST fall back to REST HTTP calls without throwing exceptions to callers.
- Calling `stop_websocket()` MUST cleanly close the connection and terminate background streaming tasks.

## 3. UI WebSocket Invariant
- The Dashboard UI MUST attempt connecting to `/ws/extreme-live`.
- If connected, UI MUST update setups, trade history, and KPI ribbons in real-time upon message receipt.
- If disconnected, UI MUST automatically fallback to periodic HTTP polling.
