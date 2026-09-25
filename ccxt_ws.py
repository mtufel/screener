"""Live websocket data using ccxt watchOHLCV — config-driven, plug anywhere."""
import os
import ccxt
from typing import Callable, Any, Optional

class CcxtWsFeed:
    def __init__(self, exchange_id: str, pair: str, timeframe: str = "5m",
                 on_candle: Optional[Callable[[list], Any]] = None):
        self.exchange = ccxt.binance() if exchange_id == "binance" else ccxt.exchange(exchange_id)
        self.pair = pair
        self.timeframe = timeframe
        self.on_candle = on_candle or (lambda c: None)

    @classmethod
    def from_config(cls, config: Optional[dict] = None) -> "CcxtWsFeed":
        """Create from config dict or env vars. Reads: ccxt_exchange, ccxt_pair, timeframe."""
        config = config or {}
        exchange = config.get("ccxt_exchange") or os.getenv("CCXT_EXCHANGE", "binance")
        pair = config.get("ccxt_pair") or os.getenv("CCXT_PAIR", "BTC/USDT")
        timeframe = config.get("timeframe") or os.getenv("TIMEFRAME", "5m")
        return cls(exchange, pair, timeframe)

    async def start(self, on_candle: Optional[Callable[[list], Any]] = None):
        """Main loop: yields each candle to on_candle callback."""
        cb = on_candle or self.on_candle
        while True:
            candle = await self.exchange.watchOHLCV(self.pair, self.timeframe)
            cb(candle)
