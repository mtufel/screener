# Strategy 2 (Extreme LTF FVG) — Performance Research & Recommended Changes

> **Why "the strategy is not doing good":** the live defaults run the *worst* configuration —
> **5m + wick invalidation + NO session filter** — which my backtests confirm is a consistent loser
> (−86R net across BTC/ETH/SOL over 60d). Research repeatedly showed the winning config
> (5m close + session filters) was never wired into the live defaults. This doc quantifies the
> fixes and the new bias parameters to add.

**Data basis:** 60 days of Binance candles (4H + 5m) for BTC, ETH, SOL. Close invalidation,
2R target. ~1,900 baseline trades. Methods: (A) marginal bin analysis on a single base simulation
of all executed trades, (B) selection-time cap simulation, (C) combined-filter confirmation.

---

## 1. Baseline is a clear loser — but fixable

| Symbol | Baseline trades | Baseline net 2R | PF |
|--------|-----------------|------------------|-----|
| BTC    | 630             | +18.0R           | 1.04 |
| ETH    | 528             | **−39.0R**       | 0.89 |
| SOL    | 758             | **−65.0R**       | 0.88 |
| **Total** | **1,916**    | **−86.0R**       | ~0.95 |

Large-volume baseline (no filters, 5m wick) bleeds money on ETH/SOL; only BTC is marginally
positive. This matches the field-tested reality the user is seeing.

---

## 2. Marginal impact of each candidate bias (single-factor, 60d 5m close-invalidation pool)

### 2a. Session bias — **the single biggest lever** ⭐
| Bucket   | Trades | WR 2R | Net 2R | PF |
|----------|--------|-------|--------|-----|
| NY (13–22 UTC) | 255 | 39.2% | **+45.0R** | 1.29 |
| non-NY         | 375 | 30.9% | **−27.0R** | 0.90 |

A ~72R swing entirely from trading-time. The 4H-touch → LTF-FVG logic produces far better
setups inside the NY session (higher liquidity, cleaner impulse).

### 2b. Distance of LTF FVG from the 4H zone — **confluence bias** ⭐
| Bucket | Trades | Net 2R | PF |
|--------|--------|--------|-----|
| ≤0.5% | 141 | +9.0R | 1.10 |
| 0.5–1% | 95 | +10.0R | 1.17 |
| 1–2% | 189 | **+18.0R** | 1.15 |
| **2–5%** | 198 | **−15.0R** | **0.89** |
| 5%+ | 7 | −4.0R | 0.33 |

LTF FVGs within ~2% of the 4H anchor zone retain the HTF imbalance edge; beyond ~2% they lose.
**Rejecting setups >2% away removes a −15R dead zone.** Confirmed at selection time too.

### 2c. Momentum impulse-candle bias
| Bucket | Trades | Net 2R | PF |
|--------|--------|--------|-----|
| Momentum (c2 body ≥50% range, in-trade-direction) | 597 | **+27.0R** | 1.07 |
| no-momentum | 33 | **−9.0R** | 0.64 |

Requiring the gap's impulse candle to be a strong, directional body is a clean positive filter.

### 2d. Gap-size band (operating range, not just floor)
| Bucket | Trades | Net 2R | PF |
|--------|--------|--------|-----|
| **0.05–0.1%** | 366 | **+24.0R** | 1.10 |
| 0.1–0.3% | 250 | +2.0R | 1.01 |
| 0.3–0.6% | 12 | **−6.0R** | 0.40 |
| 0.6%+ | 2 | −2.0R | 0.00 |

Small gaps (0.05–0.1%) dominate; medium gaps 0.3–0.6% lose. **Important:** raising the *floor*
(min-gap 0.1%) historically *hurt* (−10R in prior runs); the value is a **ceiling** (reject gaps
>0.3%) plus keeping the 0.05% floor. The deployed `min_gap=0.05` is correct.

