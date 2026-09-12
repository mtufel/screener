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

# ccxt adapter integration (wrap/extend for unified multi-exchange data)
try:
    from ccxt_adapter import CcxtDataProvider
except ImportError:
    CcxtDataProvider = None  # optional dependency

logger = logging.getLogger("market_data_provider")

# Provider Registry & Factory
_PROVIDERS: Dict[str, BaseMarketDataProvider] = {}


def get_market_data_provider(
    provider_name: Optional[str] = None,
    fallback_name: Optional[str] = None,
) -> BaseMarketDataProvider:
    """
    Returns the singleton instance of the requested market data provider with fallback delegation.
    Defaults:
      - Primary: DATA_PROVIDER env var, or 'binance'.
      - Fallback: FALLBACK_DATA_PROVIDER env var, or 'hyperliquid'.
    Options: 'binance', 'binance_futures', 'binance_spot', 'oanda', 'hyperliquid', 'none'.
    """
    selected = (provider_name or os.getenv("DATA_PROVIDER", "binance")).strip().lower()
    fb_selected = (
        fallback_name
        if fallback_name is not None
        else os.getenv("FALLBACK_DATA_PROVIDER", "hyperliquid")
    ).strip().lower()

    cache_key = f"{selected}:{fb_selected}"
    if cache_key in _PROVIDERS:
        return _PROVIDERS[cache_key]

    # If ccxt_exchange configured, prefer CcxtDataProvider (wrap + fallback)
    if CcxtDataProvider and os.getenv("CCXT_EXCHANGE"):
        inst = CcxtDataProvider({"exchange": os.getenv("CCXT_EXCHANGE"),
                                 "provider": selected})
        _PROVIDERS[cache_key] = inst
        return inst

    # Resolve fallback provider if configured and not identical to primary
    fallback_inst: Optional[BaseMarketDataProvider] = None
    if fb_selected not in ("none", "", "disabled", "false", "0", selected):
        if fb_selected in ("hyperliquid", "hl"):
            fallback_inst = HyperliquidProvider()
        elif fb_selected in ("binance", "binance_futures", "binance-futures"):
            fallback_inst = BinanceProvider(use_futures=True)
        elif fb_selected in ("binance_spot", "binance-spot"):
            fallback_inst = BinanceProvider(use_futures=False)
        elif fb_selected in ("oanda", "onda"):
            fallback_inst = OandaProvider()

    if selected in ("binance", "binance_futures", "binance-futures"):
        provider = BinanceProvider(use_futures=True, fallback_provider=fallback_inst)
    elif selected in ("binance_spot", "binance-spot"):
        provider = BinanceProvider(use_futures=False, fallback_provider=fallback_inst)
    elif selected in ("oanda", "onda"):
        provider = OandaProvider(fallback_provider=fallback_inst)
    elif selected in ("hyperliquid", "hl"):
        provider = HyperliquidProvider()
    else:
        logger.warning("Unknown DATA_PROVIDER '%s'. Defaulting to Binance Futures with Hyperliquid fallback.", selected)
        provider = BinanceProvider(use_futures=True, fallback_provider=fallback_inst)

    _PROVIDERS[cache_key] = provider
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
