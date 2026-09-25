"""
Unit and integration tests for 4H FVG fallback provider delegation,
rate-limit resilience, and canonical symbol cache keying.
"""

import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from candle_store import CandleStore
from market_data.binance import BinanceProvider
from market_data.ccxt_provider import CcxtProvider
from market_data.hyperliquid import HyperliquidProvider
from strategy_extreme_fvg import (
    Candle,
    htf_fvg_cache,
    get_active_4h_fvgs_for_symbol,
)
from qa_harness.core import FakeProvider, SinkRecorder, install_patches, configure_state


# ==============================================================================
# 1. BINANCE PROVIDER RATE-LIMIT FALLBACK DELEGATION TESTS
# ==============================================================================
@pytest.mark.asyncio
async def test_binance_provider_delegates_to_fallback_when_cache_insufficient():
    """
    When Binance is in rate-limit cooldown and has only 1 cached bar (e.g. from WS),
    it MUST NOT simply return that 1 bar; it must delegate to fallback_provider.
    """
    store = CandleStore()
    fallback = MagicMock(spec=HyperliquidProvider)
    fallback.name = "hyperliquid"
    fallback.get_last_n_candles = AsyncMock(return_value=[
        {"t": 1000 * i, "o": 100.0, "h": 105.0, "l": 95.0, "c": 102.0, "v": 10.0}
        for i in range(200)
    ])

    binance = BinanceProvider(use_futures=True, store=store, fallback_provider=fallback)
    
    # 1 single candle in store (from WS tick)
    store.merge_candles("binance_futures", "BTC", "4h", [
        {"t": 1000, "o": 100.0, "h": 105.0, "l": 95.0, "c": 102.0, "v": 10.0}
    ])
    # Mark Binance as rate-limited
    store.set_rate_limited("binance_futures", cooldown_seconds=60.0)

    # Call get_last_n_candles
    result = await binance.get_last_n_candles(symbol="BTC", timeframe="4h", n=200)

    # Verify fallback was invoked
    fallback.get_last_n_candles.assert_awaited_once_with(symbol="BTC", timeframe="4h", n=200)
    assert len(result) == 200


@pytest.mark.asyncio
async def test_binance_provider_serves_cache_when_sufficient_even_under_rate_limit():
    """
    When Binance is in rate-limit cooldown but has sufficient candles (>= 50),
    it serves the cache without invoking fallback.
    """
    store = CandleStore()
    fallback = MagicMock(spec=HyperliquidProvider)
    fallback.name = "hyperliquid"
    fallback.get_last_n_candles = AsyncMock()

    binance = BinanceProvider(use_futures=True, store=store, fallback_provider=fallback)

    # 60 candles in store
    store.merge_candles("binance_futures", "BTC", "4h", [
        {"t": 1000 * i, "o": 100.0, "h": 105.0, "l": 95.0, "c": 102.0, "v": 10.0}
        for i in range(60)
    ])
    store.set_rate_limited("binance_futures", cooldown_seconds=60.0)

    result = await binance.get_last_n_candles(symbol="BTC", timeframe="4h", n=50)

    # Fallback should NOT be called because cache is sufficient
    fallback.get_last_n_candles.assert_not_awaited()
    assert len(result) == 50


@pytest.mark.asyncio
async def test_binance_provider_http_418_delegates_to_fallback_on_insufficient_cache():
    """
    When REST klines call returns HTTP 418 and cache has only 1 candle,
    it must delegate to fallback_provider instead of returning that 1 candle.
    """
    store = CandleStore()
    fallback = MagicMock(spec=HyperliquidProvider)
    fallback.name = "hyperliquid"
    fallback.get_last_n_candles = AsyncMock(return_value=[
        {"t": 1000 * i, "o": 100.0, "h": 105.0, "l": 95.0, "c": 102.0, "v": 10.0}
        for i in range(200)
    ])

    binance = BinanceProvider(use_futures=True, store=store, fallback_provider=fallback)

    # Put 1 candle in cache
    store.merge_candles("binance_futures", "BTC", "4h", [
        {"t": 1000, "o": 100.0, "h": 105.0, "l": 95.0, "c": 102.0, "v": 10.0}
    ])

    # Mock HTTP client to return 418
    mock_resp = MagicMock()
    mock_resp.status_code = 418
    mock_resp.text = '{"code":-1003,"msg":"Way too many requests; IP banned"}'
    mock_http = MagicMock()
    mock_http.is_closed = False
    mock_http.get = AsyncMock(return_value=mock_resp)
    binance._http_client = mock_http

    result = await binance.get_last_n_candles(symbol="BTC", timeframe="4h", n=200)

    assert store.is_rate_limited("binance_futures") is True
    fallback.get_last_n_candles.assert_awaited_once_with(symbol="BTC", timeframe="4h", n=200)
    assert len(result) == 200


