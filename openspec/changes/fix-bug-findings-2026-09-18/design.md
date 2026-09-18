# Design: Bug Findings Resolution (2026-09-18)

## Architecture Overview

```
                      Market Data Feeds (REST / WS)
                                  │
                                  ▼
                     hyperliquid_client.py
                ┌──────────────────────────────────┐
                │ expand_mids_with_aliases(mids)   │
                │ lookup_mid(mids, symbol, alt)    │
                │ SYMBOL_ALIASES: + GOLD -> PAXG   │
                │ REVERSE_ALIASES: PAXG -> GOLD    │
                └─────────────────┬────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
    main.py           extreme_trade_tracker.py    backtest_extreme_fvg.py
┌──────────────────┐    ┌────────────────────┐    ┌──────────────────────┐
│ Env Defaults:    │    │ Pending Monitor:   │    │ Headline Losses:     │
│ SESSIONS = "ALL" │    │ Decouple 4H Anchor │    │ Exclude trades that  │
│ Provider-aware   │    │ Breach Check from  │    │ already hit 1R       │
│ Symbol & Mid Res │    │ LTF SL Breach      │    │ (Partial Wins)       │
└──────────────────┘    └────────────────────┘    └──────────────────────┘
```

## Technical Details

### 1. Session Environment Defaults
- `EXTREME_SESSIONS` and `EXTREME_ENTRY_SESSIONS` default to `"ALL"` in `main.py` and `extreme_trade_tracker.py`.
- This matches the UI default state (`24/7 (ALL)`) and prevents unexpected filtering of Asian / London sessions on fresh daemon startup before Redis configuration load.

### 2. Decoupled Pending Invalidation
- In `extreme_trade_tracker.py` `process_live_setups`:
  - **LTF SL Invalidation (on candle close)**: Triggered if `c_low <= stop_loss` and entry was not reached (`c_high < entry_price`).
  - **4H Anchor Invalidation (on candle close)**: Triggered if `c_low < htf_bottom` (long) or `c_high > htf_top` (short), evaluated independently regardless of whether the LTF SL level was touched.

### 3. Hyperliquid Aliases & Mid-Price Resolution
- Added `"GOLD": "PAXG"` to `SYMBOL_ALIASES`.
- Defined `REVERSE_ALIASES = {v: k for k, v in SYMBOL_ALIASES.items()}`.
- Added `expand_mids_with_aliases(mids: Dict[str, float]) -> Dict[str, float]` and `lookup_mid(mids, symbol, alt_symbol=None) -> Optional[float]`.
- Applied `expand_mids_with_aliases` to both REST `get_all_mids()` and WS `allMids` stream handler.
- Replaced direct `mids.get(tr.symbol)` with `lookup_mid(mids, tr.symbol, provider_symbol)` in `main.py`.

### 4. UI Preset Label Alignment
- In `templates/index.html`:
  - `Asia (00:00 - 08:00 UTC)` updated to `Asia (00:00 - 09:00 UTC)` matching preset `(0, 540)`.
  - `NY Killzone (13:00 - 17:00 UTC)` updated to `NY Killzone (13:00 - 16:00 UTC)` matching preset `(780, 960)`.

### 5. Backtest Headline Loss Accounting
- In `backtest_extreme_fvg.py`:
  - `losses = sum(1 for t in executed_trades if t.exit_reason == "STOPPED_OUT" and not t.hit_1r)`
  - Ensures trades that secured 1R prior to stopped-out exit are not classified as full losses in headline summary cards.
