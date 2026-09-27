# Change: Bug Findings Resolution (2026-09-18)

## Why
Analysis in `docs/bug-findings-2026-09-18.md` identified 7 functional, analytical, and operational bugs across Strategy 2 execution, market data feeds, UI presentation, and backtesting:

1. **Session Filter Boot Inconsistency**: `EXTREME_SESSIONS` and `EXTREME_ENTRY_SESSIONS` defaulted to `"NY"` while boolean flags defaulted to `False`. `from_legacy` prioritized non-`ALL` strings, causing a newly booted daemon to silently discard London and Asian session setups until an operator reconfigured settings in the UI.
2. **Pending 4H Anchor Breach Invalidation Ineffective**: In `extreme_trade_tracker.py`, the 4H anchor check (`c_low < htf_bottom` / `c_high > htf_top`) was nested within `c_low <= stop_loss`. Because 4H bounds are typically deeper than LTF stop loss, 4H anchor invalidation was dead code on closed candle evaluations.
3. **Hyperliquid Alias Whitelist Missing `"GOLD"`**: `SYMBOL_ALIASES` contained `XAU`, `XAUUSD`, and `PAXGOLD` mapping to `PAXG`, but lacked `"GOLD": "PAXG"`. Lookups on Hyperliquid REST defaulted to `None`, freezing floating R at 0.
4. **Telegram Alert & Chart Lookup Provider Inconsistency**: Alert calculations and fallback chart lookups in `main.py` relied on Hyperliquid aliases instead of the active market data provider (`provider.resolve_symbol()`), causing `+0.00% away` distance bugs in Telegram notifications.
5. **Hyperliquid REST vs. WebSocket Mid-Price Asymmetry**: Hyperliquid WS applied `REVERSE_ALIASES` to map `PAXG -> GOLD` and `WTIOIL -> OIL`, but REST `get_all_mids()` returned raw exchange keys, causing price lookups to depend on socket connectivity state.
6. **Dashboard Session Preset Label Mismatch**: HTML labels in `templates/index.html` advertised `Asia (00:00 - 08:00 UTC)` and `NY Killzone (13:00 - 17:00 UTC)`, contradicting the actual engine presets (`00:00 - 09:00 UTC` and `13:00 - 16:00 UTC`).
7. **Backtest Headline Loss Double Counting**: In `backtest_extreme_fvg.py`, trades that achieved partial TP (1R) and subsequently stopped out were counted in both `wins` and `losses`, causing `wins + losses > total_trades`.

## What Changes
* **Session Boot Defaults**: Change default environment session values for `EXTREME_SESSIONS` and `EXTREME_ENTRY_SESSIONS` to `"ALL"` in `main.py` and `extreme_trade_tracker.py`.
* **Decouple Pending Invalidation**: In `extreme_trade_tracker.py`, evaluate 4H anchor breach independently from LTF SL breach on closed candles.
* **Normalize Symbol Aliases**: Add `"GOLD": "PAXG"` to `SYMBOL_ALIASES`. Introduce `REVERSE_ALIASES`, `expand_mids_with_aliases()`, and `lookup_mid()` in `hyperliquid_client.py`.
* **Provider-Aware Price Lookups**: Use `provider.resolve_symbol()` and `lookup_mid()` across screener cycle, alerts, and fallback charting in `main.py`.
* **WS / REST Mid Parity**: Both `hyperliquid_ws.py` and `hyperliquid_client.py` use `expand_mids_with_aliases()` to ensure consistent mid-price dictionaries.
* **Align Dashboard UI Labels**: Update session dropdown options in `templates/index.html` to match `SESSION_PRESETS` in `session_filter.py`.
* **Refine Backtest Headline Losses**: Exclude trades that already achieved 1R from the headline `losses` count in `backtest_extreme_fvg.py`.
* **Comprehensive Regression Suite**: Implement `test_bug_findings_2026_09_18.py` validating all 7 invariants.
