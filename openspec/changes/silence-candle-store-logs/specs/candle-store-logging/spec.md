# Capability Specification: CandleStore Logging and Symbol Deduplication

## Invariants

### 1. Log Level Invariant
- `CandleStore.merge_candles()` SHALL log candle merge operations at `DEBUG` level, not `INFO` level.
- Normal daemon and screener operation SHALL NOT emit per-tick candle merge log messages at `INFO` level.

### 2. Single Ingestion Invariant
- `BinanceWSClient` SHALL normalize kline stream symbols to base coin symbols (e.g. `BTCUSDT` -> `BTC`) before calling `merge_candles()`.
- `BinanceWSClient` SHALL NOT call `merge_candles()` with redundant dual entries (`BTCUSDT` and `BTC`) on every incoming kline message.

### 3. Read Backward Compatibility Invariant
- `CandleStore.get_candles()`, `is_fresh()`, and `has_sufficient_candles()` SHALL transparently resolve symbols ending with `"USDT"` to their base symbol if the suffixed key is not present.
