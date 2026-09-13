# Design Document: Telegram Alert Threading for Trade Lifecycles

## 1. Architecture Overview

```
                          [Screener Cycle Event Loop]
                                      │
                     ┌────────────────┴────────────────┐
                     ▼                                 ▼
           Event: NEW_SETUP                   Event: ENTRY / TP / SL
                     │                                 │
     send_extreme_telegram_alert                       │
           (returns message_id)                        │
                     │                                 │
         Store telegram_message_id                     │
          in TrackedExtremeTrade                       │
                     │                                 │
       Persist to JSON & Redis                         │
                     │                                 │
                     └────────────────┬────────────────┘
                                      │
                                      ▼
                        send_extreme_telegram_alert(
                            reply_to_message_id=tr.telegram_message_id,
                            allow_sending_without_reply=True
                        )
                                      │
                                      ▼
                             Telegram Bot API
                          (Inline Thread / Reply)
```

## 2. Component Details

### A. `telegram_client.py`
- `send_telegram_alert(text, ..., reply_to_message_id=None, return_message_id=False)`:
  - If `reply_to_message_id` is provided:
    ```python
    payload["reply_to_message_id"] = int(reply_to_message_id)
    payload["allow_sending_without_reply"] = True
    ```
  - When HTTP 200 is received:
    - Extracts `message_id = response.json().get("result", {}).get("message_id")`.
    - Returns `(True, message_id)` if `return_message_id=True`, else returns `True` (maintaining 100% backwards compatibility).
- `send_telegram_photo(photo_bytes, caption, ..., reply_to_message_id=None, return_message_id=False)`:
  - Adds `reply_to_message_id` and `allow_sending_without_reply="true"` to form-data.
  - Returns `(True, message_id)` if `return_message_id=True`, else returns `True`.
  - On retry exhaustion, passes `reply_to_message_id` and `return_message_id` down to text fallback.

### B. `extreme_trade_tracker.py`
- `TrackedExtremeTrade`:
  - New field: `telegram_message_id: Optional[int] = None`
  - In `from_dict()`: Safely deserializes `telegram_message_id` if present, defaulting to `None` for legacy records.
  - In `to_dict()`: Automatically included via `asdict(self)`.

### C. `main.py`
- `send_extreme_telegram_alert(message, image_bytes=None, reply_to_message_id=None, return_message_id=False)`:
  - Forwards `reply_to_message_id` and `return_message_id` to `send_telegram_photo` or `send_telegram_alert`.
- In `execute_extreme_screener_cycle()` event loop:
  - On `NEW_SETUP`: Call `send_extreme_telegram_alert(..., return_message_id=True)`. If `msg_id` received, update `tr.telegram_message_id = msg_id` and invoke `extreme_trade_tracker._save()`.
  - On subsequent events (`ENTRY_FILLED`, `TP_HIT`, `SL_HIT`): Pass `reply_to_message_id=tr.telegram_message_id`.

## 3. Resilience & Invariants
1. **Zero Message Loss on Deleted Parents**: Setting `allow_sending_without_reply: true` ensures that if a user deletes the setup message, subsequent TP/SL updates will still deliver cleanly instead of failing with HTTP 400 Bad Request.
2. **Backwards Compatibility**: All functions default `reply_to_message_id=None` and `return_message_id=False`, so any existing callers expecting a boolean return value will function unchanged.
3. **Restarts & Reconnection**: `telegram_message_id` is persisted to disk and Redis, surviving process crashes and reboots.
