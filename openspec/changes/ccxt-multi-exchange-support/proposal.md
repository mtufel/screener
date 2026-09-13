# Proposal: Dual Market Data Engine (Direct Native WebSockets + CCXT / CCXT Pro)

## Problem Statement
While direct native WebSocket clients provide ultra-low latency (<50ms) and minimal overhead for specific exchanges like Hyperliquid and Binance, expanding the screener and strategy engines to other popular cryptocurrency exchanges (such as Bybit, OKX, Kraken, Gate.io, and Coinbase) would typically require writing and maintaining bespoke WebSocket and REST protocol implementations for every single exchange.

## Proposed Solution
Support a **dual market data engine** under the unified `BaseMarketDataProvider` architecture:
1. **Direct Native Providers**: Ultra-lean, zero-setup WebSocket adapters for Hyperliquid and Binance.
2. **CCXT Provider (`CcxtProvider`)**: A pluggable multi-exchange provider leveraging `ccxt.async_support` for unified REST and `ccxt.pro` for unified WebSocket streaming across 100+ exchanges, selected via `DATA_PROVIDER=ccxt` and `CCXT_EXCHANGE=<exchange_id>`.
3. **Common In-Memory Store (`CandleStore`)**: Both native and CCXT market data streams pipe into `CandleStore`, allowing all strategy rules, FVG detectors, trade lifecycles, and web dashboard feeds to remain 100% exchange-agnostic.

## Scope & Impact
- `market_data/ccxt_provider.py`: Implements `CcxtProvider` supporting REST + CCXT Pro streaming.
- `market_data_provider.py`: Registers `ccxt` in `get_market_data_provider()` with fallback delegation.
- `.env.example`: Documents `CCXT_EXCHANGE` configuration options.
- `test_ccxt_provider.py`: Automated unit and integration test coverage.
