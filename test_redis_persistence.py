"""
Unit and Integration Tests for Upstash Redis State Persistence & Environment Segregation (test_redis_persistence.py)
"""

import asyncio
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from redis_client import RedisClient, get_key
from extreme_trade_tracker import ExtremeTradeTracker, TrackedExtremeTrade
from strategy_extreme_fvg import (
    HTFFVGCache,
    FVG,
    Candle,
    fvg_to_dict,
    fvg_from_dict,
)
from telegram_client import _apply_env_tag, _resolve_chat_id, send_telegram_alert


@pytest.fixture
def mock_trades():
    t1 = TrackedExtremeTrade(
        trade_id="BTC_1000",
        symbol="BTC",
        direction="Bullish",
        ltf_timeframe="5m",
        entry_price=60000.0,
        stop_loss=59000.0,
        risk_r=1000.0,
        risk_pct=1.67,
        tp_1r=61000.0,
        tp_2r=62000.0,
        tp_3r=63000.0,
        completion_target="2R",
        htf_anchor={"direction": "Bullish", "bottom": 59500.0, "top": 60500.0},
        ltf_fvg={"direction": "Bullish", "bottom": 59800.0, "top": 60000.0, "formed_at": 1000},
        state="TRADE_ACTIVE",
        status_detail="In position",
        created_at_ist="09-Sep 04:00 PM IST",
        entry_filled_at_ist="09-Sep 04:05 PM IST",
        realized_r=0.0,
        floating_r=1.2,
        max_favorable_price=61200.0,
        mfe_r=1.2,
    )
    t2 = TrackedExtremeTrade(
        trade_id="ETH_2000",
        symbol="ETH",
        direction="Bearish",
        ltf_timeframe="5m",
        entry_price=3000.0,
        stop_loss=3050.0,
        risk_r=50.0,
        risk_pct=1.67,
        tp_1r=2950.0,
        tp_2r=2900.0,
        tp_3r=2850.0,
        completion_target="2R",
        htf_anchor={"direction": "Bearish", "bottom": 2980.0, "top": 3020.0},
        ltf_fvg={"direction": "Bearish", "bottom": 3000.0, "top": 3010.0, "formed_at": 2000},
        state="COMPLETED_TP",
        status_detail="Target hit",
        created_at_ist="09-Sep 03:00 PM IST",
        closed_at_ist="09-Sep 03:45 PM IST",
        realized_r=2.0,
        mfe_r=2.2,
    )
    return [t1, t2]


@pytest.fixture
def sample_fvg():
    c1 = Candle(timestamp=100000, open=100.0, high=102.0, low=99.0, close=101.0, volume=10.0)
    c2 = Candle(timestamp=100000 + 4 * 3600 * 1000, open=101.0, high=110.0, low=101.0, close=109.0, volume=50.0)
    c3 = Candle(timestamp=100000 + 8 * 3600 * 1000, open=109.0, high=112.0, low=105.0, close=111.0, volume=20.0)
    return FVG(
        direction="Bullish",
        top=105.0,
        bottom=102.0,
        c1=c1,
        c2=c2,
        c3=c3,
        formed_at=c3.timestamp,
        timeframe="4h",
    )


# ==============================================================================
# 1. RedisClient & Environment Key Namespacing Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_redis_client_unconfigured():
    client = RedisClient(redis_url="", rest_url="", rest_token="")
    assert not client.is_configured()
    assert await client.get_str("key") is None
    assert await client.get_json("key") is None
    assert not await client.set_str("key", "val")
    assert not await client.set_json("key", {"a": 1})
    assert not await client.exists("key")
    assert not await client.delete("key")
    assert not await client.is_alert_sent("BTC", "NEW_SETUP", "trade_1")


