"""
Unit and integration tests for Real-Time WebSocket Bug Fixes (Bugs 1, 3, 4, 5, 6).
Covers:
- Bug 1: Binance WS miniTicker partial frame merges with mids cache without clobbering.
- Bug 3: Strategy 2 screener passes runtime client=provider to get_last_n_candles.
- Bug 4: CcxtProvider reports is_websocket_connected False until first frame arrives.
- Bug 5: Background tasks and send lock concurrency in BinanceWSClient & HyperliquidWSClient.
- Bug 6: DashboardWSManager broadcasts concurrently with client timeouts without blocking.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from candle_store import CandleStore
from market_data.binance_ws import BinanceWSClient
from market_data.hyperliquid_ws import HyperliquidWSClient
from market_data.ccxt_provider import CcxtProvider
from main import DashboardWSManager, execute_extreme_screener_cycle, state


# ==============================================================================
# Bug 1: Binance WS Ticker Merging
# ==============================================================================
@pytest.mark.asyncio
async def test_binance_ws_miniticker_merges_without_clobbering():
    """Verifies that partial miniTicker frames merge with existing mids instead of wiping them."""
    store = CandleStore(mids_ttl_seconds=60.0)
    provider_name = "binance_futures"

    # Pre-populate mids for quiet coins
    store.set_cached_mids(provider_name, {"SOL": 140.0, "ETH": 2500.0})

    client = BinanceWSClient(store=store, use_futures=True)
    client.provider_name = provider_name

    # Feed a partial miniTicker update containing only BTCUSDT
    partial_batch = json.dumps([
        {"e": "24hrMiniTicker", "s": "BTCUSDT", "c": "60000.0", "o": "59000.0", "h": "61000.0", "l": "58500.0"}
    ])
    client.handle_message(partial_batch)

    mids = store.get_cached_mids(provider_name)
    assert mids is not None
    # BTC should be added/updated
    assert mids.get("BTC") == 60000.0
    assert mids.get("BTCUSDT") == 60000.0
    # SOL and ETH must NOT be clobbered
    assert mids.get("SOL") == 140.0
    assert mids.get("ETH") == 2500.0


# ==============================================================================
# Bug 3: Provider Injection in Strategy 2 Exit Monitor
# ==============================================================================
@pytest.mark.asyncio
async def test_execute_extreme_screener_cycle_injects_provider_into_get_last_n_candles():
    """Verifies that get_last_n_candles receives client=provider for exit monitoring."""
    mock_provider = MagicMock()
    mock_provider.name = "binance_futures"
    mock_provider.resolve_symbol.side_effect = lambda s: f"{s}USDT"
    mock_provider.get_all_mids = AsyncMock(return_value={"BTC": 60000.0, "BTCUSDT": 60000.0})

    candle_calls = []

    async def mock_get_last_n_candles(symbol, timeframe, n, client=None):
        candle_calls.append({"symbol": symbol, "timeframe": timeframe, "n": n, "client": client})
        return []

    orig_whitelist = state.get("coins_whitelist")
    state["coins_whitelist"] = "BTC"

    try:
        with patch("main.get_market_data_provider", return_value=mock_provider), \
             patch("main.get_last_n_candles", side_effect=mock_get_last_n_candles), \
             patch("strategy_extreme_fvg.get_extreme_setup_for_symbol", new_callable=AsyncMock) as mock_get_setup, \
             patch("main.dashboard_ws_manager.broadcast", new_callable=AsyncMock):

            mock_get_setup.return_value = None

            await execute_extreme_screener_cycle()

            # Confirm get_last_n_candles was invoked and received the runtime provider
            assert len(candle_calls) > 0
            for call in candle_calls:
                assert call["client"] is mock_provider, f"Expected {mock_provider} but got {call['client']}"
    finally:
        state["coins_whitelist"] = orig_whitelist


# ==============================================================================
# Bug 4: CcxtProvider WebSocket Connection State
# ==============================================================================
@pytest.mark.asyncio
async def test_ccxt_provider_ws_connected_state_lifecycle():
    """Verifies is_websocket_connected is False upon start and only True when frames arrive."""
    store = CandleStore()
    provider = CcxtProvider(exchange_id="binance", store=store)

    gate = asyncio.Event()
    called = False

    async def delayed_watch_tickers(symbols):
        nonlocal called
        if not called:
            called = True
            await gate.wait()
            return {"BTC/USDT": {"last": 67000.0, "symbol": "BTC/USDT"}}
        raise asyncio.CancelledError()

    with patch.object(provider._pro_exchange, "watch_tickers", side_effect=delayed_watch_tickers), \
         patch.object(provider._pro_exchange, "watch_ohlcv", side_effect=asyncio.CancelledError()):

        assert provider.is_websocket_connected is False

        started = await provider.start_websocket(symbols=["BTC"], timeframes=["5m"])
        assert started is True

        # Crucial check: must NOT report connected before first frame arrives
        assert provider.is_websocket_connected is False

        # Release the gate to deliver the first frame
        gate.set()
        await asyncio.sleep(0.05)

        # Now it must report connected
        assert provider.is_websocket_connected is True

        await provider.stop_websocket()
        assert provider.is_websocket_connected is False


# ==============================================================================
# Bug 5: Background Task Tracking & Send Locks
# ==============================================================================
@pytest.mark.asyncio
async def test_ws_clients_track_background_tasks_and_have_send_lock():
    """Verifies task tracking and send locks in BinanceWSClient and HyperliquidWSClient."""
    # 1. BinanceWSClient
    b_client = BinanceWSClient()
    assert hasattr(b_client, "_send_lock")
    assert hasattr(b_client, "_background_tasks")
    assert isinstance(b_client._background_tasks, set)

    # Mock connected state
    mock_ws = AsyncMock()
    mock_ws.state = 1
    b_client._connected = True
    b_client._ws = mock_ws
    assert b_client.is_connected is True

    b_client.update_subscriptions(["BTC", "ETH"], ["5m"])
    assert len(b_client._background_tasks) >= 1

    # Stop should cancel and clear tasks
    await b_client.stop()
    assert len(b_client._background_tasks) == 0
    assert b_client.is_connected is False

    # 2. HyperliquidWSClient
    h_client = HyperliquidWSClient()
    assert hasattr(h_client, "_send_lock")
    assert hasattr(h_client, "_background_tasks")
    assert isinstance(h_client._background_tasks, set)

    h_client._connected = True
    h_client._ws = mock_ws
    assert h_client.is_connected is True

    h_client.update_subscriptions(["BTC", "ETH"], ["5m"])
    assert len(h_client._background_tasks) >= 1

    await h_client.stop()
    assert len(h_client._background_tasks) == 0
    assert h_client.is_connected is False


# ==============================================================================
# Bug 6: Non-Blocking Concurrent Dashboard Broadcast
# ==============================================================================
@pytest.mark.asyncio
async def test_dashboard_ws_manager_concurrent_broadcast_with_timeout():
    """Verifies broadcast does not stall sequentially on slow clients and prunes timed-out sockets."""
    manager = DashboardWSManager()

    # Fast client
    fast_ws = MagicMock()
    fast_ws.send_json = AsyncMock(return_value=None)

    # Slow/stuck client that takes 5 seconds
    slow_ws = MagicMock()
    async def slow_send(msg):
        await asyncio.sleep(5.0)

    slow_ws.send_json = slow_send

    # Failing client
    failing_ws = MagicMock()
    failing_ws.send_json = AsyncMock(side_effect=RuntimeError("Connection lost"))

    manager.active_connections.add(fast_ws)
    manager.active_connections.add(slow_ws)
    manager.active_connections.add(failing_ws)

    t0 = asyncio.get_event_loop().time()
    await manager.broadcast({"type": "PING"})
    elapsed = asyncio.get_event_loop().time() - t0

    # Must finish around 2.0s (timeout threshold), definitely not 5.0s
    assert elapsed < 3.0, f"Broadcast blocked for too long: {elapsed:.2f}s"

    # Fast client must receive message and stay connected
    fast_ws.send_json.assert_called_once_with({"type": "PING"})
    assert fast_ws in manager.active_connections

    # Slow and failing clients must be pruned
    assert slow_ws not in manager.active_connections
    assert failing_ws not in manager.active_connections
