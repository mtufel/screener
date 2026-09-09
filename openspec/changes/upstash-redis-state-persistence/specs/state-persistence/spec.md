# Specification: External State Persistence & Alert Deduplication

## Purpose
Specifies behavioral requirements for persisting active trade ledgers, 4H anchor caches, runtime configurations, and Telegram alert deduplication keys to Upstash Redis with local file fallback.

## Requirements

### Requirement: Redis Client Resilience & Fallback
The Redis client SHALL provide non-blocking operations that gracefully return default/fallback values if Redis is not configured or encounters network errors.

#### Scenario: Redis not configured
- **GIVEN** `REDIS_URL` and `UPSTASH_REDIS_REST_URL` are not set
- **WHEN** any Redis read or write is invoked
- **THEN** `is_connected()` SHALL return `False`
- **AND** get operations SHALL return `None` without raising exceptions

#### Scenario: Redis connection failure
- **GIVEN** `REDIS_URL` points to an unreachable host
- **WHEN** a read or write operation is attempted
- **THEN** the error SHALL be caught and logged
- **AND** the operation SHALL return `None` or `False` safely

---

### Requirement: Trade Ledger Persistence
The system SHALL persist active and closed trades to Redis key `screener:extreme_trades` and restore them on boot.

#### Scenario: Restoring trades on startup
- **GIVEN** serialized trades exist in Redis key `screener:extreme_trades`
- **WHEN** `ExtremeTradeTracker` initializes
- **THEN** it SHALL restore `active_trades` and `history` from Redis

#### Scenario: Syncing trade state on update
- **GIVEN** an active trade state changes (e.g. `PENDING_RETRACE` -> `TRADE_ACTIVE` or `TP_HIT`)
- **WHEN** `_save()` is called on `ExtremeTradeTracker`
- **THEN** the updated state SHALL be serialized to Redis key `screener:extreme_trades`

---

### Requirement: Telegram Alert Deduplication
The system SHALL prevent sending duplicate Telegram notifications for the same trade event across restarts using Redis deduplication keys with TTL.

#### Scenario: Suppressing duplicate NEW_SETUP alert
- **GIVEN** a `NEW_SETUP` alert has been dispatched for setup `BTC:1788800000:60000`
- **AND** Redis stores key `screener:alert:BTC:NEW_SETUP:BTC:1788800000:60000`
- **WHEN** the server restarts and re-scans the market
- **THEN** the screener SHALL check the deduplication key
- **AND** SHALL NOT send a second `NEW_SETUP` Telegram message

---

### Requirement: 4H Anchor Cache Persistence
The system SHALL cache active 4H FVGs in Redis to eliminate bootstrap scan latency on cold starts.

#### Scenario: Instant anchor retrieval on restart
- **GIVEN** 4H FVGs for `BTC` are cached in Redis under `screener:htf_cache:BTC:wick`
- **WHEN** `HTFFVGCache.get_active_fvgs("BTC", use_close_invalidation=False)` is queried after a restart
- **THEN** it SHALL return the cached 4H FVGs without performing a 300-candle snapshot fetch
