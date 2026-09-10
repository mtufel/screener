# Design Document: Resilient Market Data Provider Fallback Chain

## Fallback Flow Diagram

```
+-------------------------------------------------------------+
|                      Provider Call                          |
| (get_all_mids / get_last_n_candles / get_historical_range)   |
+------------------------------+------------------------------+
                               |
                               v
             +----------------------------------+
             | Try Primary Provider (e.g.       |
             | Binance / OANDA / etc.)          |
             +-----------------+----------------+
                               |
                 +-------------+-------------+
                 |                           |
                 v                           v
         [Success / Valid Data]     [Failure / Empty / Rate Limited]
                 |                           |
                 v                           v
         Return Result              +----------------------------------+
                                    | Log [ProviderFallback] Warning   |
                                    | Query HyperliquidProvider        |
                                    +----------------+-----------------+
                                                     |
                                                     v
                                            Return Fallback Result
```

## Implementation Strategy
1. **`FallbackProvider` Wrapper or Integrated Fallback in `BinanceProvider` / `OandaProvider`**:
   - Allow providers to take an optional `fallback_provider` (defaulting to `HyperliquidProvider`).
   - When a primary call returns empty or enters rate limit cooldown with no cached data, automatically delegate to `fallback_provider`.
2. **CandleStore Consistency**:
   - Merge fallback candles into `CandleStore` under the primary key so subsequent reads hit the store seamlessly.
