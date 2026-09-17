# Change Proposal: Silence CandleStore Merge Log Noise and Normalize Binance WebSocket Symbols

## 1. Problem Statement
1. **High-Frequency Log Spam:** `CandleStore.merge_candles` currently logs at `INFO` level:
   ```
   [CandleStore] [BINANCE_FUTURES:SOL:5m] Merged 1 incoming candle(s) -> Store holds 300 bars ...
   ```
   When WebSocket streaming is active across 3 coins (BTC, ETH, SOL) and 5 timeframes (1m, 5m, 15m, 1h, 4h), 15 to 30 candle ticks arrive every second. Logging every tick at `INFO` floods the terminal console and obscures critical strategy lifecycle events (scans, setups, trade fills, TP/SL alerts).
2. **Duplicate Ingestion for Raw vs Base Symbols:** In `market_data/binance_ws.py`, incoming kline messages are merged twice: once under the raw exchange symbol (`BTCUSDT`) and once under the stripped base symbol (`BTC`). This duplicates storage, creates disconnected 1-bar entries (`BTCUSDT` holding 1 bar vs `BTC` holding 300 bars), and doubles the log noise.

## 2. Proposed Solution
1. **Demote Candle Ingestion Logging to `DEBUG`:**
   Change `logger.info` in `CandleStore.merge_candles` to `logger.debug`. Production console logs will stay clean while preserving full observability when `DEBUG` logging is enabled.
2. **Canonical Symbol Storage in `binance_ws.py`:**
   Normalize kline symbol names directly to base coin symbols (e.g. `BTCUSDT` -> `BTC`) before calling `merge_candles()`.
3. **Transparent Suffix Fallback in `CandleStore`:**
   In `CandleStore.get_candles()`, `is_fresh()`, and `has_sufficient_candles()`, provide transparent fallback to base symbols if queried with an `-USDT` suffix.

## 3. Impact & Risk Analysis
- **Non-Breaking:** All existing tests and strategy candle consumption continue using base symbols (`BTC`, `ETH`, `SOL`).
- **Performance:** Reduces CPU/IO consumption spent formatting and writing dozens of log messages per second.
- **Log Hygiene:** The application console will focus purely on daemon cycles, alerts, and system health.
