# Proposal: Config-Driven Multi-Provider Market Data Architecture (Binance & OANDA)

## 1. Problem Statement
Currently, the crypto FVG screener relies primarily on Hyperliquid's Info API for live price feeds, universe discovery, and kline historical data. While Hyperliquid is lightweight and keyless, it has limitations:
1. **Rolling Node Depth**: Historical klines on 1m/5m/15m are capped to 200–500 recent snapshots, requiring awkward fallbacks for deep backtesting.
2. **Pricing & Alignment**: Many traders analyze charts primarily on Binance (`BTCUSDT.P`) or trade traditional commodities/forex on institutional brokers like OANDA (`XAU_USD` Gold, `WTICO_USD` Crude Oil, Forex majors).
3. **Hardcoded Exchange Coupling**: The strategy engine, backtester, and background daemon are tightly coupled to Hyperliquid data structures rather than a swappable, config-driven provider abstraction.

## 2. Proposed Solution
Design and implement a clean, extensible **Market Data Provider Abstraction Layer**:
1. **`BaseMarketDataProvider` Abstract Interface**: Standardizes `get_candles()`, `get_all_mids()`, `get_universe_coins()`, and `resolve_symbol()`.
2. **Binance Provider (`BinanceProvider`)**:
   - High-performance, keyless public access to Binance Futures (`fapi.binance.com`) and Spot (`api.binance.com`).
   - Unlimited historical candle retrieval across 1m, 5m, 15m, 1h, 4h.
   - Bulk ticker price resolution in a single HTTP call.
3. **OANDA Provider (`OandaProvider`)**:
   - Institutional multi-asset CFD feed via OANDA v20 REST API (`api-fxtrade.oanda.com` / `api-fxpractice.oanda.com`).
   - Native support for Gold (`XAU_USD`), Silver (`XAG_USD`), Crude Oil (`WTICO_USD`, `BCO_USD`), and Forex pairs.
   - Granular candle querying (`M1`, `M5`, `M15`, `H1`, `H4`) and pricing endpoints.
4. **Hyperliquid Provider (`HyperliquidProvider`)**:
   - Preserves existing decentralized perpetual DEX integration.
5. **Config-Driven Dispatcher**:
   - Swappable via environment variable `DATA_PROVIDER="binance"` | `"oanda"` | `"hyperliquid"`.
   - Runtime switching via `/api/extreme/config?provider=binance`.
   - Optional automatic symbol-routing (e.g. route commodities like `WTIOIL`/`XAU` to OANDA, crypto to Binance).

## 3. Impact & Backward Compatibility
- 100% backward compatible: Existing configurations default seamlessly without breaking tests.
- Full test suite verification across all providers with mock and live integration tests.
