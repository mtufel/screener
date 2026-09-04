# Change: Strategy 2 Hardening & Findings Resolution

## Why
Analysis in `docs/strategy2-findings.md` identified operational edge cases in Strategy 2:
1. Stale `PENDING_RETRACE` setups were never monitored for invalidation or expiration, remaining in the active ledger indefinitely.
2. Alias whitelist symbols (e.g. `GOLD`) failed to map to Hyperliquid perpetual names (`PAXG`) during mid-price lookups, freezing `floating_r` at `0.00R`.
3. Default query parameters in `/api/extreme/scan` inadvertently overwrote background daemon settings during manual scan requests.

## What Changes
* **Ledger Invalidation**: Add active pre-entry monitoring in `extreme_trade_tracker.py` to transition pending trades to `INVALIDATED` if SL or 4H anchor boundaries are breached before entry fill.
* **Alias Mid-Price Resolution**: Integrate `SYMBOL_ALIASES` mapping in `extreme_trade_tracker.py` and `main.py` so alias tickers resolve live prices and compute floating $R$ dynamically.
* **Scan Parameter Defaults**: Make `/api/extreme/scan` query parameters default to `None` and inherit `state` values without mutating background daemon state.
* **Timestamp Semantics**: Ensure consistent usage of formation and close timestamps across backtester, live daemon, and chart generator.
