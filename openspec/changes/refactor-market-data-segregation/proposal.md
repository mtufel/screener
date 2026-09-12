# Change Proposal: Modular Market Data & Cache Architecture Segregation

## Problem Statement
Previously, `market_data_provider.py` accumulated multiple distinct domain responsibilities into a single ~950 line monolithic file:
1. In-memory candle storage, rolling buffer, delta merging, and timestamp indexing (`CandleStore`).
2. Ticker price cache and rate-limit cooldown management.
3. Base abstract market data provider interface (`BaseMarketDataProvider`).
4. Binance REST client implementation (`BinanceProvider`).
5. OANDA v20 REST client implementation (`OandaProvider`).
6. Hyperliquid Perpetual client adapter (`HyperliquidProvider`).
7. Provider factory and singleton lifecycle management (`get_market_data_provider`).

This violates the Single Responsibility Principle (SRP) and creates tight coupling.

## Proposed Solution
Segregate responsibilities into dedicated, modular single-purpose files:
1. **`candle_store.py`**:
   - `CandleStore` class and `candle_store` global singleton.
   - Timeframe millisecond constants (`TIMEFRAME_MS`).
   - Responsibility: High-speed in-memory OHLCV storage, delta merging, capacity truncation, freshness evaluation, mids caching, and rate-limit cooldown tracking.
2. **`market_data/` (or dedicated provider modules)**:
   - `market_data_base.py` / `market_data/base.py`: Abstract Base Class `BaseMarketDataProvider`.
   - `market_data/binance.py`: `BinanceProvider` (Futures & Spot, symbol normalizer, kline pagination, delta queries, rate limit detection).
   - `market_data/oanda.py`: `OandaProvider` (OANDA v20 REST API, multi-asset commodity/forex normalizer, RFC3339 timestamp parser).
   - `market_data/hyperliquid.py`: `HyperliquidProvider` (Adapter wrapping `HyperliquidClient`).
3. **`market_data_provider.py` (Facade & Factory)**:
   - Imports from modular sub-components and exposes unified factory `get_market_data_provider()`, active singleton `market_data_provider`, and cleanup functions.
   - Re-exports all public symbols for 100% backward compatibility with existing tests and imports.

## Impact & Invariants
- Zero breaking changes to existing callers (`main.py`, `strategy_extreme_fvg.py`, `test_*.py`).
- 100% automated test suite pass rate preserved.
