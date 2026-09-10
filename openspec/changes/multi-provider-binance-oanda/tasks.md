# Implementation Tasks: Multi-Provider Market Data Architecture

## Tasks

### Phase 1: Provider Abstraction & Base Interface
- [x] Task 1.1: Create `market_data_provider.py` with `BaseMarketDataProvider` abstract class and standard factory function `get_market_data_provider()`.

### Phase 2: Binance Provider Implementation
- [x] Task 2.1: Implement `BinanceProvider` with Binance Futures & Spot endpoints, bulk mid prices, symbol normalization, and deep kline pagination.

### Phase 3: OANDA Provider Implementation
- [x] Task 3.1: Implement `OandaProvider` with OANDA v20 REST endpoints for candles (`/v3/instruments/{instrument}/candles`), pricing (`/v3/accounts/{accountID}/pricing`), and CFD commodity/forex symbol mapping.

### Phase 4: Strategy & Main Application Integration
- [x] Task 4.1: Adapt `HyperliquidProvider` inside `market_data_provider.py`.
- [x] Task 4.2: Update `strategy_extreme_fvg.py`, `strategy.py`, `extreme_trade_tracker.py`, and `main.py` to use `market_data_provider`.
- [x] Task 4.3: Expose active provider in `/api/extreme/config` and allow runtime switching.

### Phase 5: Testing & Verification
- [x] Task 5.1: Create comprehensive unit test suite `test_market_data_providers.py` for Binance, OANDA, and Hyperliquid providers.
- [x] Task 5.2: Run full test suite (`pytest -v`) to confirm 100% pass rate.

