"""Live websocket data using ccxt watchOHLCV."""
import ccxt

class CcxtWsFeed:
    def __init__(self, exchange_id, pair, timeframe="5m"):
        self.exchange = ccxt.exchange(exchange_id)
        self.pair = pair
        self.timeframe = timeframe
    async def start(self):
        # ccxt async watchOHLCV
        while True:
            candle = await self.exchange.watchOHLCV(self.pair, self.timeframe)
            # feed to strategy / screener
