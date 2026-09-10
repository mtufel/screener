# Change Proposal: Resilient Market Data Provider Fallback Chain (Hyperliquid Fallback)

## Problem Statement
When the primary market data provider (e.g., Binance or OANDA) encounters network outages, IP rate limiting (HTTP 418/429), or missing instrument data, requests currently return empty responses or fail.

## Proposed Solution
Implement a config-driven fallback mechanism (`FALLBACK_DATA_PROVIDER`, defaulting to `hyperliquid`):
1. **Config-Driven**: Controlled via `FALLBACK_DATA_PROVIDER` env var / runtime configuration (options: `hyperliquid`, `binance`, `none`).
2. `get_last_n_candles`: If primary provider returns empty or is rate limited with no cache, query configured fallback provider and log fallback activation.
3. `get_all_mids`: If primary provider fails to retrieve mid prices, query fallback provider.
4. `get_historical_candles_range`: If primary provider fails to retrieve historical ranges, query fallback provider's deep historical archive.
5. `get_universe_coins`: If primary universe query fails, fallback to fallback provider's universe.

## Invariants & Impact
- Non-blocking: Primary provider is always tried first; fallback only activates on genuine failure or empty data.
- Transparency: Every fallback trigger is clearly logged with `[ProviderFallback]` tags.
- Zero breaking changes to existing signatures and 100% test pass rate.
