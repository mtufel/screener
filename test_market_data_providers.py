"""
Comprehensive Unit & Integration Test Suite for Market Data Providers:
- BinanceProvider (Futures & Spot)
- OandaProvider (Forex, Commodities & Crypto)
- HyperliquidProvider (Adapter)
- Provider Factory & Dynamic Runtime Switching
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from market_data_provider import (
    BaseMarketDataProvider,
    BinanceProvider,
    OandaProvider,
    HyperliquidProvider,
    get_market_data_provider,
    close_all_providers,
    _oanda_rfc3339_to_ms,
)
from fastapi.testclient import TestClient
from main import app, state


@pytest.fixture(autouse=True)
def cleanup_providers():
    yield
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(close_all_providers())
        else:
            loop.run_until_complete(close_all_providers())
    except Exception:
        pass


# ==============================================================================
# 1. BINANCE PROVIDER TESTS
# ==============================================================================
def test_binance_provider_symbol_normalization():
    p_futures = BinanceProvider(use_futures=True)
    p_spot = BinanceProvider(use_futures=False)

    assert p_futures.name == "binance_futures"
    assert p_spot.name == "binance_spot"

    # Direct crypto symbol resolution
    assert p_futures.resolve_symbol("BTC") == "BTCUSDT"
    assert p_futures.resolve_symbol("eth") == "ETHUSDT"
    assert p_futures.resolve_symbol("solusdt") == "SOLUSDT"

    # Commodity and alias mapping
    assert p_futures.resolve_symbol("GOLD") == "PAXGUSDT"
    assert p_futures.resolve_symbol("SILVER") == "XAGUSDT"
    assert p_futures.resolve_symbol("kPEPE") == "1000PEPEUSDT"
    assert p_futures.resolve_symbol("SHIB") == "1000SHIBUSDT"


@pytest.mark.asyncio
async def test_binance_provider_get_all_mids_mocked():
    p = BinanceProvider(use_futures=True)

    fake_ticker_response = [
        {"symbol": "BTCUSDT", "price": "67500.50"},
        {"symbol": "ETHUSDT", "price": "3450.20"},
        {"symbol": "SOLUSDT", "price": "145.80"},
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_ticker_response
    mock_resp.raise_for_status = MagicMock()

    with patch.object(p._http, "get", new=AsyncMock(return_value=mock_resp)):
        mids = await p.get_all_mids()

    assert "BTC" in mids
    assert "BTCUSDT" in mids
    assert mids["BTC"] == 67500.50
    assert mids["ETH"] == 3450.20
    assert mids["SOL"] == 145.80


@pytest.mark.asyncio
async def test_binance_provider_get_last_n_candles_mocked():
    p = BinanceProvider(use_futures=True)

    # Binance kline format: [open_time, open, high, low, close, volume, close_time, ...]
    fake_klines = [
        [1700000000000, "100.0", "105.0", "99.0", "104.0", "1500.0", 1700000299999],
        [1700000300000, "104.0", "108.0", "103.0", "107.5", "2000.0", 1700000599999],
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_klines
    mock_resp.raise_for_status = MagicMock()

    with patch.object(p._http, "get", new=AsyncMock(return_value=mock_resp)):
        candles = await p.get_last_n_candles("SOL", timeframe="5m", n=2)

    assert len(candles) == 2
    assert candles[0]["t"] == 1700000000000
    assert candles[0]["o"] == 100.0
    assert candles[0]["h"] == 105.0
    assert candles[0]["l"] == 99.0
    assert candles[0]["c"] == 104.0
    assert candles[0]["v"] == 1500.0


# ==============================================================================
# 2. OANDA PROVIDER TESTS
# ==============================================================================
def test_oanda_provider_symbol_normalization():
    p = OandaProvider()
    assert p.name == "oanda"

    # Commodity mappings
    assert p.resolve_symbol("GOLD") == "XAU_USD"
    assert p.resolve_symbol("XAU") == "XAU_USD"
    assert p.resolve_symbol("SILVER") == "XAG_USD"
    assert p.resolve_symbol("XAG") == "XAG_USD"
    assert p.resolve_symbol("OIL") == "WTICO_USD"
    assert p.resolve_symbol("BRENT") == "BCO_USD"

    # Forex & Crypto mappings
    assert p.resolve_symbol("EURUSD") == "EUR_USD"
    assert p.resolve_symbol("EUR_USD") == "EUR_USD"
    assert p.resolve_symbol("BTC") == "BTC_USD"
    assert p.resolve_symbol("ETH") == "ETH_USD"


def test_oanda_rfc3339_to_epoch_ms():
    # Standard RFC3339 with nanoseconds
    ms = _oanda_rfc3339_to_ms("2026-09-10T12:00:00.000000000Z")
    assert isinstance(ms, int)
    assert ms > 0

    # Basic RFC3339 with Z
    ms2 = _oanda_rfc3339_to_ms("2026-09-10T12:00:00Z")
    assert ms == ms2


@pytest.mark.asyncio
async def test_oanda_provider_get_last_n_candles_mocked():
    p = OandaProvider(api_key="mock_key", account_id="mock_acc")

    fake_oanda_candles = {
        "instrument": "XAU_USD",
        "granularity": "M5",
        "candles": [
            {
                "time": "2026-09-10T12:00:00.000000000Z",
                "mid": {"o": "2500.10", "h": "2505.50", "l": "2498.00", "c": "2504.00"},
                "volume": 350,
                "complete": True,
            },
            {
                "time": "2026-09-10T12:05:00.000000000Z",
                "mid": {"o": "2504.00", "h": "2508.20", "l": "2503.10", "c": "2507.80"},
                "volume": 420,
                "complete": True,
            },
        ],
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_oanda_candles
    mock_resp.raise_for_status = MagicMock()

    with patch.object(p._http, "get", new=AsyncMock(return_value=mock_resp)):
        candles = await p.get_last_n_candles("GOLD", timeframe="5m", n=2)

    assert len(candles) == 2
    assert candles[0]["o"] == 2500.10
    assert candles[0]["h"] == 2505.50
    assert candles[0]["l"] == 2498.00
    assert candles[0]["c"] == 2504.00
    assert candles[0]["v"] == 350.0


# ==============================================================================
# 3. FACTORY & RUNTIME SWITCHING TESTS
# ==============================================================================
def test_provider_factory_resolution():
    p1 = get_market_data_provider("binance")
    assert isinstance(p1, BinanceProvider)
    assert p1.name == "binance_futures"

    p2 = get_market_data_provider("binance_spot")
    assert isinstance(p2, BinanceProvider)
    assert p2.name == "binance_spot"

    p3 = get_market_data_provider("oanda")
    assert isinstance(p3, OandaProvider)
    assert p3.name == "oanda"

    p4 = get_market_data_provider("hyperliquid")
    assert isinstance(p4, HyperliquidProvider)
    assert p4.name == "hyperliquid"


def test_api_extreme_config_provider_switch():
    client = TestClient(app)

    # Switch provider to oanda via API
    resp = client.post("/api/extreme/config?provider=oanda")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["config"]["data_provider"] == "oanda"
    assert state["data_provider"] == "oanda"

    # Switch back to binance
    resp2 = client.post("/api/extreme/config?provider=binance")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["config"]["data_provider"] == "binance"
    assert state["data_provider"] == "binance"
