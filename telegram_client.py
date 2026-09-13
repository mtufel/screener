"""
Telegram broadcast and alert formatting module.
Handles sending HTML-formatted trading notifications to configured Telegram channels/chats
with split message support for length restrictions (4096 characters).
Supports two distinct alerts:
1. Alert 1 (Setup Formed): New LTF FVG created inside 4H zone; waiting for retrace.
2. Alert 2 (Trade Activated): Price retraces into the LTF FVG with Entry, SL, and 1R-3R TPs.
"""

import asyncio
from datetime import datetime, timezone, timedelta
import logging
import os
from typing import Dict, List, Optional, Tuple, Union
import httpx
from dotenv import load_dotenv

from strategy import SetupResult, Candle

load_dotenv()

logger = logging.getLogger(__name__)

# IST Timezone (UTC + 5:30)
IST = timezone(timedelta(hours=5, minutes=30))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4000

APP_ENV = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "local")).strip().lower()
IS_PRODUCTION = APP_ENV in ("production", "prod", "server")
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "true").strip().lower() in ("true", "1", "yes")
TELEGRAM_DEV_CHAT_ID = os.getenv("TELEGRAM_DEV_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID_DEV", os.getenv("TELEGRAM_CHAT_ID_LOCAL", ""))).strip()


def _apply_env_tag(text: str) -> str:
    """Prepends environment tag for non-production environments."""
    if IS_PRODUCTION:
        return text
    tag = f"🧪 <b>[{APP_ENV.upper()}]</b>\n"
    if text.startswith(tag) or f"[{APP_ENV.upper()}]" in text:
        return text
    return f"{tag}{text}"


def _resolve_chat_id(explicit_chat_id: Optional[str] = None) -> str:
    """Resolves target Telegram chat ID with dev/local override support."""
    if explicit_chat_id:
        return explicit_chat_id.strip()
    if not IS_PRODUCTION and TELEGRAM_DEV_CHAT_ID:
        return TELEGRAM_DEV_CHAT_ID
    return TELEGRAM_CHAT_ID


def _format_price(price: float) -> str:
    """Formats price into human-readable representation."""
    if price is None:
        return "--"
    if price >= 10:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:,.4f}"
    elif price >= 0.0001:
        return f"{price:,.6f}"
    else:
        return f"{price:,.8f}"


def format_single_setup(setup: SetupResult) -> str:
    """
    Formats a single SetupResult into either:
    - Alert 1 (Setup Formed — Waiting Retrace)
    - Alert 2 (Trade Activated — Retrace Entry with 1R-3R TPs)
    """
    is_bullish = setup.direction == "Bullish"
    ltf_name = setup.ltf_timeframe or "5m"
    sl_operator = "≤" if is_bullish else "≥"

    htf_low_str = _format_price(setup.htf_fvg.bottom)
    htf_high_str = _format_price(setup.htf_fvg.top)
    ltf_low_str = _format_price(setup.ltf_fvg.bottom)
    ltf_high_str = _format_price(setup.ltf_fvg.top)
    current_price_str = _format_price(setup.current_price)
    entry_price_str = _format_price(setup.entry_price)
    sl_str = _format_price(setup.sl_ref)

    tp1_str = _format_price(setup.tp_levels.r1)
    tp1_5_str = _format_price(setup.tp_levels.r1_5)
    tp2_str = _format_price(setup.tp_levels.r2)
    tp3_str = _format_price(setup.tp_levels.r3)

    sl_pts_str = _format_price(setup.tp_levels.sl_points)
    tp1_pts_str = _format_price(setup.tp_levels.r1_points)
    tp1_5_pts_str = _format_price(setup.tp_levels.r1_5_points)
    tp2_pts_str = _format_price(setup.tp_levels.r2_points)
    tp3_pts_str = _format_price(setup.tp_levels.r3_points)

    fvg_formed_time = setup.fvg_formation_time_ist or setup.formed_time_ist
    entry_time = setup.entry_time_ist or ("Live Retrace" if setup.stage == "ACTIVATED" else "⏳ Awaiting Retrace")

    if setup.stage == "ACTIVATED":
        emoji = "🚀 🟢" if is_bullish else "🚀 🔴"
        return (
            f"{emoji} <b>{setup.symbol}-PERP — TRADE ACTIVATED!</b>\n"
            f"<b>Direction:</b> {setup.direction} ({ltf_name} Retrace Entry)\n"
            f"<b>⏰ LTF FVG Formed:</b> {fvg_formed_time}\n"
            f"<b>⏰ 4H FVG Formed:</b> {getattr(setup.htf_fvg, 'formed_time_ist', '--') or '--'}\n"
            f"<b>🚀 Trade Entry Time:</b> {entry_time}\n"
            f"<b>Entry Price:</b> ${entry_price_str}\n"
            f"<b>Stop Loss:</b> {sl_operator} ${sl_str} (Risk: {sl_pts_str} pts / {setup.tp_levels.risk_pct:.2f}%)\n"
            f"<b>Take Profit Targets:</b>\n"
            f"  🎯 <b>1.0R:</b> ${tp1_str} (+{tp1_pts_str} pts)\n"
            f"  🎯 <b>1.5R:</b> ${tp1_5_str} (+{tp1_5_pts_str} pts)\n"
            f"  🎯 <b>2.0R:</b> ${tp2_str} (+{tp2_pts_str} pts)\n"
            f"  🎯 <b>3.0R:</b> ${tp3_str} (+{tp3_pts_str} pts)\n"
            f"<b>Zones:</b> 4H [{htf_low_str} – {htf_high_str}] | {ltf_name} [{ltf_low_str} – {ltf_high_str}]\n"
            f"<b>Score:</b> {setup.score:.2f}"
        )
    else:
        emoji = "🟡"
        return (
            f"{emoji} <b>{setup.symbol}-PERP — {setup.direction} Setup Formed</b>\n"
            f"<b>Status:</b> ⏳ Waiting for price to retrace into {ltf_name} FVG\n"
            f"<b>⏰ LTF FVG Formed:</b> {fvg_formed_time}\n"
            f"<b>⏰ 4H FVG Formed:</b> {getattr(setup.htf_fvg, 'formed_time_ist', '--') or '--'}\n"
            f"<b>Current Price:</b> ${current_price_str}\n"
            f"<b>Retrace Target Zone:</b> ${ltf_low_str} – ${ltf_high_str}\n"
            f"<b>4H FVG:</b> ${htf_low_str} – ${htf_high_str}\n"
            f"<b>Projected SL:</b> {sl_operator} ${sl_str} (Risk: {sl_pts_str} pts / {setup.tp_levels.risk_pct:.2f}%)\n"
            f"<b>Projected 2.0R TP:</b> ${tp2_str} (+{tp2_pts_str} pts)\n"
            f"<b>Score:</b> {setup.score:.2f}"
        )


