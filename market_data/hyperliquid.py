"""
Hyperliquid Market Data Provider Adapter (market_data/hyperliquid.py)
"""

from typing import Any, Dict, List, Optional

from market_data.base import BaseMarketDataProvider
from candle_store import CandleStore, candle_store
from hyperliquid_client import HyperliquidClient, hyperliquid_client, resolve_symbol


class HyperliquidProvider(BaseMarketDataProvider):
    """Adapter wrapping HyperliquidClient into BaseMarketDataProvider interface with CandleStore caching."""

    def __init__(
        self,
        client: Optional[HyperliquidClient] = None,
        store: Optional[CandleStore] = None,
    ):
        self._client = client or hyperliquid_client
        self._store = store or candle_store
        self._ws_client: Optional[Any] = None

    @property
    def name(self) -> str:
        return "hyperliquid"

    @property
    def supports_websocket(self) -> bool:
        return True

    @property
    def is_websocket_connected(self) -> bool:
        return self._ws_client is not None and getattr(self._ws_client, "is_connected", False)

    async def start_websocket(self, symbols: Optional[List[str]] = None, timeframes: Optional[List[str]] = None) -> bool:
        from market_data.hyperliquid_ws import HyperliquidWSClient
        if self._ws_client is None:
            self._ws_client = HyperliquidWSClient(
                store=self._store,
                symbols=symbols,
                timeframes=timeframes,
            )
        elif symbols:
            self._ws_client.update_subscriptions(symbols, timeframes)
        await self._ws_client.start()
        return True

    async def stop_websocket(self):
        if self._ws_client:
            await self._ws_client.stop()
            self._ws_client = None

    def resolve_symbol(self, raw_symbol: str) -> str:
        return resolve_symbol(raw_symbol)

    async def get_all_mids(self) -> Dict[str, float]:
        cached = self._store.get_cached_mids(self.name)
        if cached is not None:
            return cached
        mids = await self._client.get_all_mids()
        if mids:
            self._store.set_cached_mids(self.name, mids)
        return mids

    async def get_last_n_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        n: int = 200,
    ) -> List[Dict[str, Any]]:
        cached = self._store.get_candles(self.name, symbol, timeframe, n=n)
        if cached and len(cached) >= min(n, 50) and self._store.is_fresh(self.name, symbol, timeframe):
            return cached

        has_bootstrapped = self._store.has_sufficient_candles(self.name, symbol, timeframe, min_count=min(n, 50))
        fetch_n = 5 if has_bootstrapped else max(50, n)
        candles = await self._client.get_last_n_candles(symbol=symbol, timeframe=timeframe, n=fetch_n)
        if candles:
            self._store.merge_candles(self.name, symbol, timeframe, candles)
            return self._store.get_candles(self.name, symbol, timeframe, n=n) or candles[-n:]
        return self._store.get_candles(self.name, symbol, timeframe, n=n) or []

    async def get_historical_candles_range(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Dict[str, Any]]:
        return await self._client.get_historical_candles_range(
            coin=coin, interval=interval, start_time_ms=start_time_ms, end_time_ms=end_time_ms
        )

    async def get_universe_coins(self, min_volume: float = 0.0) -> List[str]:
        return await self._client.get_universe_coins(min_volume=min_volume)

    async def close(self):
        await self.stop_websocket()
        await self._client.close()
