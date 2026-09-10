# Specification: Market Data Modularity & Single Responsibility

## Requirements

1. **Storage Isolation**:
   - `CandleStore` MUST reside in its own dedicated module (`candle_store.py`).
   - `CandleStore` MUST NOT depend on exchange REST APIs or network clients.

2. **Provider Isolation**:
   - `BaseMarketDataProvider` MUST reside in `market_data/base.py`.
   - `BinanceProvider` MUST reside in `market_data/binance.py`.
   - `OandaProvider` MUST reside in `market_data/oanda.py`.
   - `HyperliquidProvider` MUST reside in `market_data/hyperliquid.py`.

3. **Backward Compatibility**:
   - `market_data_provider.py` MUST re-export `CandleStore`, `candle_store`, `TIMEFRAME_MS`, `BaseMarketDataProvider`, `BinanceProvider`, `OandaProvider`, `HyperliquidProvider`, `get_market_data_provider`, `market_data_provider`, `close_all_providers`, and `_oanda_rfc3339_to_ms`.
   - All existing imports across test files and screener modules MUST continue to work without modification.
