"""
Unit and Integration Tests for Market Data In-Memory Caching (CandleStore),
Delta Updates, Rate-Limit Guard, and Strategy Deduplication.
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from market_data_provider import (
    CandleStore,
    candle_store,
    BinanceProvider,
    OandaProvider,
    HyperliquidProvider,
)
from strategy_extreme_fvg import (
    Candle,
    FVG,
    TouchedAnchor,
    get_active_4h_fvgs_for_symbol,
    get_most_recent_touched_anchor_for_symbol,
    get_extreme_setup_for_symbol,
)


@pytest.fixture(autouse=True)
def reset_candle_store():
    candle_store.clear()
    yield
    candle_store.clear()


# ==============================================================================
# 1. CANDLESTORE UNIT TESTS
# ==============================================================================
def test_candlestore_merge_and_get():
    store = CandleStore(max_capacity=5)

    initial_candles = [
        {"t": 1000, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"t": 2000, "o": 11, "h": 13, "l": 10, "c": 12, "v": 150},
        {"t": 3000, "o": 12, "h": 14, "l": 11, "c": 13, "v": 200},
    ]

    store.merge_candles("binance_futures", "BTC", "5m", initial_candles)

    # Retrieval
    candles = store.get_candles("binance_futures", "BTC", "5m", n=2)
    assert candles is not None
    assert len(candles) == 2
    assert candles[0]["t"] == 2000
    assert candles[1]["t"] == 3000

    # Delta merge with overlap and new candle
    delta_candles = [
        {"t": 3000, "o": 12, "h": 15, "l": 11, "c": 14, "v": 250}, # updated close/high
        {"t": 4000, "o": 14, "h": 16, "l": 13, "c": 15, "v": 300},
    ]
    store.merge_candles("binance_futures", "BTC", "5m", delta_candles)

    all_candles = store.get_candles("binance_futures", "BTC", "5m", n=10)
    assert len(all_candles) == 4
    assert all_candles[2]["t"] == 3000
    assert all_candles[2]["c"] == 14 # updated
    assert all_candles[3]["t"] == 4000

    # Test max capacity trimming
    extra_candles = [
        {"t": 5000, "o": 15, "h": 17, "l": 14, "c": 16, "v": 100},
        {"t": 6000, "o": 16, "h": 18, "l": 15, "c": 17, "v": 100},
    ]
    store.merge_candles("binance_futures", "BTC", "5m", extra_candles)
    capped = store.get_candles("binance_futures", "BTC", "5m", n=10)
    assert len(capped) == 5 # max_capacity=5
    assert capped[0]["t"] == 2000 # oldest (1000) dropped


def test_candlestore_freshness_and_mids():
    store = CandleStore(default_ttl_seconds=1.0, mids_ttl_seconds=0.5)

    assert not store.is_fresh("binance_futures", "ETH", "5m")

    store.merge_candles("binance_futures", "ETH", "5m", [{"t": 1000, "c": 2500}])
    assert store.is_fresh("binance_futures", "ETH", "5m")

    # Midpoint cache
    store.set_cached_mids("binance_futures", {"ETH": 2500.0, "BTC": 60000.0})
    mids = store.get_cached_mids("binance_futures")
    assert mids is not None
    assert mids["ETH"] == 2500.0

    # Rate limiting
    assert not store.is_rate_limited("binance_futures")
    store.set_rate_limited("binance_futures", cooldown_seconds=2.0)
    assert store.is_rate_limited("binance_futures")


# ==============================================================================
# 2. BINANCE PROVIDER CACHING & DELTA TESTS
# ==============================================================================
@pytest.mark.asyncio
async def test_binance_provider_cache_hit_and_delta_query():
    provider = BinanceProvider(use_futures=True)

    # Generate 55 mock candles
    mock_history = [
        [1700000000000 + (i * 300000), "100.0", "105.0", "99.0", "104.0", "100.0", 1700000000000 + (i * 300000) + 299999, "1000", 10]
        for i in range(55)
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_history

    mock_get = AsyncMock(return_value=mock_resp)

    with patch.object(provider._http, "get", new=mock_get):
        # 1. First call: initial bootstrap fetch (limit=55)
        c1 = await provider.get_last_n_candles("SOL", timeframe="5m", n=55)
        assert len(c1) == 55
        assert mock_get.call_count == 1
        call_params = mock_get.call_args[1]["params"]
        assert call_params["limit"] == 55

        # 2. Second call within TTL: Instant Cache Hit (0 network requests!)
        c2 = await provider.get_last_n_candles("SOL", timeframe="5m", n=55)
        assert len(c2) == 55
        assert mock_get.call_count == 1  # No additional network request

        # 3. Simulate TTL expiry by artificially advancing timestamp in store
        key = candle_store._key("binance_futures", "SOL", "5m")
        candle_store._last_sync[key] = time.time() - 100.0

        # Delta response with 5 latest candles
        mock_delta = mock_history[-5:]
        mock_resp.json.return_value = mock_delta

        c3 = await provider.get_last_n_candles("SOL", timeframe="5m", n=50)
        assert len(c3) == 50
        assert mock_get.call_count == 2
        # Verify limit was 5 (delta) because it was already bootstrapped!
        call_params2 = mock_get.call_args[1]["params"]
        assert call_params2["limit"] == 5


@pytest.mark.asyncio
async def test_binance_provider_rate_limit_cooldown():
    provider = BinanceProvider(use_futures=True)

    # Pre-seed store with candles
    seed_candles = [{"t": 1000 + i, "c": 100.0, "o": 100, "h": 105, "l": 95, "v": 10, "i": "5m", "s": "SOL", "T": 1000+i+300} for i in range(50)]
    candle_store.merge_candles("binance_futures", "SOL", "5m", seed_candles)
    # Expire freshness to force fetch
    candle_store._last_sync[candle_store._key("binance_futures", "SOL", "5m")] = time.time() - 100.0

    mock_resp_418 = MagicMock()
    mock_resp_418.status_code = 418
    mock_resp_418.text = '{"code":-1003,"msg":"Way too many requests; IP banned"}'

    mock_get = AsyncMock(return_value=mock_resp_418)

    with patch.object(provider._http, "get", new=mock_get):
        # Call returns fallback from store and activates cooldown
        c = await provider.get_last_n_candles("SOL", timeframe="5m", n=50)
        assert len(c) == 50
        assert candle_store.is_rate_limited("binance_futures")

        # Subsequent call is blocked by cooldown and serves store without calling HTTP
        c2 = await provider.get_last_n_candles("SOL", timeframe="5m", n=50)
        assert len(c2) == 50
        assert mock_get.call_count == 1  # blocked by rate limit cooldown


# ==============================================================================
# 3. STRATEGY PIPELINE CANDLE REUSE TESTS
# ==============================================================================
@pytest.mark.asyncio
async def test_pipeline_candle_reuse_no_duplicate_fetches():
    """
    Verifies that calling get_extreme_setup_for_symbol with pre-passed candles
    or within pipeline does not re-fetch 4H or LTF candles redundantly.
    """
    mock_client = MagicMock()
    mock_client.name = "binance_futures"

    # 4H candles with a bullish FVG
    c_4h = [
        Candle.from_dict({"t": 1000, "o": 100, "h": 102, "l": 98, "c": 101, "v": 10}),
        Candle.from_dict({"t": 2000, "o": 101, "h": 115, "l": 101, "c": 114, "v": 20}), # FVG formed
        Candle.from_dict({"t": 3000, "o": 114, "h": 118, "l": 105, "c": 116, "v": 15}), # bottom=102, top=105
        Candle.from_dict({"t": 4000, "o": 116, "h": 117, "l": 104, "c": 110, "v": 10}), # Touched inside [102, 105]
    ]
    # LTF candles with a bullish FVG formed post-touch
    c_ltf = [
        Candle.from_dict({"t": 4000, "o": 104, "h": 105, "l": 103, "c": 104.5, "v": 5}),
        Candle.from_dict({"t": 4300, "o": 104.5, "h": 108, "l": 104.5, "c": 107.5, "v": 10}), # LTF FVG formed
        Candle.from_dict({"t": 4600, "o": 107.5, "h": 109, "l": 106, "c": 108.5, "v": 8}), # gap [105, 106]
    ]

    mock_client.get_last_n_candles = AsyncMock()

    # Call get_extreme_setup_for_symbol with pre-passed candles
    setup = await get_extreme_setup_for_symbol(
        symbol="BTC",
        ltf_timeframe="5m",
        client=mock_client,
        candles_4h=c_4h,
        candles_ltf=c_ltf,
    )

    # Should not call get_last_n_candles because both candle series were passed in!
    assert mock_client.get_last_n_candles.call_count == 0
    assert setup is not None
    assert setup.symbol == "BTC"
    assert setup.direction == "Bullish"
