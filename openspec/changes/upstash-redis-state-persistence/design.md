# Design: Upstash Redis State Persistence

## Context
See `proposal.md` for motivation. This design provides persistent state management across ephemeral server restarts without introducing hard dependencies or blocking failure modes.

## Goals / Non-Goals

**Goals:**
- Zero cold-start scan delay on server startup by loading pre-computed 4H FVG anchors from Redis.
- 100% duplicate alert suppression across restarts via Redis alert keys with TTL.
- Persistent active trade tracking and historical metrics across container redeployments.
- Completely optional & resilient: If `REDIS_URL` is absent, the system operates in local-file mode without warnings or degraded functionality.
- Support both socket-based Redis protocol (`REDIS_URL`) and HTTP REST API (`UPSTASH_REDIS_REST_URL`).

**Non-Goals:**
- Replacing FastAPI in-memory request handling with Redis pub/sub.
- Storing high-frequency candle ticks (candles are fetched on-demand from Hyperliquid).

## Architecture & Data Layout

### 1. Redis Key Schema

| Key Pattern | Type | TTL | Description |
| :--- | :--- | :--- | :--- |
| `screener:extreme_trades` | String (JSON) | None | Complete serialized trade ledger (`active_trades` + `history`) |
| `screener:htf_cache:<symbol>:<mode>` | String (JSON) | 24 Hours | Cached list of active 4H FVGs for symbol and invalidation mode |
| `screener:alert:<symbol>:<event>:<id>` | String ("1") | 7 Days | Deduplication flag ensuring an alert is never sent twice |
| `screener:config` | String (JSON) | None | Persisted runtime configuration overrides |

### 2. Failure Isolation & Fallback Strategy
- Every Redis operation is wrapped in a try/catch block within `redis_client.py`.
- If Redis is unavailable or times out (> 2.0s):
  - Log a debug message (no crash, no uncaught exception).
  - Return `None` (or fallback value).
  - The caller seamlessly reads from/writes to local files and memory.

### 3. Synchronization Flow

```
+-------------------------------------------------------------+
|                     FastAPI / Screener Cycle                |
+-------------------------------------------------------------+
                               |
               +---------------+---------------+
               |                               |
       [HTFFVGCache]               [ExtremeTradeTracker]
               |                               |
      Check memory cache              Check active_trades
      If missing -> Redis            If empty on boot -> Redis
      On delta -> Save Redis         On state change -> Save Redis & Disk
               |                               |
               +---------------+---------------+
                               |
                     [redis_client.py]
                               |
          +--------------------+--------------------+
          |                                         |
 [REDIS_URL (Standard)]              [UPSTASH_REST_URL (HTTP)]
```

## Migration Plan
1. Install `redis>=5.0.0` in `.venv` and add to `requirements.txt`.
2. Implement `redis_client.py` with full error handling.
3. Integrate Redis read/write into `extreme_trade_tracker.py` and `strategy_extreme_fvg.py`.
4. Add comprehensive unit tests with MockRedis.
5. Verify test suite passes 100%.
