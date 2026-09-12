"""
Unit & Integration Tests for CCXT & CCXT Pro Unified Market Data Provider (test_ccxt_provider.py)
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from market_data_provider import (
    BaseMarketDataProvider,
    CcxtProvider,
    get_market_data_provider,
    close_all_providers,
)
from candle_store import candle_store, CandleStore


@pytest.fixture(autouse=True)
def cleanup_store():
    candle_store.clear()
    yield
    candle_store.clear()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(close_all_providers())
        else:
            loop.run_until_complete(close_all_providers())
    except Exception:
        pass


# ==============================================================================
# 1. INITIALIZATION & SYMBOL RESOLUTION
# ==============================================================================
def test_ccxt_provider_initialization():
    p_binance = CcxtProvider(exchange_id="binance")
    assert p_binance.name == "ccxt_binance"
    assert p_binance.exchange_id == "binance"
    assert p_binance.supports_websocket is True

    p_bybit = CcxtProvider(exchange_id="bybit")
    assert p_bybit.name == "ccxt_bybit"
    assert p_bybit.supports_websocket is True

    with pytest.raises(ValueError, match="not supported"):
        CcxtProvider(exchange_id="non_existent_fake_exchange_123")


def test_ccxt_symbol_resolution_and_normalization():
    p = CcxtProvider(exchange_id="binance")

    # Raw crypto symbols to CCXT format
    assert p.resolve_symbol("BTC") == "BTC/USDT"
    assert p.resolve_symbol("eth") == "ETH/USDT"
    assert p.resolve_symbol("SOLUSDT") == "SOL/USDT"
    assert p.resolve_symbol("BTC-PERP") == "BTC/USDT"
    assert p.resolve_symbol("BTC/USDC") == "BTC/USDC"

    # Commodity & special token aliases
    assert p.resolve_symbol("GOLD") == "PAXG/USDT"
    assert p.resolve_symbol("XAU") == "PAXG/USDT"
    assert p.resolve_symbol("SILVER") == "XAG/USDT"
    assert p.resolve_symbol("1000PEPE") == "1000PEPE/USDT"
    assert p.resolve_symbol("KPEPE") == "1000PEPE/USDT"

    # Normalization back to base
    assert p.normalize_symbol_to_base("BTC/USDT") == "BTC"
    assert p.normalize_symbol_to_base("BTC/USDT:USDT") == "BTC"
    assert p.normalize_symbol_to_base("ETH/USDC") == "ETH"
    assert p.normalize_symbol_to_base("PAXG/USDT") == "PAXG"
    assert p.normalize_symbol_to_base("BTCUSDT") == "BTC"


# ==============================================================================
# 2. REST GET_ALL_MIDS & RATE LIMIT / FALLBACK
# ==============================================================================
@pytest.mark.asyncio
async def test_ccxt_get_all_mids_mock():
    store = CandleStore()
    provider = CcxtProvider(exchange_id="binance", store=store)

    mock_tickers = {
        "BTC/USDT": {"last": 65000.5, "symbol": "BTC/USDT"},
        "ETH/USDT": {"last": 3500.25, "symbol": "ETH/USDT"},
        "PAXG/USDT": {"last": 2400.0, "symbol": "PAXG/USDT"},
        "SOL/USDT": {"close": 150.0, "symbol": "SOL/USDT"},
    }

    with patch.object(provider._rest_exchange, "fetch_tickers", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_tickers

        mids = await provider.get_all_mids()
        assert mids["BTC"] == 65000.5
        assert mids["ETH"] == 3500.25
        assert mids["PAXG"] == 2400.0
        assert mids["GOLD"] == 2400.0
        assert mids["SOL"] == 150.0

        # Cache check - next call should NOT hit fetch_tickers
        mock_fetch.reset_mock()
        cached_mids = await provider.get_all_mids()
        assert cached_mids["BTC"] == 65000.5
        mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_ccxt_get_all_mids_rate_limit_and_fallback():
    store = CandleStore()
    fallback = AsyncMock()
    fallback.name = "mock_fallback"
    fallback.get_all_mids.return_value = {"BTC": 66000.0}

    provider = CcxtProvider(exchange_id="binance", store=store, fallback_provider=fallback)

    with patch.object(provider._rest_exchange, "fetch_tickers", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = Exception("HTTP 429 RateLimitExceeded")

        mids = await provider.get_all_mids()
        assert mids == {"BTC": 66000.0}
        assert store.is_rate_limited(provider.name) is True
        fallback.get_all_mids.assert_awaited_once()


# ==============================================================================
# 3. OHLCV FETCHING & IN-MEMORY CACHE
# ==============================================================================
@pytest.mark.asyncio
async def test_ccxt_get_last_n_candles_mock():
    store = CandleStore()
    provider = CcxtProvider(exchange_id="binance", store=store)

    # Sample CCXT OHLCV rows: [timestamp, open, high, low, close, volume]
    sample_ohlcv = [
        [1700000000000, 60000.0, 60100.0, 59900.0, 60050.0, 10.5],
        [1700000300000, 60050.0, 60200.0, 60000.0, 60150.0, 12.0],
        [1700000600000, 60150.0, 60300.0, 60100.0, 60250.0, 15.2],
    ]

    with patch.object(provider._rest_exchange, "fetch_ohlcv", new_callable=AsyncMock) as mock_ohlcv:
        mock_ohlcv.return_value = sample_ohlcv

        candles = await provider.get_last_n_candles("BTC", "5m", n=3)
        assert len(candles) == 3
        assert candles[0]["t"] == 1700000000000
        assert candles[0]["o"] == 60000.0
        assert candles[0]["c"] == 60050.0
        assert candles[0]["s"] == "BTC"
        assert candles[0]["i"] == "5m"

        # Check in CandleStore
        stored = store.get_candles(provider.name, "BTC", "5m")
        assert len(stored) == 3

        # Immediate cache hit check
        mock_ohlcv.reset_mock()
        fresh_candles = await provider.get_last_n_candles("BTC", "5m", n=3)
        assert len(fresh_candles) == 3
        mock_ohlcv.assert_not_called()


@pytest.mark.asyncio
async def test_ccxt_historical_candles_range():
    store = CandleStore()
    provider = CcxtProvider(exchange_id="binance", store=store)

    sample_ohlcv = [
        [1700000000000, 100.0, 105.0, 99.0, 102.0, 50.0],
        [1700000300000, 102.0, 104.0, 101.0, 103.0, 40.0],
    ]

    with patch.object(provider._rest_exchange, "fetch_ohlcv", new_callable=AsyncMock) as mock_ohlcv:
        mock_ohlcv.return_value = sample_ohlcv

        history = await provider.get_historical_candles_range(
            coin="SOL",
            interval="5m",
            start_time_ms=1700000000000,
            end_time_ms=1700000600000,
        )
        assert len(history) == 2
        assert history[0]["s"] == "SOL"
        assert history[1]["c"] == 103.0


# ==============================================================================
# 4. WEBSOCKET STREAMING LIFECYCLE & IN-MEMORY CACHE
# ==============================================================================
@pytest.mark.asyncio
async def test_ccxt_websocket_streaming_lifecycle():
    store = CandleStore()
    provider = CcxtProvider(exchange_id="binance", store=store)

    assert provider.supports_websocket is True
    assert provider.is_websocket_connected is False

    # Mock watch_tickers and watch_ohlcv on pro exchange
    with patch.object(provider._pro_exchange, "watch_tickers", new_callable=AsyncMock) as mock_watch_tickers, \
         patch.object(provider._pro_exchange, "watch_ohlcv", new_callable=AsyncMock) as mock_watch_ohlcv:

        mock_watch_tickers.side_effect = [
            {"BTC/USDT": {"last": 67000.0, "symbol": "BTC/USDT"}},
            asyncio.CancelledError(),
        ]
        mock_watch_ohlcv.side_effect = [
            [[1700001000000, 67000.0, 67100.0, 66950.0, 67050.0, 8.0]],
            asyncio.CancelledError(),
        ]

        started = await provider.start_websocket(symbols=["BTC"], timeframes=["5m"])
        assert started is True
        assert provider.is_websocket_connected is True

        # Let streaming task process one iteration
        await asyncio.sleep(0.05)

        # Mids should be cached in CandleStore
        mids = store.get_cached_mids(provider.name)
        assert mids is not None
        assert mids.get("BTC") == 67000.0

        # Candles should be merged into CandleStore
        candles = store.get_candles(provider.name, "BTC", "5m")
        assert len(candles) >= 1
        assert candles[-1]["c"] == 67050.0

        # Stop WebSocket
        await provider.stop_websocket()
        assert provider.is_websocket_connected is False
        assert provider._ws_running is False


# ==============================================================================
# 5. FACTORY REGISTRATION & SWITCHING
# ==============================================================================
def test_provider_factory_ccxt_resolution():
    # Direct ccxt provider
    p1 = get_market_data_provider("ccxt")
    assert isinstance(p1, CcxtProvider)
    assert p1.exchange_id == "binance"
    assert p1.name == "ccxt_binance"

    # ccxt with specific exchange suffix
    p2 = get_market_data_provider("ccxt_bybit")
    assert isinstance(p2, CcxtProvider)
    assert p2.exchange_id == "bybit"
    assert p2.name == "ccxt_bybit"

    # ccxt with fallback
    p3 = get_market_data_provider("ccxt_okx", fallback_name="hyperliquid")
    assert isinstance(p3, CcxtProvider)
    assert p3.exchange_id == "okx"
    assert p3.fallback_provider is not None
    assert p3.fallback_provider.name == "hyperliquid"
