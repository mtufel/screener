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
from typing import Dict, Optional, Tuple, Union
import httpx
from dotenv import load_dotenv

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
TELEGRAM_REPLY_MODE = os.getenv("TELEGRAM_REPLY_MODE", "thread").strip().lower()


def is_telegram_thread_mode() -> bool:
    """
    Returns True if lifecycle alerts should route to discussion comment threads ('thread').
    Returns False if configured as direct channel/chat replies ('reply').
    """
    mode = os.getenv("TELEGRAM_REPLY_MODE", TELEGRAM_REPLY_MODE).strip().lower()
    return mode in ("thread", "threads", "comment", "comments")


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
