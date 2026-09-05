# Design: Strict Backtest SL Precedence and Strategy 1 Candidate Fallback

## 1. Backtesting Engine Execution Invariant (`backtest_extreme_fvg.py`)

### 1.1 Problem Analysis
In `simulate_trade_execution`, candles are evaluated sequentially forward. Previously:
```python
# Bullish branch (old)
if not hit_1r and c.high >= tp_1r:
    hit_1r = True
if not hit_2r and c.high >= tp_2r:
    hit_2r = True
if c.high >= tp_3r:
    hit_3r = True
    exit_reason = "TP_3R"
    break

if c.low <= stop_loss:
    exit_reason = "STOPPED_OUT"
    break
```
If `c.low <= stop_loss` and `c.high >= tp_3r` occur on the same bar `c`:
- `exit_reason` becomes `"TP_3R"`
- The stop loss check is never reached due to `break`
- `realized_r_3r` is counted as `+3.0`!

Furthermore, if `c.low <= stop_loss` and `c.high >= tp_1r` on the same bar:
- `hit_1r` is set to `True`
- `c.low <= stop_loss` sets `exit_reason = "STOPPED_OUT"` and breaks
- But `realized_r_1r = 1.0 if hit_1r else -1.0` evaluates to `+1.0` (false positive win on the 1R policy).

### 1.2 Target Resolution Architecture
Under the conservative institutional execution model:
1. When evaluating bar `c`:
   - If `c.low <= stop_loss` (Bullish) or `c.high >= stop_loss` (Bearish):
     - The bar has breached Stop Loss.
     - Any TP milestone on this same bar CANNOT be verified to have occurred before the stop loss breach without sub-bar tick data.
     - Therefore, the trade is marked `exit_reason = "STOPPED_OUT"` immediately.
     - Milestones (`hit_1r`, `hit_2r`, `hit_3r`) are only validated if reached on candles where `stop_loss` was NOT breached.
   - If `stop_loss` was NOT breached on bar `c`:
     - Evaluate `hit_1r`, `hit_2r`, `hit_3r` as normal.
     - If `hit_3r` is reached, set `exit_reason = "TP_3R"` and break.

```python
if direction == "Bullish":
    max_fav_price = max(max_fav_price, c.high)
    max_adv_price = min(max_adv_price, c.low)

    # 1. Stop Loss Check FIRST (Conservative Execution)
    if c.low <= stop_loss:
        exit_reason = "STOPPED_OUT"
        break

    # 2. Take Profit Check SECOND (Only on bars that did not breach SL)
    if not hit_1r and c.high >= tp_1r:
        hit_1r = True
    if not hit_2r and c.high >= tp_2r:
        hit_2r = True
    if c.high >= tp_3r:
        hit_3r = True
        exit_reason = "TP_3R"
        break
```
*(Bearish mirror applies identically with `c.high >= stop_loss` first, followed by `c.low <= tp`.)*

---

## 2. Strategy 1 Candidate Scan Fallback (`strategy.py`)

### 2.1 Problem Analysis
In `strategy.py:721-729`:
```python
if direction == "Bullish":
    sl_ref = min(c1.low, c2.low, c3.low)
    if sl_ref >= current_price:
        if phase1_coin.htf_touch_ts is not None:
            return None
        continue
```
If `sl_ref >= current_price` on an older candidate FVG, returning `None` immediately skips any subsequent candidate FVGs in `candidate_indices`.

### 2.2 Solution
Simply use `continue` unconditionally:
```python
if direction == "Bullish":
    sl_ref = min(c1.low, c2.low, c3.low)
    if sl_ref >= current_price:
        continue
else:
    sl_ref = max(c1.high, c2.high, c3.high)
    if sl_ref <= current_price:
        continue
```
This allows the loop to check the next candidate index in `candidate_indices`. If all candidates fail, the function naturally exits and returns `None` at the end.