def format_alert_message(setups: List[SetupResult]) -> List[str]:
    """Formats setups into Telegram message chunks with IST time."""
    if not setups:
        return []

    now_ist = datetime.now(IST).strftime("%d-%b-%Y %I:%M %p IST")
    activated = [s for s in setups if s.stage == "ACTIVATED"]
    pending = [s for s in setups if s.stage == "PENDING_RETRACE"]

    header = f"⚡ <b>Crypto FVG Screener Alerts</b> ({len(activated)} Activated, {len(pending)} Pending | {now_ist})\n\n"
    blocks = [format_single_setup(s) for s in setups]

    messages: List[str] = []
    current_msg = header

    for block in blocks:
        if len(current_msg) + len(block) + 4 > MAX_MESSAGE_LENGTH:
            messages.append(current_msg.strip())
            current_msg = f"⚡ <b>Crypto FVG Alerts (Cont.)</b>\n\n{block}\n\n"
        else:
            current_msg += block + "\n\n"

    if current_msg.strip():
        messages.append(current_msg.strip())

    return messages


_LINKED_DISCUSSION_CACHE: Dict[str, Optional[int]] = {}


async def get_linked_discussion_chat_id(chat_id: Optional[str] = None, bot_token: Optional[str] = None) -> Optional[int]:
    """
    Resolves and caches the linked discussion group chat_id for a channel.
    Returns None if chat is not a channel or has no linked discussion group.
    """
    token = (bot_token or TELEGRAM_BOT_TOKEN).strip()
    c_id = _resolve_chat_id(chat_id)
    if not token or not c_id:
        return None
    if c_id in _LINKED_DISCUSSION_CACHE:
        return _LINKED_DISCUSSION_CACHE[c_id]

    try:
        timeout_cfg = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout_cfg) as client:
            url = f"{TELEGRAM_API_BASE}/bot{token}/getChat?chat_id={c_id}"
            resp = await client.get(url)
            if resp.status_code == 200:
                linked = resp.json().get("result", {}).get("linked_chat_id")
                if linked:
                    _LINKED_DISCUSSION_CACHE[c_id] = int(linked)
                    logger.info("Discovered linked discussion group %d for channel %s", linked, c_id)
                    return int(linked)
    except Exception as exc:
        logger.debug("Could not resolve linked discussion group for %s: %s", c_id, exc)

    _LINKED_DISCUSSION_CACHE[c_id] = None
    return None


