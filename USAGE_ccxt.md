# ccxt Adapter Usage

## Wire (already done)
- market_data_provider.py imports CcxtDataProvider when CCXT_EXCHANGE env set
- Falls back to HyperliquidClient / Binance / Oanda automatically

## Usage
```bash
export CCXT_EXCHANGE=binance
python live_screener_extreme.py --ltf 5m
```

## Backtest
```python
from ccxt_adapter import CcxtDataProvider
dp = CcxtDataProvider({"exchange": "binance"})
df = dp.ohlcv("BTC/USDT", "5m")
```

## Websocket live
```python
from ccxt_ws import CcxtWsFeed
feed = CcxtWsFeed("binance", "BTC/USDT", "5m")
await feed.start()
```
