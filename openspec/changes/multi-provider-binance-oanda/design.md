# Design Document: Config-Driven Multi-Provider Market Data Architecture

## Architecture Diagram

```
                              +----------------------------+
                              |   Environment / Runtime    |
                              |   DATA_PROVIDER="binance"  |
                              +----------------------------+
                                             |
                                             v
                              +----------------------------+
                              |    Provider Factory /      |
                              | get_market_data_provider() |
                              +----------------------------+
                                             |
                   +-------------------------+-------------------------+
                   |                         |                         |
                   v                         v                         v
        +--------------------+    +--------------------+    +--------------------+
        |  BinanceProvider   |    |   OandaProvider    |    | HyperliquidProvider|
        | (Futures & Spot)   |    |    (v20 REST API)  |    | (Perpetual Info)   |
        | - fapi.binance.com |    | - api-fxtrade.com  |    | - api.hyperliquid  |
        | - BTCUSDT, ETHUSDT |    | - XAU_USD, WTICO   |    | - BTC-PERP, PURR   |
        +--------------------+    +--------------------+    +--------------------+
                   \                         |                         /
                    \                        |                        /
                     v                       v                       v
               +-----------------------------------------------------------+
               |             Unified Standard Candle / Mid Data            |
               |   [{"t": epoch_ms, "o": open, "h": high, "l": low, ...}]  |
               +-----------------------------------------------------------+
                                             |
                                             v
                           +-----------------------------------+
                           | Strategy Engine / Tracker / Main  |
                           +-----------------------------------+
```

## Abstract Interface (`BaseMarketDataProvider`)

```python
class BaseMarketDataProvider(ABC):
    @abstractmethod
    async def get_all_mids(self) -> Dict[str, float]:
        """Returns map of normalized symbol -> live mid price."""
        pass

    @abstractmethod
    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        n: int = 200,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Returns standard OHLCV candle list sorted chronologically."""
        pass

    @abstractmethod
    async def get_universe_coins(self, min_volume: float = 0.0) -> List[str]:
        """Returns list of active tradable coins."""
        pass

    @abstractmethod
    def resolve_symbol(self, raw_symbol: str) -> str:
        """Converts user input symbol (e.g. BTC, XAU, OIL) to provider's native format."""
        pass

    @abstractmethod
    async def close(self):
        """Releases network client connections."""
        pass
```

## Standard OHLCV Schema
All providers normalize candle data into uniform dictionary representations:
- `t`: int (Open timestamp in milliseconds)
- `T`: int (Close timestamp in milliseconds)
- `s`: str (Normalized Symbol e.g. "BTC")
- `i`: str (Timeframe e.g. "5m")
- `o`: float (Open price)
- `h`: float (High price)
- `l`: float (Low price)
- `c`: float (Close price)
- `v`: float (Volume)
