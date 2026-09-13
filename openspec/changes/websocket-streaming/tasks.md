# OpenSpec Tasks: Real-Time WebSocket Streaming for Market Data & Live Daemon

- [x] Task 1: Update `BaseMarketDataProvider` in `market_data/base.py` with WebSocket hooks
- [x] Task 2: Implement resilient `HyperliquidWSClient` in `market_data/hyperliquid_ws.py`
- [x] Task 3: Integrate `HyperliquidWSClient` into `HyperliquidProvider` in `market_data/hyperliquid.py`
- [x] Task 4: Implement resilient `BinanceWSClient` in `market_data/binance_ws.py` and connect in `BinanceProvider`
- [x] Task 5: Implement FastAPI WebSocket endpoint `/ws/extreme-live` and broadcaster in `main.py`
- [x] Task 6: Connect Dashboard UI in `templates/index.html` with WebSocket client and auto-fallback
- [x] Task 7: Add unit and integration tests for WebSocket clients, fallback, and endpoints
- [x] Task 8: Run full test suite (`pytest -v`) to verify 100% pass rate
- [x] Task 9: Check off tasks and update Walkthrough artifact

