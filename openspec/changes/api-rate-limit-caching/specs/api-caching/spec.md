# Capability Specification: Market Data In-Memory Caching & Request Deduplication

## Requirements

### Requirement 1: In-Memory Candle Caching
- `market_data_provider.get_last_n_candles` MUST cache results in memory for a configurable TTL (default 5.0 seconds).
- Repeated calls with identical `(symbol, timeframe, limit)` within the TTL MUST return cached candles without making a network HTTP call.

### Requirement 2: Midpoint Price Caching
- `market_data_provider.get_all_mids` MUST cache results in memory for a configurable TTL (default 3.0 seconds).

### Requirement 3: Pipeline Request Deduplication
- Within a single execution cycle of `get_extreme_setup_for_symbol`, 4H candles and LTF candles for any given symbol MUST NOT be requested over the network more than once.

### Requirement 4: Rate Limit Backoff Handling
- If Binance returns HTTP 418 or 429, the provider MUST activate a cooldown period and suppress further network requests to that provider until cooldown expires, preventing log flooding and immediate connection resets.