async def resolve_discussion_thread_id(
    channel_chat_id: str,
    channel_message_id: int,
    discussion_chat_id: int,
    bot_token: Optional[str] = None,
    max_attempts: int = 6,
    interval_sec: float = 0.5,
) -> Optional[int]:
    """
    Polls getUpdates to detect the automatic forward of a channel post into the linked discussion group.
    Returns the discussion message_id (which acts as the message_thread_id for comments).
    """
    token = (bot_token or TELEGRAM_BOT_TOKEN).strip()
    if not token or not channel_message_id or not discussion_chat_id:
        return None

    timeout_cfg = httpx.Timeout(10.0, connect=4.0)
    for attempt in range(max_attempts):
        await asyncio.sleep(interval_sec)
        try:
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                url = f"{TELEGRAM_API_BASE}/bot{token}/getUpdates?offset=-30"
                resp = await client.get(url)
                if resp.status_code == 200:
                    updates = resp.json().get("result", [])
                    for u in reversed(updates):
                        m = u.get("message", {})
                        if m.get("chat", {}).get("id") == discussion_chat_id:
                            fwd_id = m.get("forward_from_message_id") or m.get("forward_origin", {}).get("message_id")
                            if fwd_id == channel_message_id:
                                disc_msg_id = m.get("message_id")
                                logger.info(
                                    "Resolved channel post %d -> discussion thread ID %d (attempt %d)",
                                    channel_message_id, disc_msg_id, attempt + 1
                                )
                                return disc_msg_id
        except Exception as exc:
            logger.debug("Error checking getUpdates for discussion thread: %s", exc)

    logger.debug("Could not auto-resolve discussion thread ID for channel post %d after %d attempts", channel_message_id, max_attempts)
    return None


async def send_telegram_alert(
    text: str,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    retries: int = 3,
    reply_to_message_id: Optional[int] = None,
    return_message_id: bool = False,
    message_thread_id: Optional[int] = None,
) -> Union[bool, Tuple[bool, Optional[int]]]:
    """
    Sends a single text message to Telegram with automatic retries and exponential backoff.
    Optionally threads as a reply to reply_to_message_id and returns (success, message_id).
    """
    if not TELEGRAM_ENABLED:
        logger.info("Telegram alerting is disabled (TELEGRAM_ENABLED=false). Skipping alert.")
        return (False, None) if return_message_id else False

    token = (bot_token or TELEGRAM_BOT_TOKEN).strip()
    chat = _resolve_chat_id(chat_id)

    if not token or not chat:
        logger.debug("Telegram credentials not configured. Skipping alert.")
        return (False, None) if return_message_id else False

    formatted_text = _apply_env_tag(text)
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": formatted_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = int(reply_to_message_id)
        payload["allow_sending_without_reply"] = True
    if message_thread_id is not None:
        payload["message_thread_id"] = int(message_thread_id)

    timeout_cfg = httpx.Timeout(15.0, connect=5.0)
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    sent_msg_id = None
                    try:
                        resp_json = response.json()
                        sent_msg_id = resp_json.get("result", {}).get("message_id")
                    except Exception:
                        pass
                    logger.info("Telegram message successfully sent to chat %s (message_id=%s).", chat, sent_msg_id)
                    return (True, sent_msg_id) if return_message_id else True
                elif response.status_code == 429:
                    retry_after = 2.0 * attempt
                    try:
                        resp_json = response.json()
                        retry_after = float(resp_json.get("parameters", {}).get("retry_after", retry_after))
                    except Exception:
                        pass
                    logger.warning("Telegram rate limited (429). Retrying in %.1fs (attempt %d/%d)", retry_after, attempt, retries)
                    await asyncio.sleep(retry_after)
                else:
                    logger.warning("Telegram message failed (HTTP %d, attempt %d/%d): %s", response.status_code, attempt, retries, response.text)
                    if attempt < retries:
                        await asyncio.sleep(1.0 * attempt)
        except Exception as exc:
            logger.warning("Error communicating with Telegram API (attempt %d/%d): %s", attempt, retries, exc)
            if attempt < retries:
                await asyncio.sleep(1.0 * attempt)

    logger.error("Failed to send Telegram message after %d attempts.", retries)
    return (False, None) if return_message_id else False


