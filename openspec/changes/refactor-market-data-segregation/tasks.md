# Implementation Tasks: Modular Market Data & Cache Architecture Segregation

## Tasks

### Phase 1: Storage Layer Segregation
- [x] Task 1.1: Create `candle_store.py` with `CandleStore`, `candle_store` singleton, and `TIMEFRAME_MS`.

### Phase 2: Provider Layer Segregation
- [x] Task 2.1: Create `market_data/base.py` with `BaseMarketDataProvider`.
- [x] Task 2.2: Create `market_data/binance.py` with `BinanceProvider`.
- [x] Task 2.3: Create `market_data/oanda.py` with `OandaProvider` and `_oanda_rfc3339_to_ms`.
- [x] Task 2.4: Create `market_data/hyperliquid.py` with `HyperliquidProvider`.
- [x] Task 2.5: Create `market_data/__init__.py` exporting all subcomponents.

### Phase 3: Facade & Factory Refactoring
- [x] Task 3.1: Refactor `market_data_provider.py` into a clean facade importing and re-exporting submodules.

### Phase 4: Testing & Verification
- [x] Task 4.1: Run full test suite (`pytest -v`) to confirm 100% pass rate.
