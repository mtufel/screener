"""
Unified Market Data Provider Facade & Factory (market_data_provider.py)

Exposes provider factory, global singleton management, and re-exports modular
market data components from `candle_store` and `market_data` package for backward compatibility.
"""

import logging
import os
from typing import Dict, Optional

# Storage & Caching Layer
from candle_store import CandleStore, candle_store, TIMEFRAME_MS

# Provider Implementations
from market_data.base import BaseMarketDataProvider
from market_data.binance import BinanceProvider
from market_data.oanda import OandaProvider, _oanda_rfc3339_to_ms
from market_data.hyperliquid import HyperliquidProvider

logger = logging.getLogger("market_data_provider")

# Provider Registry & Factory
_PROVIDERS: Dict[str, BaseMarketDataProvider] = {}


def get_market_data_provider(provider_name: Optional[str] = None) -> BaseMarketDataProvider:
    """
    Returns the singleton instance of the requested market data provider.
    Defaults to DATA_PROVIDER env var, or 'binance'.
    Options: 'binance', 'binance_futures', 'binance_spot', 'oanda', 'hyperliquid'.
    """
    selected = (provider_name or os.getenv("DATA_PROVIDER", "binance")).strip().lower()

    if selected in _PROVIDERS:
        return _PROVIDERS[selected]

    if selected in ("binance", "binance_futures", "binance-futures"):
        provider = BinanceProvider(use_futures=True)
    elif selected in ("binance_spot", "binance-spot"):
        provider = BinanceProvider(use_futures=False)
    elif selected in ("oanda", "onda"):
        provider = OandaProvider()
    elif selected in ("hyperliquid", "hl"):
        provider = HyperliquidProvider()
    else:
        logger.warning("Unknown DATA_PROVIDER '%s'. Defaulting to Binance Futures.", selected)
        provider = BinanceProvider(use_futures=True)

    _PROVIDERS[selected] = provider
    return provider


# Active default singleton
market_data_provider = get_market_data_provider()


async def close_all_providers():
    """Closes all initialized market data provider instances."""
    for name, prov in list(_PROVIDERS.items()):
        try:
            await prov.close()
        except Exception as exc:
            logger.debug("Error closing provider %s: %s", name, exc)
    _PROVIDERS.clear()


__all__ = [
    "CandleStore",
    "candle_store",
    "TIMEFRAME_MS",
    "BaseMarketDataProvider",
    "BinanceProvider",
    "OandaProvider",
    "HyperliquidProvider",
    "_oanda_rfc3339_to_ms",
    "get_market_data_provider",
    "market_data_provider",
    "close_all_providers",
]
