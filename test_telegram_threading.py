"""
Unit and integration tests for Telegram Alert Threading and Reply Linking.
Tests:
- Payload generation with reply_to_message_id and allow_sending_without_reply
- Backward-compatible return value (bool vs Tuple[bool, Optional[int]])
- TrackedExtremeTrade and TrackedTrade dataclass serialization with telegram_message_id
- Screener daemon integration and thread linkage across lifecycle events
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import httpx

from telegram_client import send_telegram_alert, send_telegram_photo
from extreme_trade_tracker import TrackedExtremeTrade, ExtremeTradeTracker
from trade_tracker import TrackedTrade, TPLevels
from main import send_extreme_telegram_alert


# ==============================================================================
# 1. send_telegram_alert Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_send_telegram_alert_default_bool_return():
    """Verifies that send_telegram_alert returns a boolean by default for backwards compatibility."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 99991}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await send_telegram_alert("Setup formed", bot_token="fake_token", chat_id="fake_chat")
        assert res is True

        call_kwargs = mock_post.call_args.kwargs
        assert "Setup formed" in call_kwargs["json"]["text"]
        assert "reply_to_message_id" not in call_kwargs["json"]
        assert "allow_sending_without_reply" not in call_kwargs["json"]


@pytest.mark.asyncio
async def test_send_telegram_alert_with_reply_and_return_message_id():
    """Verifies that send_telegram_alert sets reply_to_message_id and returns (True, message_id)."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 12345}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        success, msg_id = await send_telegram_alert(
            "Trade Activated",
            bot_token="fake_token",
            chat_id="fake_chat",
            reply_to_message_id=9876,
            return_message_id=True,
        )
        assert success is True
        assert msg_id == 12345

        call_kwargs = mock_post.call_args.kwargs
        payload = call_kwargs["json"]
        assert payload["reply_to_message_id"] == 9876
        assert payload["allow_sending_without_reply"] is True


@pytest.mark.asyncio
async def test_send_telegram_alert_disabled_or_missing_creds():
    """Verifies behavior when alerting is disabled or credentials are missing."""
    with patch("telegram_client.TELEGRAM_ENABLED", False):
        assert await send_telegram_alert("test") is False
        assert await send_telegram_alert("test", return_message_id=True) == (False, None)

    with patch("telegram_client.TELEGRAM_ENABLED", True), \
         patch("telegram_client.TELEGRAM_BOT_TOKEN", ""), \
         patch("telegram_client.TELEGRAM_CHAT_ID", ""):
        assert await send_telegram_alert("test") is False
        assert await send_telegram_alert("test", return_message_id=True) == (False, None)


# ==============================================================================
# 2. send_telegram_photo Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_send_telegram_photo_with_reply_and_return_id():
    """Verifies send_telegram_photo includes reply_to_message_id in form-data and returns message ID."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 77712}}

    fake_png = b"\x89PNG\r\n\x1a\nfake"
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        success, msg_id = await send_telegram_photo(
            fake_png,
            "Chart Caption",
            bot_token="fake_token",
            chat_id="fake_chat",
            reply_to_message_id=5555,
            return_message_id=True,
        )
        assert success is True
        assert msg_id == 77712

        call_kwargs = mock_post.call_args.kwargs
        data = call_kwargs["data"]
        assert data["reply_to_message_id"] == "5555"
        assert data["allow_sending_without_reply"] == "true"
        assert "photo" in call_kwargs["files"]


@pytest.mark.asyncio
async def test_send_telegram_photo_fallback_preserves_reply():
    """Verifies that if photo send fails and falls back to text, reply arguments are forwarded."""
    mock_resp_fail = MagicMock(spec=httpx.Response)
    mock_resp_fail.status_code = 500
    mock_resp_fail.text = "Internal Server Error"

    mock_resp_text = MagicMock(spec=httpx.Response)
    mock_resp_text.status_code = 200
    mock_resp_text.json.return_value = {"ok": True, "result": {"message_id": 88888}}

    fake_png = b"fake"
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # 1 attempt for photo (fails), then fallback to text (succeeds)
        mock_post.side_effect = [mock_resp_fail, mock_resp_text]

        success, msg_id = await send_telegram_photo(
            fake_png,
            "Chart Caption",
            bot_token="fake_token",
            chat_id="fake_chat",
            retries=1,
            reply_to_message_id=4444,
            return_message_id=True,
        )
        assert success is True
        assert msg_id == 88888
        assert mock_post.call_count == 2
        # Verify second call was text sendMessage with reply_to_message_id
        second_call = mock_post.call_args_list[1]
        assert "sendMessage" in second_call.args[0]
        assert second_call.kwargs["json"]["reply_to_message_id"] == 4444


# ==============================================================================
# 3. TrackedExtremeTrade Dataclass & Persistence Tests
# ==============================================================================

def test_tracked_extreme_trade_message_id_serialization():
    """Verifies that telegram_message_id is preserved across serialization/deserialization."""
    trade = TrackedExtremeTrade(
        symbol="BTC",
        direction="Bullish",
        entry_price=50000.0,
        stop_loss=49000.0,
        risk_r=1000.0,
        risk_pct=2.0,
        tp_1r=51000.0,
        tp_2r=52000.0,
        tp_3r=53000.0,
        completion_target="2R",
        telegram_message_id=42042,
    )
    d = trade.to_dict()
    assert d["telegram_message_id"] == 42042

    # Roundtrip from dict
    restored = TrackedExtremeTrade.from_dict(d)
    assert restored.telegram_message_id == 42042
    assert restored.telegram_discussion_thread_id is None