def test_redis_key_namespacing_and_segregation():
    # Test default helper
    k1 = get_key("extreme_trades", prefix="screener:local")
    assert k1 == "screener:local:extreme_trades"

    k2 = get_key("extreme_trades", prefix="screener:prod")
    assert k2 == "screener:prod:extreme_trades"

    # Test client instance prefixing
    dev_client = RedisClient(key_prefix="screener:dev")
    assert dev_client.get_key("config") == "screener:dev:config"
    assert dev_client.get_key("alert:BTC:NEW_SETUP:123") == "screener:dev:alert:BTC:NEW_SETUP:123"

    prod_client = RedisClient(key_prefix="screener:prod")
    assert prod_client.get_key("config") == "screener:prod:config"
    assert prod_client.get_key("alert:BTC:NEW_SETUP:123") == "screener:prod:alert:BTC:NEW_SETUP:123"


@pytest.mark.asyncio
async def test_redis_client_rest_protocol():
    client = RedisClient(rest_url="https://fake-upstash.io", rest_token="fake_token", key_prefix="screener:test")
    assert client.is_configured()

    # Mock HTTP responses
    mock_http = AsyncMock()

    # Test GET
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = {"result": json.dumps({"test": 123})}
    mock_http.get.return_value = mock_get_resp

    # Test POST (SET)
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_http.post.return_value = mock_post_resp

    with patch.object(client, "_get_http", return_value=mock_http):
        val = await client.get_json("test_key")
        assert val == {"test": 123}

        success = await client.set_json("test_key", {"test": 456}, ex=3600)
        assert success is True

        # Test alert deduplication
        mock_exists_resp = MagicMock()
        mock_exists_resp.status_code = 200
        mock_exists_resp.json.return_value = {"result": 1}
        mock_http.get.return_value = mock_exists_resp

        exists = await client.is_alert_sent("BTC", "NEW_SETUP", "BTC_123")
        assert exists is True

        # Mark alert sent
        marked = await client.mark_alert_sent("BTC", "NEW_SETUP", "BTC_123")
        assert marked is True


# ==============================================================================
# 2. ExtremeTradeTracker Redis Sync Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_extreme_trade_tracker_redis_sync(tmp_path, mock_trades):
    ledger_path = str(tmp_path / "test_ledger.json")
    tracker = ExtremeTradeTracker(storage_path=ledger_path)

    t1, t2 = mock_trades
    tracker.active_trades[t1.trade_id] = t1
    tracker.history.append(t2)

    # Mock Redis client
    mock_redis = MagicMock()
    mock_redis.is_configured.return_value = True
    mock_redis.get_key.side_effect = lambda k: f"screener:local:{k}"
    mock_redis.set_json = AsyncMock(return_value=True)

    with patch("redis_client.redis_client", mock_redis):
        saved = await tracker.save_async()
        assert saved is True
        mock_redis.set_json.assert_called_once()
        args = mock_redis.set_json.call_args[0]
        assert args[0] == "screener:local:extreme_trades"
        assert "active_trades" in args[1]
        assert "BTC_1000" in args[1]["active_trades"]

    # Test load_async from Redis
    tracker2 = ExtremeTradeTracker(storage_path=str(tmp_path / "empty_ledger.json"))
    assert len(tracker2.active_trades) == 0

    mock_redis.get_json = AsyncMock(return_value={
        "active_trades": {t1.trade_id: t1.to_dict()},
        "history": [t2.to_dict()],
    })

    with patch("redis_client.redis_client", mock_redis):
        loaded = await tracker2.load_async()
        assert loaded is True
        assert len(tracker2.active_trades) == 1
        assert "BTC_1000" in tracker2.active_trades
        assert tracker2.active_trades["BTC_1000"].floating_r == 1.2
        assert len(tracker2.history) == 1
        assert tracker2.history[0].realized_r == 2.0


# ==============================================================================
# 3. HTFFVGCache Serialization & Namespaced Redis Tests
# ==============================================================================
def test_fvg_serialization(sample_fvg):
    d = fvg_to_dict(sample_fvg)
    assert d["direction"] == "Bullish"
    assert d["top"] == 105.0
    assert d["bottom"] == 102.0
    assert d["c1"]["h"] == 102.0

    restored = fvg_from_dict(d)
    assert restored.direction == "Bullish"
    assert restored.top == 105.0
    assert restored.bottom == 102.0
    assert restored.c1.high == 102.0
    assert restored.formed_at == sample_fvg.formed_at


