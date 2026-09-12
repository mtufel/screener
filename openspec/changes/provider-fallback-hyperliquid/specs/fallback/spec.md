# Specification: Market Data Fallback Chain

## Requirements

1. **Automatic Fallback on Failure**:
   - When `BinanceProvider` or `OandaProvider` fails to fetch candles for a crypto asset, it MUST attempt fallback to `HyperliquidProvider`.
   - When `get_all_mids` returns empty or partial data from primary, missing crypto symbols MUST be backfilled from `HyperliquidProvider`.
   - When `get_historical_candles_range` returns empty for a crypto asset, it MUST query `HyperliquidProvider`.

2. **Logging Invariant**:
   - When fallback occurs, a warning log MUST be emitted: `[ProviderFallback] Primary {primary} failed for {symbol} ({operation}), falling back to {fallback}`.

3. **Pass-Through Non-Crypto Guard**:
   - For non-crypto instruments exclusive to OANDA (e.g., WTIOIL, EUR_USD), fallback should be skipped gracefully if not available on Hyperliquid.
