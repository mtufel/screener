# Design Document: Modular Market Data & Cache Architecture Segregation

## Architecture Diagram

```
+--------------------------------------------------------------------------+
|                              Application                                  |
|   (main.py, strategy_extreme_fvg.py, extreme_trade_tracker.py, tests)    |
+------------------------------------+-------------------------------------+
                                     |
                                     v
+--------------------------------------------------------------------------+
|                  market_data_provider.py (Facade & Factory)               |
|      - get_market_data_provider(name) -> BaseMarketDataProvider          |
|      - close_all_providers()                                             |
|      - Re-exports: CandleStore, BinanceProvider, OandaProvider, etc.     |
+-------------------+--------------------+-------------------+-------------+
                    |                    |                   |
                    v                    v                   v
           +------------------+ +------------------+ +-------------------+
           | market_data/     | | market_data/     | | market_data/      |
           | binance.py       | | oanda.py         | | hyperliquid.py    |
           | BinanceProvider  | | OandaProvider    | | HyperliquidProv   |
           +--------+---------+ +--------+---------+ +---------+---------+
                    |                    |                     |
                    +--------------------+---------------------+
                                         |
                                         v
                         +--------------------------------+
                         |        candle_store.py         |
                         |   - CandleStore                |
                         |   - candle_store (singleton)   |
                         |   - TIMEFRAME_MS               |
                         |   - Rate-limit cooldown state  |
                         +--------------------------------+
```

## Module Segregation & Boundaries

1. **`candle_store.py`**:
   - Class `CandleStore`
   - Global singleton `candle_store`
   - Constant `TIMEFRAME_MS`
   - Pure domain: storage and caching. No direct network I/O or provider-specific API logic.

2. **`market_data/base.py`**:
   - `BaseMarketDataProvider` (ABC)
   - Interface definition: `name`, `get_all_mids()`, `get_last_n_candles()`, `get_historical_candles_range()`, `get_universe_coins()`, `resolve_symbol()`, `close()`.

3. **`market_data/binance.py`**:
   - `BinanceProvider`
   - Binance specific endpoint handling (`/fapi/v1/klines`, `/fapi/v1/ticker/price`, `/api/v3/klines`).
   - Binance symbol normalizer and alias mapping.
   - Delta querying and rate limit cooldown triggering on 418/429.

4. **`market_data/oanda.py`**:
   - `OandaProvider`
   - `_oanda_rfc3339_to_ms` helper.
   - OANDA v20 REST API client, instrument formatting, pricing endpoint parsing.

5. **`market_data/hyperliquid.py`**:
   - `HyperliquidProvider`
   - Adapter connecting `HyperliquidClient` with `BaseMarketDataProvider` and `CandleStore`.

6. **`market_data_provider.py`**:
   - Factory `get_market_data_provider()`, `close_all_providers()`, active default `market_data_provider`.
   - Backward-compatibility re-exports.
