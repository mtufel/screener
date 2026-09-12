## Spec: ccxt-integration-wrap

### Capability
Wrap/extend existing market data provider (HyperliquidClient + Binance/OANDA config) with ccxt for unified multi-exchange live/backtest data fetching.

### Requirements
- `ccxt_adapter.py`: ccxt wrapper with fallback to HyperliquidClient
- Live websocket support for real-time candles
- `ExchangeAdapter` class implementing freqtrade-style interface
- Config schema validation for ccxt settings
- Full live + backtest pipeline (DataProvider-style)

### Non-Functional
- Keep existing position sizing engine untouched
- Keep telegram alerts untouched
- Maintain backward compatibility with current provider
