# Tasks: Silence CandleStore Merge Log Noise and Normalize Binance WebSocket Symbols

- [x] 1. Core CandleStore Enhancements
  - [x] 1.1 Demote `logger.info` to `logger.debug` in `CandleStore.merge_candles()`.
  - [x] 1.2 Add transparent `-USDT` fallback in `CandleStore.get_candles()`, `is_fresh()`, and `has_sufficient_candles()`.
- [x] 2. BinanceWSClient Normalization
  - [x] 2.1 Update `market_data/binance_ws.py` to normalize kline stream symbols to base coin (`BTCUSDT` -> `BTC`) before merging.
- [x] 3. Automated Verification
  - [x] 3.1 Verify WebSocket streaming tests (`test_websocket_streaming.py`, `test_realtime_ws_fixes.py`).
  - [x] 3.2 Run full test suite (`pytest -v`) to verify 100% pass rate.
