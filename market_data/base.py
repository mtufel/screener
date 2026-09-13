"""
Abstract Base Market Data Provider (market_data/base.py)
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BaseMarketDataProvider(ABC):
    """Abstract Base Class for all market data providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Returns provider identifier name."""
        pass

    @abstractmethod
    async def get_all_mids(self) -> Dict[str, float]:
        """Returns a map of normalized symbols to their latest mid prices."""
        pass

    @abstractmethod
    async def get_last_n_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        n: int = 200,
    ) -> List[Dict[str, Any]]:
        """Fetches the latest N closed/in-progress OHLCV candles."""
        pass

    @abstractmethod
    async def get_historical_candles_range(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Dict[str, Any]]:
        """Fetches historical candles over an explicit epoch milliseconds range."""
        pass

    @abstractmethod
    async def get_universe_coins(self, min_volume: float = 0.0) -> List[str]:
        """Returns the active tradable coin universe sorted by volume."""
        pass

    @abstractmethod
    def resolve_symbol(self, raw_symbol: str) -> str:
        """Normalizes symbol to provider format."""
        pass

    @abstractmethod
    async def close(self):
        """Releases underlying HTTP client connections."""
        pass
