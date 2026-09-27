# Specification: Bug Findings Hardening (2026-09-18)

## ADDED REQUIREMENTS

### REQ-BUG-1: Session Environment Defaults
- The daemon environment defaults for `EXTREME_SESSIONS` and `EXTREME_ENTRY_SESSIONS` MUST be `"ALL"`.
- In the absence of an explicit session string or Redis configuration override, all trading sessions MUST be allowed for FVG detection and entry execution.

### REQ-BUG-2: Pending 4H Anchor Breach Invalidation
- A pending Strategy 2 trade MUST transition to `INVALIDATED` if a closed LTF candle breaches the 4H anchor bounds (`c_low < htf_bottom` for Longs, or `c_high > htf_top` for Shorts), regardless of whether the LTF stop-loss level is touched or violated.

### REQ-BUG-3: Hyperliquid Gold Alias Support
- `SYMBOL_ALIASES` MUST map `"GOLD"` to `"PAXG"`.
- REST mid queries on Hyperliquid MUST return resolved mid-prices under `"GOLD"` when `"PAXG"` is present in the feed.

### REQ-BUG-4: Reverse Alias Expansion for REST & WebSocket Mids
- Both Hyperliquid REST `get_all_mids()` and WebSocket `allMids` feeds MUST expand mids across `REVERSE_ALIASES` so canonical and user-facing symbols resolve without lookup misses.

### REQ-BUG-5: Provider-Aware Symbol and Mid Resolution
- Live screener distance calculations and fallback chart candle requests in `main.py` MUST resolve symbols through `provider.resolve_symbol()` and look up mids with fallback alias keys (`lookup_mid`).

### REQ-BUG-6: Dashboard Session Preset Text Consistency
- UI session dropdown labels in `templates/index.html` MUST accurately state the minute boundaries defined in `SESSION_PRESETS` (`Asia: 00:00 - 09:00 UTC`, `NY Killzone: 13:00 - 16:00 UTC`).

### REQ-BUG-7: Backtest Headline Loss Accounting
- In Strategy 2 backtest summaries, headline `losses` MUST exclude trades that have achieved 1R partial target (`hit_1r == True`), preventing double-counting of trades across wins and losses.
