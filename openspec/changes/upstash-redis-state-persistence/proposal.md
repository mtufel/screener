# Proposal: Upstash Redis State Persistence

## Why

Currently, the Crypto FVG Screener stores application state in local JSON files (`data/extreme_live_trades.json`) and process memory (`HTFFVGCache`, `notified_states`, `state` dictionary in `main.py`).
When deployed on cloud platforms like Render where container disks are ephemeral:
1. **Duplicate Alerts on Restart**: Server rebuilds or restarts lose the in-memory alert history, causing the scanner to re-discover existing setups and send duplicate `NEW_SETUP` or `ENTRY_FILLED` Telegram notifications.
2. **Cold-Start Re-scan Lag**: The 4H FVG cache is wiped on restart, forcing the screener to fetch and calculate 300+ 4H candles per coin before identifying anchors.
3. **Lost Trade Ledger**: Active positions and historical performance metrics are lost on container teardowns.

Integrating **Upstash Redis** allows externalizing this state with sub-millisecond access and zero server cold-start delays.

## What Changes

- **New Resilient Redis Client (`redis_client.py`)**:
  - Supports standard `REDIS_URL` connection strings (e.g. `rediss://default:TOKEN@ENDPOINT.upstash.io:6379`) and Upstash REST API (`UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN`).
  - Automatic non-blocking fallback: if Redis is not configured or temporarily unreachable, falls back to local JSON files and memory cache with zero downtime.

- **Persistent Live Trade Ledger (`extreme_trade_tracker.py`)**:
  - `ExtremeTradeTracker` saves and restores `active_trades` and `history` to/from Redis key `screener:extreme_trades`.
  - Maintains dual-sync to local JSON for local dev debugging.

- **Persistent 4H FVG Anchor Cache (`strategy_extreme_fvg.py`)**:
  - `HTFFVGCache` stores active 4H FVGs in Redis under `screener:htf_cache:<symbol>:<mode>`.
  - On restart, the scanner loads cached anchors instantly without fetching 300+ historical candles.

- **Alert Deduplication Engine**:
  - Stores sent alerts in Redis key `screener:alert:<symbol>:<event>:<trade_id>` with a 7-day TTL.
  - Guarantees no duplicate Telegram notifications across redeployments or container restarts.

- **Persistent Runtime Configuration (`main.py`)**:
  - Runtime adjustments to config (`extreme_ltf`, `extreme_min_gap`, `extreme_session_filter`, `coins_whitelist`) persist to Redis key `screener:config` and are restored on boot.

## Capabilities

### New Capabilities
- `state-persistence`: External key-value state persistence using Upstash Redis for trade ledger, HTF anchor cache, runtime configuration, and alert deduplication.

### Modified Capabilities
- `strategy-2-extreme`: Trade lifecycle tracking and HTF anchor caching utilize Redis when available with seamless local file/memory fallback.

## Impact
- `requirements.txt`: Add `redis>=5.0.0`.
- `redis_client.py`: New unified Redis client module.
- `extreme_trade_tracker.py`: Syncs ledger and alert deduplication with Redis.
- `strategy_extreme_fvg.py`: Syncs `HTFFVGCache` with Redis.
- `main.py`: Restores runtime config and syncs updates with Redis.
