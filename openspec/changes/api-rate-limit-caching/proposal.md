# OpenSpec Proposal: API Rate Limit Mitigation & In-Memory Market Data Caching

## 1. Problem Statement
The Binance market data provider and Strategy 2 screening loop currently flood exchange REST endpoints with duplicate and redundant HTTP requests every 30-second cycle:
- For a single coin, `strategy_extreme_fvg.py` requests identical 4H and 5m candles 2 to 3 times within milliseconds.
- `main.py` makes additional duplicate 5m candle fetches for trade tracking and chart generation.
- No in-memory TTL caching exists at the market data provider layer (`market_data_provider.py`), causing raw network hits on every call.
- This quickly exhausts Binance's IP rate limit quota, leading to HTTP 418 / 429 IP bans (`code: -1003`).

## 2. Proposed Solution
1. **Network-Layer In-Memory TTL Cache in `market_data_provider.py`**:
   - Cache `get_last_n_candles` and `get_all_mids` with a short TTL (e.g. 5–10s) across all providers (Binance, OANDA, Hyperliquid).
   - Cache key: `(provider_name, symbol, timeframe, limit)`.
   - Thread-safe and asyncio-safe caching.
2. **Strategy Pipeline Request Deduplication in `strategy_extreme_fvg.py`**:
   - Refactor `get_touched_4h_fvg_for_symbol` and `find_active_extreme_setup_for_symbol` to share candle results rather than issuing duplicate calls.
3. **Ledger & Screener Cycle Candle Reuse in `main.py`**:
   - Store and reuse fetched LTF candles in `recent_candles_map` during the cycle.
4. **418/429 Rate Limit Throttling**:
   - Handle rate limit error codes gracefully with exponential backoff and backoff cooldown.

## 3. Expected Impact
- Eliminates 70–80% of all REST API requests per scan cycle.
- Prevents IP bans on Binance and other providers.
- Sub-millisecond response times for cached candle lookups.
- 100% test pass rate with automated unit and integration tests.
