# Proposal: Real-Time WebSocket Streaming for Market Data & Live Daemon

## Problem Statement
The current system relies predominantly on duration-based polling (e.g. `poll_interval_seconds: 30`, `extreme_interval_seconds: 30`, `trade_monitor_worker: 30s sleep`, and frontend `setInterval` 10s HTTP requests).
This pull-based architecture presents two major challenges:
1. **Latency & Execution Inaccuracy**: An open trade can touch TP or breach SL, but the daemon might take up to 30 seconds to wake up, fetch prices via HTTP, and record the fill or exit.
2. **API Quota & Rate Limit Risks**: Constant periodic polling of `allMids` and candle snapshots via REST HTTP calls risks triggering 429 rate limits, especially during volatile market movements.

## Proposed Solution
Replace periodic polling with persistent, event-driven **WebSockets** where supported:
1. **Hyperliquid Market Data**: Connect to `wss://api.hyperliquid.xyz/ws` and subscribe to `allMids` and active whitelist `candle` streams. Stream real-time prices directly into `CandleStore`.
2. **Binance Market Data**: Connect to `wss://fstream.binance.com/ws` and subscribe to `!miniTicker@arr` and kline streams into `CandleStore`.
3. **Resilient REST Fallback**: If a provider does not support WebSockets (e.g., OANDA), or if the WebSocket connection drops and is attempting reconnection, the system transparently falls back to REST queries with zero data loss or downtime.
4. **Event-Driven Trade Monitoring**: Monitor open trades against the continuously refreshed in-memory cache with sub-second responsiveness instead of static 30s sleep cycles.
5. **Dashboard Web UI**: Provide a FastAPI WebSocket endpoint (`/ws/extreme-live`) pushing trade updates and scanner state to connected browsers in real-time, eliminating frontend `setInterval` polling.

## Scope & Impact
- **Modules Affected**:
  - `market_data/base.py`: Adds optional WebSocket lifecycle hooks.
  - `market_data/hyperliquid_ws.py` & `market_data/binance_ws.py`: Resilient async WebSocket client implementations.
  - `market_data/hyperliquid.py` & `market_data/binance.py`: Integrates WebSocket streams with seamless REST fallback.
  - `main.py`: WebSocket server endpoint and lifespan background streaming task.
  - `templates/index.html`: Client WebSocket connection with HTTP fallback.