async def send_telegram_photo(
    photo_bytes: bytes,
    caption: str,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    retries: int = 3,
    reply_to_message_id: Optional[int] = None,
    return_message_id: bool = False,
    message_thread_id: Optional[int] = None,
) -> Union[bool, Tuple[bool, Optional[int]]]:
    """
    Sends a photo with caption to Telegram with automatic retries, falling back to text.
    Optionally threads as a reply to reply_to_message_id and returns (success, message_id).
    """
    if not TELEGRAM_ENABLED:
        logger.info("Telegram alerting is disabled (TELEGRAM_ENABLED=false). Skipping photo alert.")
        return (False, None) if return_message_id else False

    token = (bot_token or TELEGRAM_BOT_TOKEN).strip()
    chat = _resolve_chat_id(chat_id)

    if not token or not chat:
        return (False, None) if return_message_id else False

    formatted_caption = _apply_env_tag(caption)
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendPhoto"
    data = {
        "chat_id": chat,
        "caption": formatted_caption[:1024],  # Telegram caption max 1024 chars
        "parse_mode": "HTML",
    }
    if reply_to_message_id is not None:
        data["reply_to_message_id"] = str(reply_to_message_id)
        data["allow_sending_without_reply"] = "true"
    if message_thread_id is not None:
        data["message_thread_id"] = str(message_thread_id)

    timeout_cfg = httpx.Timeout(20.0, connect=6.0)
    for attempt in range(1, retries + 1):
        try:
            files = {
                "photo": ("chart.png", photo_bytes, "image/png")
            }
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                response = await client.post(url, data=data, files=files)
                if response.status_code == 200:
                    sent_msg_id = None
                    try:
                        resp_json = response.json()
                        sent_msg_id = resp_json.get("result", {}).get("message_id")
                    except Exception:
                        pass
                    logger.info("Telegram chart photo successfully sent to chat %s (message_id=%s).", chat, sent_msg_id)
                    return (True, sent_msg_id) if return_message_id else True
                elif response.status_code == 429:
                    retry_after = 2.0 * attempt
                    try:
                        resp_json = response.json()
                        retry_after = float(resp_json.get("parameters", {}).get("retry_after", retry_after))
                    except Exception:
                        pass
                    logger.warning("Telegram photo rate limited (429). Retrying in %.1fs (attempt %d/%d)", retry_after, attempt, retries)
                    await asyncio.sleep(retry_after)
                else:
                    logger.warning("Failed to send photo (HTTP %d, attempt %d/%d): %s", response.status_code, attempt, retries, response.text)
                    if attempt < retries:
                        await asyncio.sleep(1.0 * attempt)
        except Exception as exc:
            logger.warning("Error sending Telegram photo (attempt %d/%d): %s", attempt, retries, exc)
            if attempt < retries:
                await asyncio.sleep(1.0 * attempt)

    logger.warning("Photo send exhausted retries. Falling back to text alert.")
    return await send_telegram_alert(
        caption,
        bot_token=token,
        chat_id=chat,
        retries=retries,
        reply_to_message_id=reply_to_message_id,
        return_message_id=return_message_id,
        message_thread_id=message_thread_id,
    )


async def broadcast_trade_updates(updates: List[str]) -> int:
    """Broadcasts TP/SL trade status update messages to Telegram."""
    if not updates:
        return 0

    sent_count = 0
    for msg in updates:
        success = await send_telegram_alert(msg)
        if success:
            sent_count += 1
        await asyncio.sleep(0.5)

    return sent_count


async def broadcast_setups_stateful(
    setups: List[SetupResult],
    candles_map: Optional[Dict[str, List[Candle]]] = None,
) -> int:
    """
    Dispatches setup alerts with deduplication and attached TradingView-style charts.
    Prevents duplicate alerts for the same setup.
    """
    if not setups:
        return 0

    from trade_tracker import trade_tracker

    sent_count = 0
    c_map = candles_map or {}

    for setup in setups:
        candles_ltf = c_map.get(setup.symbol, [])
        should_alert, alert_text, chart_bytes = trade_tracker.register_or_update_setup(setup, candles_ltf)

        if not should_alert or not alert_text:
            continue

        setup_id = trade_tracker.get_setup_id(setup)
        trade_obj = trade_tracker.trades.get(setup_id)
        reply_id = trade_obj.telegram_message_id if trade_obj else None

        if chart_bytes and len(chart_bytes) > 0:
            success, sent_id = await send_telegram_photo(
                chart_bytes, alert_text, reply_to_message_id=reply_id, return_message_id=True
            )
        else:
            success, sent_id = await send_telegram_alert(
                alert_text, reply_to_message_id=reply_id, return_message_id=True
            )

        if success:
            sent_count += 1
            if trade_obj and not trade_obj.telegram_message_id and sent_id:
                trade_obj.telegram_message_id = sent_id
                trade_tracker.save_state_to_disk()

        await asyncio.sleep(0.6)

    return sent_count


async def broadcast_setups(setups: List[SetupResult]) -> int:
    """Formats and dispatches all setup alerts to Telegram (legacy wrapper)."""
    return await broadcast_setups_stateful(setups)
