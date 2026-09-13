# Specification: Telegram Alert Threading for Trade Lifecycles

## 1. Telegram API Message Threading
- **REQ-1.1**: `send_telegram_alert()` and `send_telegram_photo()` MUST accept an optional `reply_to_message_id: Optional[int] = None`.
- **REQ-1.2**: When `reply_to_message_id` is specified, the outbound request payload MUST include `reply_to_message_id` and `allow_sending_without_reply: True` (or `"true"` for multipart form-data).
- **REQ-1.3**: When `return_message_id=True` is provided, `send_telegram_alert()` and `send_telegram_photo()` MUST return a `Tuple[bool, Optional[int]]` indicating `(success, sent_message_id)` where `sent_message_id` is parsed from `response.json()["result"]["message_id"]`.
- **REQ-1.4**: If `return_message_id=False` (default), the functions MUST return a `bool` to ensure 100% backward compatibility with all existing callers.
- **REQ-1.5**: If photo delivery fails and exhausts retries, `send_telegram_photo()` MUST forward `reply_to_message_id` and `return_message_id` to the fallback `send_telegram_alert()`.

## 2. Trade Record Message ID Persistence
- **REQ-2.1**: `TrackedExtremeTrade` MUST define a `telegram_message_id: Optional[int] = None` field.
- **REQ-2.2**: `TrackedExtremeTrade.to_dict()` MUST include `telegram_message_id`.
- **REQ-2.3**: `TrackedExtremeTrade.from_dict()` MUST correctly deserialize `telegram_message_id` when present, and default to `None` when absent in legacy records.
- **REQ-2.4**: `telegram_message_id` MUST survive serialization to and deserialization from disk JSON and Redis.

## 3. Screener Event Loop Alert Linking
- **REQ-3.1**: When a `NEW_SETUP` event fires and the Telegram setup alert is sent successfully, the returned Telegram message ID MUST be stored in `tr.telegram_message_id` and saved immediately.
- **REQ-3.2**: When subsequent events (`ENTRY_FILLED`, `TP_HIT`, `SL_HIT`) fire for that trade, `send_extreme_telegram_alert` MUST pass `reply_to_message_id=tr.telegram_message_id`.
- **REQ-3.3**: If `tr.telegram_message_id` is `None` (e.g. setup alert failed or pre-dates threading), subsequent events MUST be sent as top-level messages without error.
