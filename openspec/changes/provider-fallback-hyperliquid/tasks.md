# Implementation Tasks: Resilient Market Data Provider Fallback Chain

## Tasks

### Phase 1: Implement Fallback in BinanceProvider & OandaProvider
- [x] Task 1.1: Add `fallback_provider: Optional[BaseMarketDataProvider]` to `BinanceProvider` and `OandaProvider`.
- [x] Task 1.2: Implement automatic fallback in `get_last_n_candles`, `get_all_mids`, `get_historical_candles_range`, and `get_universe_coins`.

### Phase 2: Factory Wiring
- [x] Task 2.1: In `market_data_provider.py`, instantiate `BinanceProvider` and `OandaProvider` with `HyperliquidProvider` as fallback when `ENABLE_PROVIDER_FALLBACK` is True (default True).

### Phase 3: Unit Testing & Verification
- [x] Task 3.1: Add unit tests verifying fallback triggers when primary returns empty or errors.
- [x] Task 3.2: Run full test suite (`pytest -v`) to confirm 100% pass rate.