# ==============================================================================
# 2. CCXT PROVIDER RATE-LIMIT FALLBACK DELEGATION TESTS
# ==============================================================================
@pytest.mark.asyncio
async def test_ccxt_provider_delegates_to_fallback_when_cache_insufficient():
    """
    CcxtProvider rate-limit guard must delegate to fallback when cache has < 50 bars.
    """
    store = CandleStore()
    fallback = MagicMock(spec=HyperliquidProvider)
    fallback.name = "hyperliquid"
    fallback.get_last_n_candles = AsyncMock(return_value=[
        {"t": 1000 * i, "o": 50.0, "h": 55.0, "l": 48.0, "c": 52.0, "v": 5.0}
        for i in range(100)
    ])

    ccxt_p = CcxtProvider(exchange_id="binance", store=store, fallback_provider=fallback)
    # Put 1 candle in cache
    store.merge_candles(ccxt_p.name, "SOL", "4h", [
        {"t": 1000, "o": 50.0, "h": 55.0, "l": 48.0, "c": 52.0, "v": 5.0}
    ])
    store.set_rate_limited(ccxt_p.name, cooldown_seconds=60.0)

    result = await ccxt_p.get_last_n_candles(symbol="SOL", timeframe="4h", n=100)

    fallback.get_last_n_candles.assert_awaited_once_with(symbol="SOL", timeframe="4h", n=100)
    assert len(result) == 100


