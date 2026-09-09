# Design Document: Environment Segregation

## Architecture & Configuration Flow

```
+-------------------------------------------------------------------------------+
|                             Environment Configuration                         |
|  APP_ENV: "local" | "development" | "staging" | "production"                  |
|  REDIS_KEY_PREFIX: Optional override (default: f"screener:{APP_ENV}")        |
|  TELEGRAM_ENABLED: "true" | "false"                                           |
+-------------------------------------------------------------------------------+
                                      |
       +------------------------------+------------------------------+
       |                                                             |
       v                                                             v
+-----------------------------+                       +-----------------------------+
|    Local / Dev Environment   |                       |    Production Server        |
|  APP_ENV=local              |                       |  APP_ENV=production         |
|                             |                       |                             |
| Redis Keys:                 |                       | Redis Keys:                 |
|  screener:local:extreme_... |                       |  screener:prod:extreme_...  |
|  screener:local:htf_cache:..|                       |  screener:prod:htf_cache:.. |
|  screener:local:alert:...   |                       |  screener:prod:alert:...    |
|                             |                       |                             |
| Local File:                 |                       | Local File:                 |
|  data/extreme_live_..._local|                       |  data/extreme_live_trades...|
|                             |                       |                             |
| Telegram Alerts:            |                       | Telegram Alerts:            |
|  Prefixed with [LOCAL]      |                       |  Standard Clean Alerts      |
+-----------------------------+                       +-----------------------------+
                               \                             /
                                \                           /
                         Shared Upstash Redis Database (Zero Collision)
```

## Redis Key Formatting Function
A unified helper `get_redis_key(sub_key: str) -> str`:
```python
def get_key(sub_key: str) -> str:
    prefix = REDIS_KEY_PREFIX or f"screener:{APP_ENV}"
    return f"{prefix}:{sub_key}"
```
