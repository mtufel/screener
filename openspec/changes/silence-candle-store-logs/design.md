# Design: CandleStore Log Level Demotion & Symbol Normalization

## Architecture & Data Flow

### Before
```
Binance WS Kline
  │
  ├──► CandleStore.merge_candles("binance_futures", "BTCUSDT", tf, candle) ──► logger.info("[CandleStore] [BINANCE_FUTURES:BTCUSDT:5m] ...")
  └──► CandleStore.merge_candles("binance_futures", "BTC", tf, candle)     ──► logger.info("[CandleStore] [BINANCE_FUTURES:BTC:5m] ...")
```
Total logs: 2 INFO messages per tick.

### After
```
Binance WS Kline
  │
  └──► target_sym = sym[:-4] if sym.endswith("USDT") else sym
       │
       └──► CandleStore.merge_candles("binance_futures", "BTC", tf, candle) ──► logger.debug("[CandleStore] [BINANCE_FUTURES:BTC:5m] ...")
```
Total logs: 0 INFO messages in production (available in DEBUG).

### Transparent Fallback in CandleStore
If any component queries `CandleStore.get_candles(provider, "BTCUSDT", tf)`:
1. First check for exact key `binance_futures:BTCUSDT:5m`.
2. If absent and symbol ends with `"USDT"`, fall back to `binance_futures:BTC:5m`.
3. Return candles seamlessly.
