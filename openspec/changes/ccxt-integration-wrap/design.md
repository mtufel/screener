## Design: ccxt-integration-wrap

### Architecture
- `ccxt_adapter.CcxtDataProvider`: wraps `get_market_data_provider()` + `ccxt.exchange`
- `ccxt_adapter.ExchangeAdapter`: implements `ohlcv()`, `ticker()`, `klines()` matching freqtrade `Exchange`
- `ccxt_ws.py`: websocket subscriber for live candle stream (ccxt `watchOHLCV`)
- Config: add `ccxt_exchange` key to existing config schema

### Integration Points
- `market_data_provider.py`: keep; adapter delegates to it on fallback
- `backtest_extreme_fvg.py`: uses adapter for data loading instead of HyperliquidClient directly
- `live_screener_extreme.py`: uses adapter for live feed
