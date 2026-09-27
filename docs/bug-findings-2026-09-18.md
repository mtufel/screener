# Bug Findings — 2026-09-18

> Scope: live Strategy 2 daemon, session filter defaults, symbol aliases,
> Hyperliquid REST vs WS, dashboard labels, backtest reporting.
> Method: code review against current sources (not a full pytest run).
> Branch at review: `fix/backtest-session-resolution`.
>
> Older write-up: `docs/strategy2-findings.md` (2026-09-04). Several items
> there are already fixed (see §3). This document is the current list.
>
> **Status (2026-09-18):** all items in §1 are fixed in code. Regression
> coverage: `test_bug_findings_2026_09_18.py`.

---

## 1. Confirmed bugs

### 1.1 🔴 Session filter is ON at boot while the boolean default is OFF

**Severity:** High (silent drop of valid setups on a fresh process)

**Files:**
- `main.py` lines 96–101, 342–349
- `extreme_trade_tracker.py` `from_env` lines 159–168
- `session_filter.py` `SessionFilterConfig.from_legacy` lines 192–205
- `main.py` config POST lines 1704–1712 (shows intended “off” behavior)

**Problem:** Two defaults contradict each other.

| Knob | Default |
|---|---|
| `EXTREME_SESSION_FILTER_ENABLED` | `false` |
| `EXTREME_ENTRY_SESSION_FILTER_ENABLED` | `false` |
| `EXTREME_SESSIONS` | `"NY"` |
| `EXTREME_ENTRY_SESSIONS` | `"NY"` |

`from_legacy` treats a non-`ALL` session string as authoritative even when
`session_filter=False`:

```python
if sessions is not None and sessions.strip() and sessions.strip().upper() != "ALL":
    resolved_fvg = sessions.strip()   # "NY" wins
elif session_filter is True:
    resolved_fvg = default_session
else:
    resolved_fvg = "ALL"
```

`execute_extreme_screener_cycle` always builds config this way, so a fresh
daemon **only accepts NY-session FVG formation and fills**.

That disagrees with:

- Dashboard HTML, which ships with **24/7 (ALL)** selected
  (`templates/index.html` ~822, ~845).
- Config POST: turning the boolean off sets `extreme_sessions = "ALL"`
  (`main.py` 1709–1710).

Until Redis restores a saved config or someone POSTs `/api/extreme/config`,
London / Asia / weekend setups never enter the ledger.

**Suggested fix:** Default `EXTREME_SESSIONS` / `EXTREME_ENTRY_SESSIONS` to
`ALL` (or `None`). Only apply a named session when the boolean is true **or**
the string is an explicit non-default. Align `from_env` with the config POST
path.

---

### 1.2 🟠 Pending 4H-anchor breach is dead on closed candles

**Severity:** Medium (spec miss vs Strategy 2 pending invalidation)

**File:** `extreme_trade_tracker.py` `process_live_setups` pending monitor
(~482–503)

**Problem:** Spec: a pending setup that breaks **LTF SL or 4H bounds** before
fill must go `INVALIDATED`.

Bullish path:

```python
if c_low <= trade.stop_loss or c_low < htf_bottom:
    if c_low <= trade.stop_loss and c_high < trade.entry_price:
        trade.state = "INVALIDATED"
```

The outer `or c_low < htf_bottom` never does anything. A wick through the 4H
zone that does **not** also print the LTF SL is ignored. Same for shorts
(`c_high > htf_top` vs inner `c_high >= trade.stop_loss`).

Live mid can still invalidate later if price is still through the anchor;
closed-candle 4H invalidation does not.

**Suggested fix:** Invalidate on:

- SL breach with no entry tag on that bar (`c_low <= SL and c_high < entry`), **or**
- 4H bound breach (`c_low < htf_bottom` / `c_high > htf_top`), independently.

---

### 1.3 🟠 `GOLD → PAXG` missing on Hyperliquid REST / tracker aliases

**Severity:** Medium (commodity whitelist on Hyperliquid)

**Files:**
- `hyperliquid_client.py` `SYMBOL_ALIASES` lines 26–35
- `extreme_trade_tracker.py` lines 454–458
- Spec: Strategy 2 requires `GOLD → PAXG` for live mids

**Problem:** Map has `XAU` / `XAUUSD` / `PAXGOLD` → `PAXG`, but **not** `GOLD`.

```python
SYMBOL_ALIASES.get("GOLD")  # None → stays "GOLD"
```

Binance and CCXT resolve GOLD. Hyperliquid WS copies PAXG onto GOLD via
`REVERSE_ALIASES`. Hyperliquid **REST** `allMids` does not. Tracker always
resolves through the Hyperliquid map, so without WS, GOLD mids miss and
**floating R freezes at 0 / entry**.

**Suggested fix:** Add `"GOLD": "PAXG"` to `SYMBOL_ALIASES`. Apply the same
reverse aliases on REST `get_all_mids` as WS already does.

---

### 1.4 🟠 Alert/chart path uses Hyperliquid aliases, not the active provider

**Severity:** Medium (wrong distance copy; possible empty charts)

**File:** `main.py` `execute_extreme_screener_cycle`

