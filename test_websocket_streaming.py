"""
Unit & Integration Tests for WebSocket Streaming Market Data & Live Dashboard.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from candle_store import CandleStore
from market_data.hyperliquid_ws import HyperliquidWSClient
from market_data.binance_ws import BinanceWSClient
from market_data.hyperliquid import HyperliquidProvider
from market_data.binance import BinanceProvider
from main import app, dashboard_ws_manager


@pytest.fixture
def test_store():
    return CandleStore()


def test_hyperliquid_ws_allmids_message_parsing(test_store):
    received_mids = None

    def on_update(mids):
        nonlocal received_mids
        received_mids = mids

    client = HyperliquidWSClient(store=test_store, on_price_update=on_update)
    raw_payload = {
        "channel": "allMids",
        "data": {
            "mids": {
                "BTC": "62500.50",
                "ETH": "3100.25",
                "SOL": "155.00",
                "PAXG": "2500.00",
            }
        }
    }
    client.handle_message(json.dumps(raw_payload))

    # Verify cached in CandleStore
    cached = test_store.get_cached_mids("hyperliquid")
    assert cached is not None
    assert cached["BTC"] == 62500.50
    assert cached["ETH"] == 3100.25
    assert cached["SOL"] == 155.00
    assert cached["PAXG"] == 2500.00
    assert cached["GOLD"] == 2500.00  # Commodity alias mapped

    # Verify callback invoked
    assert received_mids is not None
    assert received_mids["BTC"] == 62500.50


def test_hyperliquid_ws_candle_message_parsing(test_store):
    client = HyperliquidWSClient(store=test_store)
    raw_payload = {
        "channel": "candle",
        "data": {
            "t": 1788000000000,
            "T": 1788000300000,
            "s": "BTC",
            "i": "5m",
            "o": "60000.0",
            "c": "60150.0",
            "h": "60200.0",
            "l": "59950.0",
            "v": "15.42",
            "n": 120,
        }
    }
    client.handle_message(json.dumps(raw_payload))

    candles = test_store.get_candles("hyperliquid", "BTC", "5m")
    assert candles is not None
    assert len(candles) == 1
    c = candles[0]
    assert c["t"] == 1788000000000
    assert c["o"] == 60000.0
    assert c["c"] == 60150.0
    assert c["h"] == 60200.0
    assert c["l"] == 59950.0
    assert c["v"] == 15.42


def test_binance_ws_miniticker_message_parsing(test_store):
    client = BinanceWSClient(use_futures=True, store=test_store)
    raw_payload = [
        {"e": "24hrMiniTicker", "s": "BTCUSDT", "c": "63100.20"},
        {"e": "24hrMiniTicker", "s": "ETHUSDT", "c": "3150.10"},
    ]
    client.handle_message(json.dumps(raw_payload))

    cached = test_store.get_cached_mids("binance_futures")
    assert cached is not None
    assert cached["BTCUSDT"] == 63100.20
    assert cached["BTC"] == 63100.20
    assert cached["ETH"] == 3150.10


def test_binance_ws_kline_message_parsing(test_store):
    client = BinanceWSClient(use_futures=True, store=test_store)
    raw_payload = {
        "e": "kline",
        "E": 1788000300000,
        "s": "BTCUSDT",
        "k": {
            "t": 1788000000000,
            "T": 1788000300000,
            "s": "BTCUSDT",
            "i": "5m",
            "o": "62000.0",
            "c": "62200.0",
            "h": "62300.0",
            "l": "61900.0",
            "v": "25.0",
            "x": False,
        }
    }
    client.handle_message(json.dumps(raw_payload))

    candles = test_store.get_candles("binance_futures", "BTC", "5m")
    assert candles is not None
    assert len(candles) == 1
    assert candles[0]["c"] == 62200.0


@pytest.mark.asyncio
async def test_hyperliquid_provider_ws_lifecycle_and_fallback(test_store):
    mock_http_client = AsyncMock()
    mock_http_client.get_all_mids.return_value = {"BTC": 60000.0}

    provider = HyperliquidProvider(client=mock_http_client, store=test_store)
    assert provider.supports_websocket is True
    assert provider.is_websocket_connected is False

    # 1. When WS not connected -> falls back to REST
    mids_rest = await provider.get_all_mids()
    assert mids_rest == {"BTC": 60000.0}
    assert mock_http_client.get_all_mids.called

    # 2. When WS feeds mids into store -> served directly from memory, zero HTTP
    test_store.set_cached_mids("hyperliquid", {"BTC": 60500.0, "ETH": 3000.0})
    mock_http_client.get_all_mids.reset_mock()

    mids_cached = await provider.get_all_mids()
    assert mids_cached["BTC"] == 60500.0
    assert not mock_http_client.get_all_mids.called

    # 3. Clean close
    await provider.close()


@pytest.mark.asyncio
async def test_binance_provider_ws_lifecycle(test_store):
    provider = BinanceProvider(use_futures=True, store=test_store)
    assert provider.supports_websocket is True
    assert provider.is_websocket_connected is False

    with patch.object(BinanceWSClient, "start", new_callable=AsyncMock) as mock_start:
        started = await provider.start_websocket(["BTC", "ETH"], ["5m"])
        assert started is True
        assert mock_start.called

    await provider.close()


def test_dashboard_websocket_endpoint():
    client = TestClient(app)
    with client.websocket_connect("/ws/extreme-live") as websocket:
        initial_msg = websocket.receive_json()
        assert initial_msg["type"] == "initial_state"
        assert "is_running" in initial_msg
        assert "history_data" in initial_msg
        assert initial_msg["history_data"]["status"] == "success"

        # Test ping/pong
        websocket.send_text("ping")
        resp = websocket.receive_text()
        assert resp == "pong"
