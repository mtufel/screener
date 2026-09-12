"""Wrap/extend market provider with ccxt for unified multi-exchange data."""
import ccxt
from market_data_provider import get_market_data_provider

class CcxtDataProvider:
    """Wrap existing provider; delegate to ccxt for new/exchanges."""
    def __init__(self, config):
        self.provider = get_market_data_provider(config)
        self.exchange = ccxt.exchange(config.get("exchange", "binance"))
    
    def ohlcv(self, pair, timeframe="5m"):
        # Try ccxt first; fall back to HyperliquidClient
        try:
            return self.exchange.fetch_ohlcv(pair, timeframe, limit=500)
        except Exception:
            return self.provider.get_ohlcv(pair, timeframe)
