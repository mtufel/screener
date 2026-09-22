# OpenSpec Specification: 4H FVG Fallback & Symbol Normalization

## Invariants & Requirements

### 1. Provider Fallback Delegation
- **R1.1:** If `BinanceProvider` or `CcxtProvider` is rate-limited and the local `CandleStore` contains fewer than `min(n, 50)` candles for the requested symbol and timeframe, the provider MUST delegate the request to `self.fallback_provider` if one is configured.
- **R1.2:** If `BinanceProvider` receives an HTTP 418 or 429 response and the local `CandleStore` contains fewer than `min(n, 50)` candles, the provider MUST delegate to `self.fallback_provider` before returning partial/empty candles.
- **R1.3:** If fallback succeeds, the fetched candles MUST be merged into `CandleStore` for subsequent reads.

### 2. Symbol Normalization in 4H FVG Endpoint
- **R2.1:** `/api/extreme/4h-fvgs` MUST pass the canonical base coin symbol (e.g. `BTC`, `ETH`, `SOL`, `PAXG`) to `get_active_4h_fvgs_for_symbol`.
- **R2.2:** `htf_fvg_cache` keys MUST remain consistent between the background daemon (`screener_cycle`) and the public API `/api/extreme/4h-fvgs`.
- **R2.3:** If fewer than 3 4H candles are returned by the provider, the endpoint MUST NOT pass them to `get_active_4h_fvgs_for_symbol` as bootstrap/delta input, preserving cached and Redis-restored FVGs.

### 3. Non-Regression
- **R3.1:** When primary provider is healthy and not rate-limited, existing caching and delta query behavior remains unchanged.
- **R3.2:** Existing test suite (303 tests) must pass with 100% pass rate.
