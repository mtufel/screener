# Proposal: Environment Segregation (Local vs Server)

## 1. Problem Statement
When developers test or run the Crypto FVG Screener locally while a production server is running in the cloud (e.g. Render / Fly.io / VPS):
1. **Redis Collision**: Both instances sharing the same Upstash Redis database would overwrite each other's active trade ledger (`screener:extreme_trades`), invalidate each other's 4H FVG cache, and interfere with alert deduplication keys.
2. **Alert Pollution**: Local testing could dispatch duplicate or unverified alerts into live Telegram channels without clear indication of the originating environment.
3. **Local Ledger Overwrites**: Local JSON file storage might overwrite production state backups if directories or sync scripts are shared.

## 2. Proposed Solution
Implement explicit environment segregation across all storage, caching, and alerting layers:
1. **Environment Identification (`APP_ENV`)**:
   - Supported values: `production` (or `prod` / `server`), `staging`, `development`, `local` (default: `local`).
2. **Redis Namespacing**:
   - All Redis keys are prefixed by environment namespace:
     - Production: `screener:prod:...` (or configurable `REDIS_KEY_PREFIX=screener:prod`)
     - Local/Dev: `screener:local:...` (or `screener:<APP_ENV>:...`)
   - Complete data isolation on the same Upstash Redis instance without cross-talk or collisions.
3. **Telegram Segregation**:
   - `TELEGRAM_ENABLED`: Optional boolean to enable/disable Telegram broadcasting entirely (useful for quiet local testing).
   - If alerts are sent from non-production (`APP_ENV != "production"`), alert headers automatically prepend `[LOCAL]` / `[DEV]` tag for clear identification.
   - Optional environment-specific chat ID (`TELEGRAM_DEV_CHAT_ID` / `TELEGRAM_LOCAL_CHAT_ID`).
4. **Local Ledger Segregation**:
   - Local persistence filename defaults to `data/extreme_live_trades_local.json` in local/dev mode and `data/extreme_live_trades.json` in production.

## 3. Impact & Compatibility
- 100% backward compatible with existing deployments when `APP_ENV=production` is set or default prefix is used.
- Allows running tests, simulations, and local daemons concurrently with production servers without any risk of database contamination or false live alerts.