### 2e. Age of FVG (LTF candles between 4H touch and FVG c3 close) — **moderate cap helps** ⭐
| Cap (session ON, avg over distance) | Trades | Net 2R |
|--------------------------------------|--------|--------|
| no cap (≤∞) | 426 | +3.0R |
| ≤5 | 9 | +3.0R (tiny) |
| ≤10 | 32 | −5.0R |
| ≤15 | 48 | +6.0R |
| ≤20 | 66 | **+12.0R** |
| **≤40** | 110 | **+19.0R** |

**Direct answer to "should we add an age-of-FVG bias?":** a moderate ceiling on FVG age
(≈20–40 LTF candles after the 4H touch) roughly **triples net R per-trade quality** (PF up,
dead/decayed imbalances removed). But **"freshest = best" is false** — capping below ~10 candles
adds noise and guts the sample. Age bias has real value *as a ceiling*, not as a recency play.

### 2f. HTF (4H anchor) age — **no stable edge, do not add**
| Bucket | Net 2R | PF |
|--------|--------|-----|
| 0–5 4H candles | +3.0R | 1.02 |
| 6–10 | −1.0R | 0.98 |
| 11–20 | +16.0R | 1.16 |
| 21+ | 0.0R | 1.00 |

Adding an HTF-age filter to the final combo **hurt** (FULL combo: −20R on ETH, cut sample to
161). Not worth it.

---

## 3. Combined-filter confirmation (best stack vs baseline)

Filters applied to captured trade set → net 2R summed over BTC+ETH+SOL (60d each):

| Config | Trades | Net 2R |
|--------|--------|--------|
| baseline | 1,916 | **−86.0R** |
| session only | 740 | −2.0R |
| session + momentum | 680 | +10.0R |
| session + distance≤2% | 531 | +21.0R |
| session + mom + dist≤2% | 485 | +34.0R |
| **session + mom + dist≤2% + small-gap(≤0.3%)** | 451 | **+56.0R** ⭐ |
| + HTF-age 6–20 (overfit) | 161 | +1.0R |

Per-symbol at the best stack (PF 2R):
- **BTC**: PF 1.52, net +46R
- **SOL**: PF 1.24, net +22R
- **ETH**: PF 0.88, net −12R *(weak regardless; see below)*

---

## 4. Caveats, robustness & symbol nuance

1. **Sample-size / overfitting risk.** Every added filter shrinks the sample (1,916 → ~450 → ~50
   when stacking). Effects become noisy and symbol-specific at tiny n. Recommend shipping the
   **3 most robust changes** (session, distance≤2%, momentum) as defaults and exposing
   gap-ceiling + age-ceiling as *tunable* knobs rather than forcing all six.
2. **ETH is structurally weak.** Even the best stack leaves ETH at PF 0.88 / −12R. ETH 5m
   FVG setups underperform BTC/SOL. Consider: (a) exclude ETH from the live whitelist, or
   (b) run ETH with a *different* config (interestingly ETH improved with distance≤2% *without*
   the session filter: PF 1.53). Symbol-specific tuning, not one size fits all.
3. **Session filter may not suit every symbol** (ETH looked better session-off in one test).
   Verify per-symbol before hard-coding.
4. **LTF is best at 5m–1h.** Prior work: 1h gives best PF but tiny sample (2–8 trades);
   5m carries volume; 15m was the worst. Keep 5m for signal count, add the quality filters.

---

## 5. Recommended changes (prioritized)

**Priority 1 — change the deployed defaults (this alone likely fixes "not doing good"):**
- `EXTREME_USE_CLOSE_INVALIDATION=true` (close beats wick in every run)
- `EXTREME_SESSION_FILTER_ENABLED=true` (NY 13–22 UTC formation) — biggest lever
- keep `EXTREME_LTF_TIMEFRAME=5m`, `EXTREME_MIN_GAP_PCT=0.05`

