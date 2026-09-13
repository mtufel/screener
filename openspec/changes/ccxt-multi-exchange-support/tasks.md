# Tasks: CCXT & CCXT Pro Unified Market Data Provider

- [x] 1. Complete OpenSpec documentation (`proposal.md`, `design.md`, `spec.md`, `tasks.md`) <!-- id: 0 -->
- [x] 2. Implement `market_data/ccxt_provider.py` <!-- id: 1 -->
  - [x] 2.1 Define `CcxtProvider(BaseMarketDataProvider)` with `ccxt.async_support` and `ccxt.pro` adapters <!-- id: 2 -->
  - [x] 2.2 Implement symbol and timeframe normalization <!-- id: 3 -->
  - [x] 2.3 Implement REST endpoints: `get_all_mids()`, `get_last_n_candles()`, `get_historical_candles_range()`, `get_universe_coins()` <!-- id: 4 -->
  - [x] 2.4 Implement WebSocket streaming: `start_websocket()`, `stop_websocket()`, writing to `CandleStore` <!-- id: 5 -->
- [x] 3. Register `ccxt` in `market_data_provider.py` and export in `market_data/__init__.py` <!-- id: 6 -->
- [x] 4. Update `.env.example` with CCXT configuration (`DATA_PROVIDER=ccxt`, `CCXT_EXCHANGE=binance`) <!-- id: 7 -->
- [x] 5. Implement automated unit and integration tests in `test_ccxt_provider.py` <!-- id: 8 -->
- [x] 6. Run full test suite (`pytest -v`) and verify 100% pass rate <!-- id: 9 -->
- [x] 7. Update OpenSpec checklist and walkthrough <!-- id: 10 -->
