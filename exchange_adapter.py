"""ExchangeAdapter mimicking freqtrade Exchange interface."""
from ccxt_adapter import CcxtDataProvider

class ExchangeAdapter:
    def __init__(self, config):
        self.dp = CcxtDataProvider(config)
    def ohlcv_candle_limit(self, pair_tf, limit=500):
        return self.dp.ohlcv(pair_tf[0], pair_tf[1])
    def klines(self, pair_tf, copy=True):
        return self.dp.ohlcv(pair_tf[0], pair_tf[1])