@pytest.mark.asyncio
async def test_ccxt_empty_frame_success_path_returns_list_not_none():
    """
    Bug 4 (no-frame) regression guard: when fetch_ohlcv succeeds but returns an
    empty list (and the cache is cold), get_last_n_candles MUST return [] —
    never None — so downstream Candle.from_dict loops don't crash on NoneType.
    """
    store = CandleStore()
    ccxt_p = CcxtProvider(exchange_id="binance", store=store)

    with patch.object(ccxt_p._rest_exchange, "fetch_ohlcv", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = []
        result = await ccxt_p.get_last_n_candles("BTC", "5m", n=50)

    assert result == []


# ==============================================================================
# 4. MIDS COOLDOWN FALLBACK DELEGATION TESTS
# ==============================================================================
@pytest.mark.asyncio
async def test_binance_mids_delegates_to_fallback_on_every_call_during_cooldown():
    """
    During a 60s rate-limit cooldown, get_all_mids MUST consult the fallback on
    every (cache-missing) call instead of returning empty mids for ~57s per minute.
    """
    store = CandleStore()
    fallback = MagicMock(spec=HyperliquidProvider)
    fallback.name = "hyperliquid"
    fallback.get_all_mids = AsyncMock(return_value={"BTC": 66000.0, "ETH": 3500.0})

    binance = BinanceProvider(use_futures=True, store=store, fallback_provider=fallback)
    store.set_rate_limited(binance.name, cooldown_seconds=60.0)

    mids1 = await binance.get_all_mids()
    assert mids1 == {"BTC": 66000.0, "ETH": 3500.0}
    assert fallback.get_all_mids.await_count == 1

    # Force-expire the mids cache while cooldown is still active -> must re-delegate
    entry = store._mids_cache[binance.name.lower()]
    store._mids_cache[binance.name.lower()] = (entry[0], time.time() - 1.0)

    mids2 = await binance.get_all_mids()
    assert mids2 == {"BTC": 66000.0, "ETH": 3500.0}
    assert fallback.get_all_mids.await_count == 2


@pytest.mark.asyncio
async def test_binance_mids_fallback_cached_for_cooldown_duration():
    """
    Fallback mids fetched during a cooldown must be cached for the REMAINING
    cooldown duration (not the default 3s TTL), so the screener keeps serving
    real prices for the whole outage window.
    """
    store = CandleStore()
    fallback = MagicMock(spec=HyperliquidProvider)
    fallback.name = "hyperliquid"
    fallback.get_all_mids = AsyncMock(return_value={"BTC": 66000.0})

    binance = BinanceProvider(use_futures=True, store=store, fallback_provider=fallback)
    store.set_rate_limited(binance.name, cooldown_seconds=60.0)

    await binance.get_all_mids()

    entry = store._mids_cache[binance.name.lower()]
    remaining_ttl = entry[1] - time.time()
    assert remaining_ttl > 50.0, f"fallback mids TTL should cover the cooldown, got {remaining_ttl:.1f}s"


@pytest.mark.asyncio
async def test_ccxt_mids_delegates_to_fallback_on_every_call_during_cooldown():
    """
    CcxtProvider must also delegate get_all_mids to the fallback on every
    cache-missing call during a rate-limit cooldown.
    """
    store = CandleStore()
    fallback = MagicMock(spec=HyperliquidProvider)
    fallback.name = "hyperliquid"
    fallback.get_all_mids = AsyncMock(return_value={"SOL": 150.0})

    ccxt_p = CcxtProvider(exchange_id="binance", store=store, fallback_provider=fallback)
    store.set_rate_limited(ccxt_p.name, cooldown_seconds=60.0)

    mids1 = await ccxt_p.get_all_mids()
    assert mids1 == {"SOL": 150.0}
    assert fallback.get_all_mids.await_count == 1

    entry = store._mids_cache[ccxt_p.name.lower()]
    store._mids_cache[ccxt_p.name.lower()] = (entry[0], time.time() - 1.0)

    mids2 = await ccxt_p.get_all_mids()
    assert mids2 == {"SOL": 150.0}
    assert fallback.get_all_mids.await_count == 2


# ==============================================================================
# 3. ENDPOINT /api/extreme/4h-fvgs SYMBOL NORMALIZATION & RESILIENCE TESTS
# ==============================================================================
def test_4h_fvg_endpoint_uses_base_symbol_and_shares_cache():
    """
    Verifies that /api/extreme/4h-fvgs shares htf_fvg_cache with canonical symbols (BTC, ETH),
    and retains active FVGs even if recent provider call returns insufficient candles.
    """
    import main
    import telegram_client
    from dashboard_ws import dashboard_ws_manager as real_ws_manager

    class ClosableSink(SinkRecorder):
        async def close(self):
            pass
        def is_configured(self):
            return False

    provider = FakeProvider(["BTC", "ETH"])
    sink = ClosableSink()

    saved_attrs = {k: getattr(main, k) for k in (
        "get_market_data_provider", "send_extreme_telegram_alert",
        "dashboard_ws_manager", "redis_client",
    )}
    saved_thread_mode = telegram_client.is_telegram_thread_mode
    saved_broadcast = real_ws_manager.broadcast

    install_patches(provider, sink)
    configure_state(main, ["BTC", "ETH"])
    htf_fvg_cache.invalidate_cache()

    try:
        with TestClient(main.app) as c:
            resp = c.get("/api/extreme/4h-fvgs")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "success"

            btc_entry = next(s for s in body["symbols"] if s["symbol"] == "BTC")
            assert btc_entry["fvg_count"] >= 1
            # Cache should be keyed by canonical "BTC:wick"
            assert htf_fvg_cache.is_bootstrapped("BTC", use_close_invalidation=False) is True
            assert htf_fvg_cache.is_bootstrapped("BTCUSDT", use_close_invalidation=False) is False
    finally:
        telegram_client.is_telegram_thread_mode = saved_thread_mode
        real_ws_manager.broadcast = saved_broadcast
        for k, v in saved_attrs.items():
            setattr(main, k, v)
        htf_fvg_cache.invalidate_cache()


def test_4h_fvg_endpoint_does_not_wipe_cache_on_insufficient_candles():
    """
    If htf_fvg_cache is already bootstrapped with valid FVGs, but a subsequent
    provider fetch returns only 1 candle, the endpoint MUST NOT wipe the active FVGs.
    """
    import main
    import telegram_client
    from dashboard_ws import dashboard_ws_manager as real_ws_manager

    class ClosableSink(SinkRecorder):
        async def close(self):
            pass
        def is_configured(self):
            return False

    provider = FakeProvider(["BTC", "ETH"])
    sink = ClosableSink()

    saved_attrs = {k: getattr(main, k) for k in (
        "get_market_data_provider", "send_extreme_telegram_alert",
        "dashboard_ws_manager", "redis_client",
    )}
    saved_thread_mode = telegram_client.is_telegram_thread_mode
    saved_broadcast = real_ws_manager.broadcast

    install_patches(provider, sink)
    configure_state(main, ["BTC", "ETH"])
    htf_fvg_cache.invalidate_cache()

    try:
        with TestClient(main.app) as c:
            # First call populates cache
            resp1 = c.get("/api/extreme/4h-fvgs")
            assert resp1.status_code == 200
            btc1 = next(s for s in resp1.json()["symbols"] if s["symbol"] == "BTC")
            initial_count = btc1["fvg_count"]
            assert initial_count >= 1

            # Simulate provider returning only 1 candle on second call
            provider._feed["BTC"]["4h"] = [provider._feed["BTC"]["4h"][-1]]

            resp2 = c.get("/api/extreme/4h-fvgs")
            assert resp2.status_code == 200
            btc2 = next(s for s in resp2.json()["symbols"] if s["symbol"] == "BTC")
            # Active FVGs must still be present and not wiped to 0!
            assert btc2["fvg_count"] == initial_count
    finally:
        telegram_client.is_telegram_thread_mode = saved_thread_mode
        real_ws_manager.broadcast = saved_broadcast
        for k, v in saved_attrs.items():
            setattr(main, k, v)
        htf_fvg_cache.invalidate_cache()