- Scan loop correctly uses `provider.resolve_symbol(sym)` (~363).
- Telegram NEW SETUP distance (~543):

```python
dist = ((float(mids.get(tr.symbol, tr.entry_price)) - tr.entry_price) / tr.entry_price) * 100
```

- Chart fallback (~503–509):

```python
raw_sym = SYMBOL_ALIASES.get(tr.symbol, tr.symbol)
# ...
candles_ltf = await get_last_n_candles(symbol=raw_sym, ...)
```

**Problem:** `tr.symbol` is the whitelist name (`OIL`, `GOLD`). Hyperliquid
mids are keyed `WTIOIL` / `PAXG`. Miss → fallback to `entry_price` → alert
shows **`+0.00% away`**.

On Binance, Hyperliquid `OIL → WTIOIL` then fetches `WTIOILUSDT`, which is
not a Binance pair, if `recent_candles_map` missed.

**Suggested fix:** Always resolve with `provider.resolve_symbol` and look up
mids with both alias and whitelist keys (same pattern as the scan loop).

---

### 1.5 🟡 Hyperliquid REST vs WS mids are inconsistent

**Severity:** Low–medium

**Files:**
- WS: `market_data/hyperliquid_ws.py` `REVERSE_ALIASES` (~27–31, 184–187)
- REST: `market_data/hyperliquid.py` `get_all_mids` (~57–64) → raw exchange keys

**Problem:** With the socket up, cached mids include `GOLD` / `OIL`. REST-only
(or before WS connects) they do not. Alias behavior depends on connection
state.

**Suggested fix:** Normalize REST mids with the same reverse-alias expansion
as the WS handler.

---

### 1.6 🟡 Dashboard session labels don’t match presets

**Severity:** Low (operator confusion)

**Files:** `templates/index.html` (~826–828) vs `session_filter.py` `SESSION_PRESETS`

| UI label | Actual preset |
|---|---|
| Asia “00-08 UTC” | 00:00–09:00 UTC `(0, 540)` |
| NY KZ “12-15 UTC” | 13:00–16:00 UTC `(780, 960)` |

**Suggested fix:** Align labels with `SESSION_PRESETS` (or change presets if
the UI times are the intended product).

---

### 1.7 🟡 Backtest `losses` overlaps independent 2R/3R wins

**Severity:** Low (reporting, not fill/SL order)

**File:** `backtest_extreme_fvg.py` tally ~670–673

Same-bar SL-before-TP is already implemented. Independent target flags stay
sticky: 2R on bar 1, SL on bar 2 → `hit_2r=True` **and**
`exit_reason="STOPPED_OUT"`.

```python
wins_2r = sum(1 for t in executed_trades if t.hit_2r)
losses  = sum(1 for t in executed_trades if t.exit_reason == "STOPPED_OUT")
```

`wins_2r + losses` can exceed `total_trades`. Easy to misread as
single-target P&L.

**Suggested fix:** Either document independent-target accounting in the API
payload, or compute `losses_Nr` as `STOPPED_OUT and not hit_Nr`.

---

## 2. Failure scenarios (quick)

| ID | Inputs / state | Observable failure |
|---|---|---|
| 1.1 | Fresh process, no Redis config, filters left at env defaults | FVGs formed 07:00–13:00 UTC never ingest; UI may flash ALL then jump to NY |
| 1.2 | Bullish pending; LTF low wicks below 4H `bottom` but stays above LTF SL | Setup remains `PENDING_RETRACE` |
| 1.3 | `DATA_PROVIDER=hyperliquid`, whitelist `GOLD`, WS down | `current_mids` miss; floating R stuck |
| 1.4 | Hyperliquid + `OIL` NEW_SETUP | Telegram distance `+0.00%` |
| 1.5 | Hyperliquid REST then WS connect | GOLD/OIL mids appear only after socket |
| 1.6 | Operator picks “Asia 00-08” | Engine still includes 08:00–09:00 UTC |
| 1.7 | Trade hits 2R then later SL | Report: 1 win and 1 loss for one trade |

---

## 3. Previously reported (2026-09-04) — already fixed

Do **not** re-open these unless regression tests fail:

- Stale `PENDING_RETRACE` never expiring (`absent_cycles` / `TIME_EXPIRED`)
- `/api/extreme/scan` clobbering daemon `extreme_ltf` / session flags
- Strategy 1 Phase 2 `return None` aborting the LTF search
- Backtest same-bar TP-before-SL (SL now takes precedence on the collision bar)
- Tracker `GOLD`/`OIL` floating-R freeze **on the monitor path when aliases
  and mids keys already match** (Binance GOLD, Hyperliquid OIL via
  `SYMBOL_ALIASES`) — remaining holes are 1.3–1.5

---

## 4. Out of scope / not claimed

- Full pytest / live-market verification was not run for this list.
- Strategy 1 ranking math, Redis persistence, and WS reconnect were not
  fully re-audited.
- No code changes in this document.

---

## 5. Suggested fix order

1. Session default / `from_legacy` boot behavior (1.1)
2. Provider-aware symbol + mids lookup (1.3, 1.4, 1.5)
3. Pending 4H-anchor invalidation (1.2)
4. UI labels (1.6) and backtest loss accounting (1.7)
