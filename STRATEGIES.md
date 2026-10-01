# 📖 Comprehensive Strategy Documentation

This document provides complete architectural, mathematical, and algorithmic specifications for the trading strategies in this repository.

**Runtime note:** Strategy 1 is retired. The live app, daemon, dashboard, and backtester run Strategy 2 (Extreme LTF FVG) only. The Strategy 1 section below is historical reference.

**Source material:** the primary reference videos (Atif Hussain FVG models), their verbatim transcripts, and an audit of where this implementation diverges from them are documented in [`docs/source-videos.md`](docs/source-videos.md). Read that first when changing entry, stop-loss, target, or session rules — several settings here are deliberate, evidence-based deviations from the videos and are flagged as such there.

---

## 📑 Table of Contents
1. [Core Concepts: Fair Value Gaps (FVG)](#-core-concepts-fair-value-gaps-fvg)
2. [Strategy 1: 2-Stage Standard Multi-Timeframe FVG](#-strategy-1-2-stage-standard-multi-timeframe-fvg)
   - [Overview & Workflow](#strategy-1-overview--workflow)
   - [4H HTF Selection Modes](#4h-htf-selection-modes)
   - [Invalidation Rules (Wick vs Close)](#invalidation-rules-wick-vs-close)
   - [Trade Tracker Lifecycle](#strategy-1-trade-tracker-lifecycle)
3. [Strategy 2: ⚡ Extreme LTF FVG Strategy](#-strategy-2--extreme-ltf-fvg-strategy)
   - [Overview & Architecture](#strategy-2-overview--architecture)
   - [Step 1: Incremental 4H FVG Cache](#step-1-incremental-4h-fvg-cache)
   - [Step 2: 4H Anchor Selection & First Touch Pinpointing](#step-2-4h-anchor-selection--first-touch-pinpointing)
   - [Step 3: Post-Touch LTF FVG Discovery](#step-3-post-touch-ltf-fvg-discovery)
   - [Step 4: Extreme Ranking & Selection](#step-4-extreme-ranking--selection)
   - [Step 5: Exact Execution Parameters (Entry, SL, Targets)](#step-5-exact-execution-parameters-entry-sl-targets)
   - [Step 6: Immutable Active Trade Ledger & Lifecycle State Machine](#step-6-immutable-active-trade-ledger--lifecycle-state-machine)
4. [Strategy 3: 🌊 Liquidity-Sweep FVG Strategy](#-strategy-3--liquidity-sweep-fvg-strategy)
   - [Model & Registry Identity](#strategy-3-model--registry-identity)
   - [Liquidity Module (liquidity.py)](#liquidity-module-liquiditypy)
   - [Gating Pipeline](#strategy-3-gating-pipeline)
   - [Liquidity-First Take Profit](#liquidity-first-take-profit)
   - [Validation Results](#strategy-3-validation-results)
5. [Strategy 4: 🎯 Video FVG Strategy (4H Anchor)](#-strategy-4--video-fvg-strategy-4h-anchor)
   - [Model & Registry Identity](#strategy-4-model--registry-identity)
   - [Step 1: 4H FVG Directional Anchor](#step-1-4h-fvg-directional-anchor)
   - [Step 2: HTF-Respect Confirmation](#step-2-htf-respect-confirmation)
   - [Step 3: First LTF FVG Entry Trigger](#step-3-first-ltf-fvg-entry-trigger)
   - [Step 4: Execution Parameters (Entry, SL, 3R Target)](#step-4-execution-parameters-entry-sl-3r-target)
   - [Comparison of Strategies 2, 3, and 4](#-comparison-of-strategies-2-3-and-4)
6. [Backtesting Engines & Validation](#-backtesting-engines--validation)
7. [System Architecture & Resilience](#-system-architecture--resilience)

---

## 🧩 Core Concepts: Fair Value Gaps (FVG)

A Fair Value Gap occurs during an energetic market imbalance across a 3-candle rolling sequence `[c1, c2, c3]` (`c1` oldest, `c2` middle impulse, `c3` newest):

```
         BULLISH FVG                                  BEARISH FVG
    c1        c2         c3                      c1        c2         c3
             [  ]                               ===       [  ]
             [  ]                                |        [  ]
   ===       [  ]                                |        [  ]       ===
    |        [  ]        |                       |        [  ]        |
    |        [  ]       ===                    [   ]      [  ]        |
  [   ]      [  ]        |                     [   ]      [  ]      [   ]
  [   ]      [  ]      [   ]                   [   ]      [  ]      [   ]
              ||       [   ]                    ||         ||        ||
              ||        ||                      ||                   ||
```

### Mathematical Definitions:
* **Bullish FVG**: `c3.low > c1.high`
  * **Top boundary**: `c3.low`
  * **Bottom boundary**: `c1.high`
  * **Gap Width**: `c3.low - c1.high`
  * **Midpoint**: `(c3.low + c1.high) / 2`
  * **Gap %**: `(Gap Width / Midpoint) * 100`

* **Bearish FVG**: `c3.high < c1.low`
  * **Top boundary**: `c1.low`
  * **Bottom boundary**: `c3.high`
  * **Gap Width**: `c1.low - c3.high`
  * **Midpoint**: `(c1.low + c3.high) / 2`
  * **Gap %**: `(Gap Width / Midpoint) * 100`

---

## 🏛️ Strategy 1: 2-Stage Standard Multi-Timeframe FVG

### Strategy 1 Overview & Workflow
Strategy 1 scans for confluence between the Higher Timeframe (4H) and Lower Timeframe (5m/15m).

1. **Stage 1 (4H Macro Anchor)**: Identifies if price is currently inside or recently retraced into an active, non-invalidated 4H FVG zone.
2. **Stage 2 (LTF Micro Confirmation)**: Once inside the 4H zone, checks the LTF (5m/15m) for a matching FVG in the same direction.
3. **Scoring & Ranking**:
   $$\text{Score} = 0.35 \times \text{Tightness}_{\text{4H}} + 0.35 \times \text{Tightness}_{\text{LTF}} + 0.30 \times \text{CenterProximity}$$

### 4H HTF Selection Modes
* **`ANY_VALID`**: Scans all active, non-invalidated 4H FVGs and selects any zone containing current price.
* **`RECENT_FORMED`**: Strictly selects the most recently formed 4H FVG (within the last $N$ candles).
* **`TOUCH_WINDOW`**: Selects 4H FVGs that price touched within a configured retrace window (default: 18 candles).

### Invalidation Rules (Wick vs Close)
* **Wick Invalidation (`USE_CLOSE_BASED_INVALIDATION = False`)**:
  * Bullish FVG is invalidated immediately if subsequent candle `low < fvg.bottom`.
  * Bearish FVG is invalidated immediately if subsequent candle `high > fvg.top`.
* **Close Invalidation (`USE_CLOSE_BASED_INVALIDATION = True`)**:
  * Bullish FVG is invalidated only if subsequent candle **closes** below `fvg.bottom` (`close < fvg.bottom`).
  * Bearish FVG is invalidated only if subsequent candle **closes** above `fvg.top` (`close > fvg.top`).

### Strategy 1 Trade Tracker Lifecycle
* **`PENDING_RETRACE`**: Setup discovered, waiting for price to retrace to the LTF FVG entry.
* **`TRADE_ACTIVE`**: Price fills entry level.
* **`COMPLETED_TP`**: Price reaches target ($2.0R$ default).
* **`STOPPED_OUT`**: Price breaches the 3-candle LTF swing extreme stop-loss level.

---

## ⚡ Strategy 2: ⚡ Extreme LTF FVG Strategy

Strategy 2 is a high-precision day-trading system engineered for crypto perpetuals with strict multi-timeframe isolation and zero lookahead bias.

```mermaid
flowchart TD
    A["Live 4H Candles"] --> B["Incremental 4H FVG Cache"]
    B --> C["Filter Active, Non-Invalidated 4H FVGs"]
    C --> D["Detect Most Recent Touched 4H Anchor"]
    D --> E["Pinpoint Exact First Touch Timestamp"]
    E --> F["Scan LTF Candles Formed Strictly Post-Touch"]
    F --> G["Filter Minimum Gap % (>= 0.05%)"]
    G --> H["Run LTF State Machine (Filter Stale/Blown FVGs)"]
    H --> I["Select #1 Extreme FVG (Lowest for Long, Highest for Short)"]
    I --> J["Compute Entry, Stop Loss, 1R/2R/3R Targets"]
    J --> K["Register in Immutable Active Trade Ledger"]
```

### Step 1: Incremental 4H FVG Cache
* Operates an incremental $O(1)$ cache per symbol (`HTFFVGCache`).
* On initial bootstrap, scans historical 4H closed bars and caches all valid FVGs.
* On subsequent daemon cycles, only processes new closed delta bars, updating boundary invalidations in real time.

### Step 2: 4H Anchor Selection & First Touch Pinpointing
* Identifies the single **most recent touched 4H FVG**:
  * **Currently Inside**: Priority given to 4H zones containing live market price.
  * **Most Recent Touch**: Highest `most_recent_touch_timestamp`.
* **First Touch Anchor**: Pinpoints the exact timestamp when price **first** penetrated the 4H zone post-close (`first_touch_timestamp`).
* **Strict Rule**: LTF FVG discovery will **ALWAYS start strictly from this First Touch Timestamp**. Any LTF FVG formed prior to this touch is invalid.

### Step 3: Post-Touch LTF FVG Discovery
* Scans closed LTF candles (15m default) whose Candle 3 closed at or after `first_touch_timestamp`.
* Applies **Minimum Gap Filter**: $\text{Gap \%} \ge 0.05\%$ (eliminates negligible sub-tick gaps).
* Runs candidate lifecycle state machine:
  * Discards candidate FVGs if price blew through their stop loss before touching entry (`INVALIDATED`).
  * Discards candidate FVGs that already completed targets or stopped out.
  * Retains candidates in `PENDING_RETRACE` or `TRADE_ACTIVE`.

### Step 3b: Research-backed Bias Filters (Empirical, 2026-09-26)
Four opt-in bias filters, derived from marginal backtest analysis in
`strategy_research_findings.md`, refine candidate LTF FVGs **before** lifecycle/ranking. All
are applied inside `find_unmitigated_ltf_fvgs` (and mirrored in the backtester's scan loop).
Defaults are permissive/off so existing behavior is unchanged unless enabled:

| Filter | Param | Default | Rule |
|---|---|---|---|
| **Distance from 4H zone** | `max_dist_from_4h_pct` | `2.0` (0 = off) | Reject LTF FVG whose midpoint distance from the active 4H anchor zone exceeds the ceiling. Bullish: $d = \frac{\text{bottom} - \text{anchor.bottom}}{\text{anchor.bottom}} \times 100$. Bearish: $d = \frac{\text{anchor.top} - \text{top}}{\text{anchor.top}} \times 100$. |
| **Momentum impulse-candle** | `require_momentum` | `false` | When on, reject any FVG whose middle candle `c2` is not a strong directional impulse: body ≥ 50% of range **and** direction matching the FVG (`_is_strong_momentum`). |
| **Gap ceiling** | `max_gap_pct` | `0.3` (0 = off) | Reject LTF FVGs whose `gap_pct` exceeds this ceiling (respects the existing `min_gap_pct` floor). |
| **Age ceiling** | `max_ltf_fvg_age_candles` | `9999` (permissive) | Reject LTF FVGs that form more than this many candles after the 4H first touch: $\text{age} = \text{candle}_3 \text{ index} - \text{touch index}$ (via `bisect` on LTF timestamps). |

**Empirical basis** (from `strategy_research_findings.md`): momentum-bucket setups (c2 body
≥ 50%, in-trade direction) earned **+27R vs −9R** for no-momentum; FVGs far from the 4H zone and
oversized gaps underperformed. These filters converge candidates toward confluent, high-quality
imbalances. They are exposed end-to-end: env vars → `app_config` → strategy engine → backtester
CLI (`--max-dist-from-4h-pct`, `--require-momentum`, `--max-gap-pct`, `--max-ltf-fvg-age`) →
`api/extreme` (backtest + config) → live daemon → Web dashboard (read-only display).

### Step 4: Extreme Ranking & Selection
From all valid, unmitigated LTF FVGs formed post-touch:
* **Bullish (Long)**: Selects the **Lowest Price FVG** (deepest discount, closest to the 4H anchor support zone).  $$\text{Selected FVG} = \arg\min_{f \in \text{FVGs}} (f.\text{bottom})$$
* **Bearish (Short)**: Selects the **Highest Price FVG** (highest premium, closest to the 4H anchor resistance zone).
  $$\text{Selected FVG} = \arg\max_{f \in \text{FVGs}} (f.\text{top})$$

### Step 5: Exact Execution Parameters (Entry, SL, Targets)
* **Entry Price**:
  * **Bullish**: Upper boundary of the selected LTF FVG (`ltf_fvg.top`).
  * **Bearish**: Lower boundary of the selected LTF FVG (`ltf_fvg.bottom`).
* **Stop Loss (SL)**:
  * Exact extreme wick across the 3 candles `[c1, c2, c3]` forming the LTF FVG:
  * **Bullish**: $\text{SL} = \min(c_1.\text{low}, c_2.\text{low}, c_3.\text{low})$
  * **Bearish**: $\text{SL} = \max(c_1.\text{high}, c_2.\text{high}, c_3.\text{high})$
* **Risk ($R$)**:
  $$\text{Risk } R = |\text{Entry Price} - \text{Stop Loss}|$$
* **Targets**:
  * **Target 1R**: $\text{Entry} \pm 1.0 \times R$
  * **Primary Target 2R ($\star$)**: $\text{Entry} \pm 2.0 \times R$
  * **Target 3R**: $\text{Entry} \pm 3.0 \times R$

### Step 6: Immutable Active Trade Ledger & Lifecycle State Machine
* **Single Source of Truth (`ExtremeTradeTracker`)**:
  * Persisted in `data/extreme_live_trades.json`.
  * Once a position reaches `TRADE_ACTIVE`, its entry price, stop loss, targets, and FVG anchor are **strictly locked and immutable**.
  * Subsequent scanner cycles update live dynamic metrics (`floating_r`, `mfe_r`, `current_price`) without ever overwriting or recalculating the active trade.
* **Candle Extremes for TP/SL Resolution**:
  * Evaluates post-entry closed candle extremes (`candle.high` and `candle.low` where `timestamp >= entry_timestamp`).
  * If `candle.high >= target_tp` (Bullish) or `candle.low <= target_tp` (Bearish) $\rightarrow$ `COMPLETED_TP` ($+2.0R$), moves to history, and frees the symbol for the next setup.
  * If `candle.low <= stop_loss` (Bullish) or `candle.high >= stop_loss` (Bearish) $\rightarrow$ `STOPPED_OUT` ($-1.0R$).

---

## 🌊 Strategy 3: Liquidity-Sweep FVG Strategy

### Strategy 3 Model & Registry Identity

Implements the reference video model ("Every Trader Should Know This 4H FVG Strategy"): **4H FVG for bias → liquidity sweep → LTF FVG entry → target opposing liquidity**. Registered in the pluggable framework (see `strategies/`) as:

> **⚠️ Known divergences from the source** — see [`docs/source-videos.md`](docs/source-videos.md) §4. F-01 (entry-session gate probed FVG *formation* time instead of *fill* time) and F-10 (backtester ignored `tp_mode`) are **fixed**. Still open: **F-02** — the sweep is required but its stop-loss benefit is never used.

* **Registry name**: `liquidity_sweep_fvg` (adapter `strategies/strategy3_liquidity_sweep.py`)
* **Engine**: `strategy_liquidity_sweep_fvg.py` · **Backtester**: `backtest_liquidity_sweep_fvg.py`
* Reuses Strategy 2's proven 4H machinery by import (cache, touch anchors, extreme selection); Strategy 2 files are untouched and both strategies remain selectable by name.

### Liquidity Module (liquidity.py)

Pure-function module (style of `session_filter.py`):

* **Swings**: k=2 fractal swing highs/lows; a swing contributes to pools only once its confirmation bar (k bars later) has opened — no lookahead.
* **Liquidity pools** (`LiquidityPool`): leader-clustered swing extremes within 0.05% of price → `EQUAL_HIGHS`/`EQUAL_LOWS` (≥2 touches) or `MINOR`; plus prior UTC day high/low (`PDH`/`PDL`).
* **Sweep detection** (`detect_sweep`): wick trades through the level AND candle closes back on the original side (stop-hunt signature).
* **Freshness** (`has_fresh_sweep`): an opposing-side pool swept by a candle starting within `[to_ts − max_age, to_ts)`.
* `build_pool_templates` + `pools_from_templates`: one full-series precompute, O(#pools) as-of reconstruction; `find_liquidity_pools` wraps both so live and backtest share a single code path.

### Strategy 3 Gating Pipeline

Candidates from the S2-style post-touch scan must pass, in order (all measured on 90-day backtests, see `strategy3_validation_report.html`):

1. **Gap-band exclusion** (`gap_band_exclude="0.10,0.20"`): reject LTF FVGs whose gap % falls inside the measured losing band.
2. **Fresh-sweep precondition** (`require_sweep=true`, `sweep_max_age_h=2`): an opposing-side structural pool must be swept within 2h before LTF FVG formation (the video's *sweep → FVG → entry*).
3. **Entry session** (`entry_sessions="NY_KZ"`): fills only in the NY killzone 13:00–16:00 UTC (weekday-only by default).
4. **Anchor-age guard** (`anchor_age_guard=true`): skip setups whose 4H anchor age at fill ∈ [24h, 48h) — the measured dead zone (2R WR 20% BTC / 11.8% ETH there).

Survivors are ranked with S2's extreme rule (deepest for bullish, highest for bearish).

### Liquidity-First Take Profit

With `tp_mode="LIQUIDITY"` (default): TP = nearest opposing pool at least `min_rr_for_liquidity` (1.5) R beyond entry, buffered `buffer_pct` (0.02%) in front of the level; falls back to `fallback_target_r` (2R) when no qualifying pool exists. Realized R = distance-to-TP / risk; `tp_mode` and the pool are recorded per trade.

With `tp_mode="FIXED_R"`: TP is always `entry ± fallback_target_r * risk_r`; no pool lookup.

> **Measured (90d, 5m, close invalidation):** LIQUIDITY wins on ETH (+8.8R vs +8.0R) and SOL (+16.7R vs +10.0R); **FIXED_R wins on BTC** (+11.0R vs +8.2R, PF 1.73 vs 1.58). Treat `tp_mode` as a per-symbol setting, not a global one.
>
> Backtest and live resolve TP through the same `apply_liquidity_tp()` helper, so the two agree by construction. (Before `fix/s3-backtest-tp-mode-param` the backtester ignored `tp_mode` entirely; any report labelled `FIXED_R` from before that change is invalid and must be regenerated — see `docs/source-videos.md` F-10.)

### Strategy 3 Validation Results

90-day, 5m CLOSE, BTC/ETH/SOL (full tables in `strategy3_validation_report.html`):

| Metric | S2 baseline | S3 all gates ON |
|---|---|---|
| BTC win rate / net / PF / DD | 37.3% / +10R / 1.72 / −17R | **68.8% / +11.9R / 3.38 / −1R** (16 trades; 21 w/ gap band off) |
| ETH | 34.7% / +4R | **50.0% / +14.3R / 1.65 / −4R** |
| SOL | 32.1% / −3R | **52.0% / +17.5R / 1.73 / −5R** |

Per-trade expectancy improves ~3–6×; drawdown drops 3–17×. Live stays on `extreme_fvg` until a shadow run of `liquidity_sweep_fvg` confirms parity.

### Strategy 3 Shadow (Paper) Mode

Set `EXTREME_SHADOW_STRATEGIES=liquidity_sweep_fvg` to run S3 **alongside** the active strategy in the same daemon cycle — no second process needed. Mechanics (see `test_shadow_strategies.py`):

* **Isolation scope**: the ledger partitions trades by scope (`""` = active strategy, registry name = shadow). Active/pending checks are scope-aware, so a shadow trade on BTC never suppresses the active strategy's BTC scan, and the absent-setup expiry counter only sees its own scope's emissions. Shadow trade IDs carry a `:<strategy>` suffix to avoid collisions.
* **Params**: shadow strategies resolve their own `default_params` (S3's validated gates: NY_KZ entry, sweep ≤2h, gap-band, anchor-age guard, LIQUIDITY TP) — shared daemon config (ltf/target/min_gap) applies only to the active strategy.
* **Silence**: shadow lifecycle events broadcast to the dashboard and persist to Redis/disk, but never reach Telegram; the dashboard's Live History tags them `SHADOW` with a per-strategy filter.
* **Promotion**: after ~2 weeks, compare `get_summary(strategy=...)` win rate / net R / drawdown vs the S2 live ledger, then promote via `EXTREME_ACTIVE_STRATEGY=liquidity_sweep_fvg` (and clear `EXTREME_SHADOW_STRATEGIES`).

Adapter note: the S3 engine returns setup **dicts**; `strategies/strategy3_liquidity_sweep.py` normalizes them into the attribute view the daemon payload builder expects (adds `state`, `risk_pct`, `tp_1r/2r/3r`, `entry_timestamp`).

---

## 🎯 Strategy 4: Video FVG Strategy (4H Anchor)

### Strategy 4 Model & Registry Identity

Implements the strict multi-timeframe FVG model where higher-timeframe respect must be confirmed before entering on the first subsequent LTF FVG:

* **Registry name**: `video_fvg` (adapter `strategies/strategy4_video_fvg.py`)
* **Display name**: `Video FVG (4H Anchor)`
* **Description**: `4H FVG Bias + HTF Respect Confirmation + First LTF FVG Entry + 3R Target`
* **Engine**: `strategy_video_fvg.py` · **Backtester**: `backtest_video_fvg.py`
* **Interface version**: `1`
* **Default parameters**:
  * `ltf_timeframe`: `"5m"`
  * `min_gap_pct`: `0.03`
  * `completion_target`: `"3R"`
  * `htf_confirm_body_pct`: `0.5` (50% body-to-range ratio for HTF respect)
  * `session_filter`: `False` (optional session gating at LTF formation)
  * `weekday_filter`: `False`
  * `sessions`: `"ALL"`

### Step 1: 4H FVG Directional Anchor
* **Anchor Identification**: Selects the **most recently closed 4H FVG** that has not been completely invalidated (unlike S2, which ranks by most-recent-touch, S4 prioritizes the newest formed closed 4H macro imbalance).
* **Bias Direction**:
  * Bullish 4H FVG $\rightarrow$ Bullish bias (look for longs only).
  * Bearish 4H FVG $\rightarrow$ Bearish bias (look for shorts only).

### Step 2: HTF-Respect Confirmation
* Price must retrace and touch the 4H FVG zone.
* The HTF candle touching the zone (or a subsequent candle closing in the zone) must exhibit **strong directional respect**:
  $$\text{Body Ratio} = \frac{|\text{close} - \text{open}|}{\text{high} - \text{low}} \ge \text{htf\_confirm\_body\_pct} \quad (0.50)$$
* Bullish respect requires a green close (`close > open`); Bearish respect requires a red close (`close < open`).
* The close timestamp of this confirming candle establishes `htf_confirm_timestamp`. No LTF FVG formed prior to this timestamp is eligible.

### Step 3: First LTF FVG Entry Trigger
* Once HTF respect is confirmed, the engine scans lower timeframe candles (`5m` default) strictly formed **after** the confirmation close.
* **First Qualifying FVG**: Selects the **first** LTF FVG in the bias direction that meets `min_gap_pct >= 0.03%`.
* Earlier or stale FVGs are ignored; only the fresh impulse following HTF confirmation is traded.

### Step 4: Execution Parameters (Entry, SL, 3R Target)
* **Entry Price**:
  * **Bullish**: Outer boundary of LTF FVG (`ltf_fvg.bottom`).
  * **Bearish**: Outer boundary of LTF FVG (`ltf_fvg.top`).
* **Stop Loss (SL)**:
  * Extreme wick across the 3 candles `[c1, c2, c3]` of the LTF FVG:
  * **Bullish**: $\text{SL} = \min(c_1.\text{low}, c_2.\text{low}, c_3.\text{low})$
  * **Bearish**: $\text{SL} = \max(c_1.\text{high}, c_2.\text{high}, c_3.\text{high})$
* **Risk ($R$)**:
  $$R = |\text{Entry Price} - \text{Stop Loss}|$$
* **Targets**:
  * **Target 1R**: $\text{Entry} \pm 1.0 \times R$ (telemetry)
  * **Target 2R**: $\text{Entry} \pm 2.0 \times R$ (telemetry)
  * **Primary Target 3R ($\star$)**: $\text{Entry} \pm 3.0 \times R$ (completion target)

---

## 📊 Comparison of Strategies 2, 3, and 4

| Feature | Strategy 2 (`extreme_fvg`) | Strategy 3 (`liquidity_sweep_fvg`) | Strategy 4 (`video_fvg`) |
|---|---|---|---|
| **Macro Anchor** | 4H FVG with most recent touch | 4H FVG with fresh opposing liquidity sweep | Most recent closed 4H FVG |
| **Confirmation** | Touch-based anchor activation | 2h fresh liquidity pool sweep | HTF candle body respect ($\ge 50\%$) |
| **LTF Selection** | Extreme ranking (deepest/highest) | Deepest post-touch passing sweep & gap band | First formed LTF FVG post-HTF confirm |
| **Gating Filters** | Bias filters (dist, momentum, max gap) | NY KZ, gap-band exclusion, anchor-age guard | HTF body %, min gap %, optional sessions |
| **Take Profit** | 2R default (1R/2R/3R matrix) | Opposing liquidity pool ($\ge 1.5R$) or 2R | 3R target |
| **Daemon Role** | Active production baseline | Shadow validation / Liquidity target | Multi-timeframe trend continuation |

---

## 🔬 Backtesting Engines & Validation

Both strategies feature backtesting modules with chronological candle simulation:

* **Strategy 1 Backtester (`backtest.py`)**:
  * Multi-symbol batch simulation over user-defined date ranges.
  * Win-rate, Net $R$, profit factor, and max drawdown.
* **Strategy 2 Extreme Backtester (`backtest_extreme_fvg.py`)**:
  * 1R, 2R, and 3R target resolution matrices.
  * Duration in minutes, Maximum Favorable Excursion (MFE), and chronological trade inspection.
  * Interactive TradingView chart generator showing entry-to-exit lifecycle.

---

## 🛡️ System Architecture & Resilience

1. **Hyperliquid Async Client (`hyperliquid_client.py`)**:
   * Token Bucket rate limiter (120 req/min).
   * Automatic 429 exponential cooldown coordinator with jitter.
   * Binance Kline API fallback for extended historical lookbacks.
   * Fast-fail on unsupported HIP-1 commodity pairs (`WTIOIL`, `SILVER`).
2. **Telegram Bot Dispatcher (`telegram_client.py`)**:
   * Multi-attempt retry loop with exponential backoff (`1s, 2s, 4s`).
   * High-resolution matplotlib candlestick chart attachments.
   * Auto-fallback to text formatting if photo upload is rate-limited.
3. **Interactive TradingView Chart Generator (`chart_generator.py`)**:
   * High-contrast dark theme (#0b0f19).
   * Dynamic auto-scaled Y-axis with 4H Anchor visual box (or pill if off-chart).
   * Exact entry marker arrow pointing strictly to the first post-formation touch candle.
