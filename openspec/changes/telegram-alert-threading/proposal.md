# Proposal: Telegram Alert Threading for Trade Lifecycles

## Problem Statement
In Telegram channels and chats, when a trade setup is formed (`NEW_SETUP`), and subsequent events fire (`ENTRY_FILLED`, `TP_HIT`, `SL_HIT`), each alert currently arrives as a standalone, disconnected top-level message. As multiple coins trigger setups and fills, users cannot easily associate an exit or entry alert back to the original setup without manually searching for the symbol and timestamps.

## Proposed Solution
1. **Telegram Alert Threading / Reply Linking**:
   - Capture Telegram's `message_id` returned when the initial `NEW_SETUP` alert (with chart) is delivered.
   - Persist `telegram_message_id: Optional[int]` in `TrackedExtremeTrade` and save it to both local storage (`data/extreme_live_trades_*.json`) and Redis.
   - For all subsequent lifecycle events of that trade (`ENTRY_FILLED`, `TP_HIT`, `SL_HIT`), deliver the alert message with `reply_to_message_id=tr.telegram_message_id` and `allow_sending_without_reply=True`.
   - In Telegram clients and groups, this creates an organized message thread linking the entire lifecycle of each trade back to its initial setup chart.

## Scope & Impact
- `telegram_client.py`: Extend `send_telegram_alert` and `send_telegram_photo` to accept `reply_to_message_id` and `return_message_id` parameters, extracting and returning `sent_message_id` on HTTP 200.
- `extreme_trade_tracker.py`: Add `telegram_message_id` attribute to `TrackedExtremeTrade`, update serialization/deserialization.
- `main.py`: Wire `telegram_message_id` capture on `NEW_SETUP`, pass `reply_to_message_id` on subsequent events (`ENTRY_FILLED`, `TP_HIT`, `SL_HIT`).
- `test_telegram_threading.py`: Unit and integration tests for payload formatting, threading, and message ID persistence.
