# Tasks: Fix Real-Time WebSocket Streaming & Monitoring Bugs

- [x] Task 1: Fix Bug 1 — Use `merge=True` in `market_data/binance_ws.py` when setting cached mids
- [x] Task 2: Fix Bug 3 — Pass `client=provider` to `get_last_n_candles` in `main.py` lines 458 and 489
- [x] Task 3: Fix Bug 4 — Defer `_ws_connected = True` in `market_data/ccxt_provider.py` until first frame arrives
- [x] Task 4: Fix Bug 5 — Track background subscription tasks and serialize sends via lock in `market_data/binance_ws.py` and `market_data/hyperliquid_ws.py`
- [x] Task 5: Fix Bug 6 — Implement non-blocking concurrent broadcast with timeout in `DashboardWSManager` in `main.py`
- [x] Task 6: Implement comprehensive automated test suite `test_realtime_ws_fixes.py`
- [x] Task 7: Run test suite and full pytest regression suite to ensure 100% pass rate