**Priority 2 — add bias parameters (new, OpenSpec change):**
- **Distance-from-4H cap** ~2% (confluence): reject LTF FVGs >2% from the 4H anchor zone.
- **Momentum impulse filter**: require the gap's impulse candle (c2) to be a strong
  in-direction body (body ≥ ~50% of range).
- **Gap ceiling** ~0.3%: reject oversized gaps; keep 0.05% floor.
- **Age-of-FVG ceiling** ~20–40 LTF candles post-touch (configurable; do NOT force ultra-fresh).

**Priority 3 — symbol policy:**
- Reconsider ETH in the whitelist, or make bias thresholds per-symbol.

---

## 6. Files produced by this research
- `enhanced_backtest.py` — marginal-bias simulator (single base pass + bin analysis)
- `enhanced_backtest_results.json` — all per-trade features + marginal tables (BTC 60d 5m)
- `enhanced_backtest_findings.md` — marginal analysis report
- `enhanced_confirm.py` / `enhanced_confirm_results.json` — combined-filter confirmation BTC/ETH/SOL
- `enhanced_age.py` / `enhanced_age_results.json` — FVG-age cap sweep

---

## 7. Reconciliation with the user's target strategy (video: "Every Trader Should Know This 4H FVG Strategy")

The user is implementing the 4H FVG strategy from **Atif Hussain**'s video. Its rules:
1. Mark the most recent 4H FVG (HTF anchor).
2. Wait for price to return & **respect** it (touch then bounce away) = confirmation.
3. On an LTF <4H chart, wait for the **FIRST LTF FVG** to form in the direction of the 4H FVG.
4. Enter when price returns to that LTF FVG; SL = extreme of the forming candle.
5. **Target a minimum 3:1 risk:reward.**

I fetched the video transcript and built a head-to-head backtest (`video_strategy_backtest.py`) comparing
**first-FVG selection** vs the deployed **deepest/extreme selection**, at **2R** vs **3R**, with/without session filter.
60d, 5m, close invalidation, BTC/ETH/SOL:

| Variant (session) | Trades | BTC net | ETH net | SOL net | Aggregate net |
|--------------------|--------|---------|---------|---------|----------------|
| current extreme, 2R | 426 | +5R | −13R | +11R | **+3R** |
| **video first-FVG, 2R** | 421 | +6R | −15R | +11R | **+2R** |
| video first-FVG, **3R** | 421 | −15R | −32R | −2R | **−49R** |

**Conclusions that change how to implement the video:**
- **"First FVG" vs "deepest/extreme" selection is a wash.** They select essentially the same
  trade (the first post-touch FVG is typically already the bottommost candidate). Changing the
  selection rule alone does NOT fix performance. Do not treat this as the differentiator.
- **The video's "target 3R" advice HURTS**: 3R win rate collapses to ~20% and net R turns sharply
  negative (aggregate −49R vs +2R at 2R). **Keep 2R.**
- **Session filter is the real win**, for both selection rules (aggregate −86R baseline → +3R with session).
- The video's **structure already matches the current engine** (4H anchor → LTF FVG → entry at FVG,
  SL at forming-candle extreme). What the video *doesn't* cover — and what research shows is the biggest
  lever — is **trading only in the NY session** plus the **quality bias filters** (distance ≤2%,
  momentum impulse, gap ceiling, and a moderate age ceiling).

**Net recommendation for the user's implementation:**
1. Keep the existing 4H-anchor → LTF-FVG engine structure (it already implements the video's model).
2. **Add session filter (NY 13–22 UTC) as the default** — the single biggest, most robust edge.
3. **Add the bias filters** (distance ≤2% from 4H zone, momentum impulse candle, gap ceiling ~0.3%,
   optional LTF-FVG age ceiling ~20–40 candles). These are quantified in sections 2b–2e.
4. **Keep 2R (or 2R-then-trail), do NOT switch to 3R** despite the video's advice.
5. Reconsider **ETH** in the whitelist (structurally weak in every variant), or tune per-symbol.