def test_tracked_extreme_trade_discussion_thread_id():
    """Verifies that telegram_discussion_thread_id is preserved across serialization."""
    trade = TrackedExtremeTrade(
        symbol="BTC",
        direction="Bullish",
        entry_price=50000.0,
        stop_loss=49000.0,
        risk_r=1000.0,
        risk_pct=2.0,
        tp_1r=51000.0,
        tp_2r=52000.0,
        tp_3r=53000.0,
        completion_target="2R",
        telegram_message_id=42042,
        telegram_discussion_thread_id=55555,
    )
    d = trade.to_dict()
    assert d["telegram_discussion_thread_id"] == 55555

    restored = TrackedExtremeTrade.from_dict(d)
    assert restored.telegram_discussion_thread_id == 55555


def test_tracked_trade_strategy1_message_id():
    """Verifies that Strategy 1 TrackedTrade also supports telegram_message_id and discussion thread."""
    tp = TPLevels(r1=101, r1_5=101.5, r2=102, r3=103, risk_points=1, risk_pct=1, r1_points=1, r1_5_points=1.5, r2_points=2, r3_points=3, sl_points=1)
    t = TrackedTrade(
        setup_id="SOL:100:Bullish",
        symbol="SOL",
        direction="Bullish",
        ltf_timeframe="5m",
        entry_price=100.0,
        sl_price=99.0,
        tp_levels=tp,
        htf_fvg_bottom=98.0,
        htf_fvg_top=102.0,
        ltf_fvg_bottom=99.5,
        ltf_fvg_top=100.5,
        score=1.5,
        stage="PENDING_RETRACE",
        created_at_ist="13-Sep 10:00 AM IST",
        telegram_message_id=98765,
        telegram_discussion_thread_id=88888,
    )
    d = t.to_dict()
    assert d["telegram_message_id"] == 98765
    assert d["telegram_discussion_thread_id"] == 88888

    restored = TrackedTrade.from_dict(d)
    assert restored.telegram_message_id == 98765
    assert restored.telegram_discussion_thread_id == 88888


# ==============================================================================
# 4. Discussion Helpers & send_extreme_telegram_alert Integration
# ==============================================================================

@pytest.mark.asyncio
async def test_get_linked_discussion_chat_id():
    """Verifies get_linked_discussion_chat_id queries and caches linked_chat_id."""
    from telegram_client import get_linked_discussion_chat_id, _LINKED_DISCUSSION_CACHE
    _LINKED_DISCUSSION_CACHE.clear()

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"linked_chat_id": -100999888}}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        disc_id = await get_linked_discussion_chat_id(chat_id="-100111222", bot_token="token")
        assert disc_id == -100999888
        assert _LINKED_DISCUSSION_CACHE["-100111222"] == -100999888


@pytest.mark.asyncio
async def test_resolve_discussion_thread_id():
    """Verifies resolve_discussion_thread_id finds discussion message matching channel post."""
    from telegram_client import resolve_discussion_thread_id

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "result": [
            {
                "message": {
                    "message_id": 42,
                    "chat": {"id": -100999888},
                    "forward_from_message_id": 850,
                }
            }
        ],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        thread_id = await resolve_discussion_thread_id(
            channel_chat_id="-100111222",
            channel_message_id=850,
            discussion_chat_id=-100999888,
            bot_token="token",
            max_attempts=1,
            interval_sec=0.01,
        )
        assert thread_id == 42


@pytest.mark.asyncio
async def test_send_extreme_telegram_alert_threading_forwarding():
    """Verifies that send_extreme_telegram_alert passes reply_to_message_id and return_message_id correctly."""
    with patch("telegram_client.send_telegram_photo", new_callable=AsyncMock) as mock_photo, \
         patch("telegram_client.send_telegram_alert", new_callable=AsyncMock) as mock_text:
        
        mock_photo.return_value = (True, 1001)
        mock_text.return_value = (True, 1002)

        # 1. With image
        res_photo = await send_extreme_telegram_alert(
            message="Setup with chart",
            image_bytes=b"img",
            reply_to_message_id=500,
            return_message_id=True,
        )
        assert res_photo == (True, 1001)
        mock_photo.assert_called_once_with(
            photo_bytes=b"img",
            caption="Setup with chart",
            chat_id=None,
            reply_to_message_id=500,
            return_message_id=True,
            message_thread_id=None,
        )

        # 2. Text only with discussion thread
        res_text = await send_extreme_telegram_alert(
            message="Text update",
            image_bytes=None,
            chat_id="-100999888",
            reply_to_message_id=42,
            message_thread_id=42,
            return_message_id=True,
        )
        assert res_text == (True, 1002)
        mock_text.assert_called_once_with(
            text="Text update",
            chat_id="-100999888",
            reply_to_message_id=42,
            return_message_id=True,
            message_thread_id=42,
        )