@pytest.mark.asyncio
async def test_htf_fvg_cache_redis_save_load(sample_fvg):
    cache = HTFFVGCache()
    key = cache._key("SOL", use_close_invalidation=False)
    cache.active_fvgs[key] = [sample_fvg]
    cache.last_processed_candle_ts[key] = sample_fvg.formed_at
    cache.last_closed_candles[key] = [sample_fvg.c2, sample_fvg.c3]

    mock_redis = MagicMock()
    mock_redis.is_configured.return_value = True
    mock_redis.get_key.side_effect = lambda k: f"screener:local:{k}"
    mock_redis.set_json = AsyncMock(return_value=True)

    with patch("redis_client.redis_client", mock_redis):
        saved = await cache.save_to_redis("SOL", use_close_invalidation=False)
        assert saved is True
        mock_redis.set_json.assert_called_once()
        call_key = mock_redis.set_json.call_args[0][0]
        assert call_key == "screener:local:htf_cache:SOL:wick"

    # Test load from Redis into a fresh cache
    new_cache = HTFFVGCache()
    assert not new_cache.is_bootstrapped("SOL", use_close_invalidation=False)

    mock_redis.get_json = AsyncMock(return_value={
        "symbol": "SOL",
        "mode": "wick",
        "last_processed_candle_ts": sample_fvg.formed_at,
        "last_closed_candles": [
            {"t": sample_fvg.c2.timestamp, "o": sample_fvg.c2.open, "h": sample_fvg.c2.high, "l": sample_fvg.c2.low, "c": sample_fvg.c2.close, "v": sample_fvg.c2.volume},
            {"t": sample_fvg.c3.timestamp, "o": sample_fvg.c3.open, "h": sample_fvg.c3.high, "l": sample_fvg.c3.low, "c": sample_fvg.c3.close, "v": sample_fvg.c3.volume},
        ],
        "active_fvgs": [fvg_to_dict(sample_fvg)],
    })

    with patch("redis_client.redis_client", mock_redis):
        loaded = await new_cache.load_from_redis("SOL", use_close_invalidation=False)
        assert loaded is True
        assert new_cache.is_bootstrapped("SOL", use_close_invalidation=False)
        fvgs = new_cache.get_active_fvgs("SOL", use_close_invalidation=False)
        assert len(fvgs) == 1
        assert fvgs[0].direction == "Bullish"
        assert fvgs[0].top == 105.0


# ==============================================================================
# 4. Telegram Environment Isolation Tests
# ==============================================================================
def test_telegram_env_tagging():
    with patch("telegram_client.IS_PRODUCTION", False), patch("telegram_client.APP_ENV", "local"):
        tagged = _apply_env_tag("Hello Alert")
        assert tagged.startswith("🧪 <b>[LOCAL]</b>\nHello Alert")

    with patch("telegram_client.IS_PRODUCTION", True):
        prod_msg = _apply_env_tag("Hello Production Alert")
        assert prod_msg == "Hello Production Alert"


def test_telegram_chat_id_routing():
    with patch("telegram_client.IS_PRODUCTION", False), patch("telegram_client.TELEGRAM_DEV_CHAT_ID", "-100devchat"), patch("telegram_client.TELEGRAM_CHAT_ID", "-100prodchat"):
        assert _resolve_chat_id() == "-100devchat"

    with patch("telegram_client.IS_PRODUCTION", True), patch("telegram_client.TELEGRAM_DEV_CHAT_ID", "-100devchat"), patch("telegram_client.TELEGRAM_CHAT_ID", "-100prodchat"):
        assert _resolve_chat_id() == "-100prodchat"


@pytest.mark.asyncio
async def test_telegram_disabled_toggle():
    with patch("telegram_client.TELEGRAM_ENABLED", False):
        res = await send_telegram_alert("Test alert")
        assert res is False
