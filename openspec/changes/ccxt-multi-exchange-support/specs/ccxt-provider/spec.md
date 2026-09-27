# Specification: CCXT & CCXT Pro Unified Market Data Provider

## Requirement 1: Provider Selection & Exchange Configuration
1. The system SHALL support selecting CCXT via `DATA_PROVIDER=ccxt` or passing `provider="ccxt"` to `get_market_data_provider()`.
2. The specific exchange SHALL be configured via `CCXT_EXCHANGE` (default: `"binance"`). Any exchange supported by `ccxt.async_support` and `ccxt.pro` (e.g., `"bybit"`, `"okx"`, `"binance"`, `"hyperliquid"`, `"gateio"`, `"kraken"`) SHALL be accepted.
3. The provider SHALL support passing optional exchange parameters (API key, secret, testnet, defaultType) through environment variables or constructor kwargs.

## Requirement 2: Unified Interface Implementation
`CcxtProvider` SHALL inherit from `BaseMarketDataProvider` and satisfy all abstract methods:
1. `get_all_mids()`: SHALL query tickers via CCXT REST `fetch_tickers()` or return in-memory cached mids from `CandleStore` when streaming is active. Mids dictionary SHALL map normalized base symbols (e.g., `"BTC"`, `"ETH"`) to float prices.
2. `get_last_n_candles(symbol, timeframe, n)`: SHALL return the last `n` `Candle` objects in ascending chronological order, utilizing in-memory cached candles from `CandleStore` if fresh, or falling back to CCXT REST `fetch_ohlcv()`.
3. `get_historical_candles_range(symbol, timeframe, start_time, end_time)`: SHALL fetch historical `Candle` objects over the requested timestamp range using `fetch_ohlcv()` with automatic pagination.
4. `get_universe_coins()`: SHALL return available active coins/symbols from the exchange markets.

## Requirement 3: Real-Time Streaming Lifecycle (`ccxt.pro`)
1. `supports_websocket` SHALL return `True` if `ccxt.pro` is available for the configured exchange.
2. `start_websocket(symbols, timeframes)`:
   - SHALL launch asynchronous background streaming tasks for ticker updates (`watch_tickers` or `watch_ticker`) and candle updates (`watch_ohlcv`).
   - Received tickers SHALL update `CandleStore.set_cached_mids()`.
   - Received klines/candles SHALL update `CandleStore.merge_candles()`.
   - Reconnections and transient network disconnects SHALL be handled gracefully without crashing the screener or monitor processes.
3. `stop_websocket()`: SHALL gracefully cancel streaming tasks and close active WebSocket connections via `exchange.close()`.
4. `is_websocket_connected`: SHALL return `True` when streaming tasks are active and receiving updates.

## Requirement 4: Symbol & Timeframe Normalization
1. Standard timeframes (`"1m"`, `"5m"`, `"15m"`, `"1h"`, `"4h"`, `"1d"`) SHALL map to CCXT timeframe strings.
2. Normalized symbol aliases:
   - Base coins like `"BTC"` SHALL map to exchange CCXT symbol (e.g. `"BTC/USDT"` or `"BTC/USDT:USDT"` for swap).
   - Commodity tokens like `"GOLD"` SHALL map to `"PAXG/USDT"` or `"XAU/USDT"` if available on the exchange.
3. Candle models produced SHALL be standard `Candle` dataclass instances matching the structure produced by `HyperliquidProvider` and `BinanceProvider`.

## Requirement 5: Dual Engine Interoperability
1. Both Direct Native Providers (`HyperliquidProvider`, `BinanceProvider`) and `CcxtProvider` SHALL feed the exact same `CandleStore`.
2. Extreme and Standard FVG screeners, `ExtremeTradeTracker`, and the FastAPI WebSocket hub (`/ws/extreme-live`) SHALL work unchanged regardless of whether native or CCXT provider is active.
