# OpenSpec Proposal: Fix 4H FVG Detection & Provider Fallback Delegation

## Why
In production environments (e.g. Render), Binance frequently issues HTTP 418 rate-limit IP bans against shared hosting IP ranges. When this occurs, `BinanceProvider` enters rate-limit cooldown.
However, `get_last_n_candles` in `BinanceProvider` and `CcxtProvider` contained an incomplete-cache check:
```python
if self._store.is_rate_limited(self.name):
    if cached:
        return cached
    if self.fallback_provider:
        ...
```
Because the WebSocket feed streams the current open candle into `CandleStore`, `cached` contains **1 candle**. The check `if cached:` evaluated to `True`, returning the single candle and completely bypassing the configured fallback provider (`Hyperliquid`). Because Fair Value Gap (FVG) detection requires at least 3 consecutive candles, 1 candle always produced 0 FVGs.

Furthermore, in `/api/extreme/4h-fvgs`, the endpoint passed the exchange-resolved symbol (e.g. `BTCUSDT`, `ETHUSDT`) to `get_active_4h_fvgs_for_symbol` instead of the base symbol (`BTC`, `ETH`). This caused cache misses against the daemon's internal `htf_fvg_cache`, overwriting the cache entry with an empty FVG list when supplied with 1 candle.

## What Changes
1. **Fallback Provider Delegation Guard (`market_data/binance.py`, `market_data/ccxt_provider.py`)**:
   - Check `len(cached) >= min(n, 50)` before serving cached bars under rate limits.
   - If `len(cached) < min(n, 50)`, delegate to `self.fallback_provider` (Hyperliquid) to fetch the full candle history.
   - Also handle HTTP 418/429 response branches by delegating to fallback provider when cache is insufficient.
2. **Canonical Symbol Normalization (`api/extreme.py`)**:
   - Use the canonical base symbol `sym` (e.g. `"BTC"`, `"ETH"`) when querying `get_active_4h_fvgs_for_symbol`.
   - Pass `candles_4h` only if `len(candles_4h) >= 3`. If insufficient, allow `get_active_4h_fvgs_for_symbol` to retrieve active FVGs from `htf_fvg_cache` / Redis or via the provider with fallback.
3. **Automated Regression Suite (`test_4h_fvg_fallback_resilience.py`)**:
   - Verify fallback delegation when primary provider is rate-limited and cache has insufficient (< 50) bars.
   - Verify `/api/extreme/4h-fvgs` endpoint returns unmitigated 4H FVGs with anchor indicators even when primary provider is rate-limited.
   - Verify symbol key compatibility across screener daemon and API endpoint.

## Impact
- **Zero Breaking Changes:** API contracts, payload shapes, and existing strategy parameters remain identical.
- **Resilience:** Seamless automatic failover to Hyperliquid when Binance is IP-banned or rate-limited on cloud deployments.
- **Data Integrity:** UI and API consumers reliably receive active 4H FVGs.
