# Source Video Reference & Implementation Fidelity Audit

**Purpose:** record the primary source material for this project's strategies (Atif Hussain FVG
models) and audit the implementation against it, so divergences are deliberate and documented
rather than accidental.

Nothing here changes runtime behaviour. It is reference material plus a findings list.

---

## 1. Provenance

All three videos are from the same channel: **Atif Hussain** (`@AtifHussainOG`,
`youtube.com/channel/UCjpzeWEU0-629Baz_RA-LbQ`).

| # | Video ID | Title | Duration | Uploaded | Feeds |
|---|---|---|---|---|---|
| 1 | `yg94t7WigEw` | Unfortunately, You Need to Know This FVG Trading Strategy | 5:30 | 2026-09-30 | `extreme_fvg` (S2) — the pure FVG model |
| 2 | `twe4-7EdHVY` | I Built a Trading Strategy With Just 3 Steps | ~7 min | 2026 | `liquidity_sweep_fvg` (S3) — the liquidity model |
| 3 | `-EEoeVVaO6M` | Every Trader Should Know This 4H FVG Strategy | 5:04 | 2026-09-20 | `liquidity_sweep_fvg` (S3) — cited in `strategy_liquidity_sweep_fvg.py:4` |

**The two videos the repo calls "the video model" disagree with each other** on 4H FVG selection
(§3). Video 3 is the one named in the code.

> **Note on the sponsor segments:** video 1 contains a prop-firm advertisement (1:08–2:00) and
> video 2 an AI-toolkit pitch (~1:40–2:20). Both are omitted from the rule distillation below and
> retained only in the verbatim appendix. They carry no strategy content.

---

## 2. Canonical rules by video

### Video 1 — pure FVG model

1. Timeframes: **4H** (HTF context) + **entry timeframe** (15m / 5m / 1m).
2. **Only** an FVG is required. Explicitly: *"we don't need to look for liquidity, we don't need
   to look for liquidity sweeps."*
3. **4H FVG selection:** bearish → mark the **highest** FVG; bullish → mark the **lowest** FVG.
4. Wait for price to **hit** the 4H FVG.
5. It must **respect** the zone — *"instantly shoot off in the opposite direction."* This is the
   stated confirmation.
6. On the LTF, wait for a **new** FVG forming in the same direction, and enter on it.
7. **Entry must occur in a key session** (Asia, London, New York). The LTF FVG itself
   **may form outside** the session — only the fill must be in-session.
8. **Stop loss:** at the **swing high/low**, deliberately wide. Rationale: price is unlikely to
   take out that high *and* return into the gap.
9. **Target:** *at least* 2:1.

### Video 2 — liquidity model (three steps)

1. **Bias from liquidity.** On 4H/daily, ask which high or low price is most likely to take.
   Key highs/lows hold stops; price takes them. Only trade in that direction.
2. **Liquidity sweep on the ENTRY timeframe.** Price wicks *through* a key high (against the
   bias), then reverses. *"Stop run in the opposite direction of the daily bias."*
3. **Entry** on an FVG / order block in the direction of bias.

Supporting rules:

- The sweep **must** be on the entry timeframe — *"That's the whole point."*
- **Stop loss above the swept high.** This is the stated *entire purpose* of the sweep:
  *"The whole reason of waiting for a liquidity sweep is so we could put our stop loss in the
  safest place possible… If you're not going to put your stop loss at this high, there's no point
  even waiting for a liquidity sweep."*
- **No market structure required.** *"After five years of trading just ditch market structure
  please."* No BOS/MSS confirmation.
- **Target:** *"at least 2:1… for me always 3:1. Never more than a 3:1. Fixed R:R. **No break
  evens, no partials, no trailing stop-loss.***"
- The sweep is the profit-making edge, not a bonus filter: *"when you combine liquidity, your
  direction, a fair value gap as your entry — but you didn't wait for the liquidity sweep, that
  is what [prevents] making money."*

### Video 3 — 4H FVG model

1. Mark the **most recent** 4H FVG in price action.
2. Wait for price to return to it.
3. It must **respect** it — *"instantly and aggressively shoots off in the opposite direction."*
4. Wait for a **new** FVG forming in the same direction as the 4H FVG.
5. **The FIRST such FVG that forms** — enter on it. *"The first bullish fair value gap that forms
   is essentially smart money screaming at you."*
6. Wait for price to return to it and enter.
7. **Stop loss:** *"at a minimum at this low"* — the low of **the candlestick that forms the FVG**
   (for a bullish gap, the `c1` wick low that creates the boundary). *"Do not put your stop right
   here. You do not need, especially for this model, a really tight stop loss."*
8. **Target:** 2:1 minimum; he personally uses 3:1.

---

## 3. Conflicts *within* the source material

| Rule | Video 1 | Video 2 | Video 3 | Implemented as |
|---|---|---|---|---|
| 4H FVG choice | **extreme** (high/low) | bias from liquidity | **most recent** | most recent touched (`strategy_extreme_fvg.py:776`) |
| Liquidity sweep | explicitly not needed | **mandatory**, on entry TF | not mentioned | mandatory (`strategy_liquidity_sweep_fvg.py:190`) |
| LTF entry FVG | new same-direction FVG | any (FVG/OB/OB) | **the FIRST** to form | **extreme** — deepest/highest (`strategy_extreme_fvg.py:1088`, `:1091`) |
| Respect/rejection of HTF zone | required | implied | required | **not implemented** |
| Stop loss | swing high/low | **above swept high** | `c1` wick low | `min/max(c1,c2,c3)` extremes (`strategy_extreme_fvg.py:1114`, `:1121`) |
| Entry sessions | Asia + London + NY | not stated | not stated | `NY_KZ` only |
| Target | ≥2:1 | 3:1, never more | 3:1 | `2R` default |

Videos 1 and 3 are both "4H FVG → LTF FVG" models but select the 4H zone differently. Neither
mentions the other's approach.

---

## 4. Fidelity audit — implementation vs source

Severity: **HIGH** = changes realised P&L materially or rejects setups the source endorses;
**MED** = deviates from stated source rules; **INFO** = deliberate, evidence-based deviation.

### F-01 · FIXED · Entry-session gate was applied to the wrong timestamp

`strategy_liquidity_sweep_fvg.py:186` gated candidates with
`is_entry_valid(formed_close_ts)` — the **LTF FVG formation close** — but the source constrains the
**fill**: *"It's completely fine for this fair value gap to form outside of the session, but the
entry has to be during the key time of day."*

Three things were wrong:

1. It discarded candidates whose FVG formed out-of-session, even when they would have filled
   in-session — precisely the case the source endorses.
2. It contradicted its own sibling gate: the anchor-age guard 25 lines below already probed the
   fill timestamp.
3. It contradicted the backtester, which tests `_in_entry_session(fill_ts, ...)`
   (`backtest_liquidity_sweep_fvg.py:438`). So **every session-filtered S3 backtest number
   described behaviour the live daemon did not implement.**

`extreme_trade_tracker.py` already enforced the session at fill correctly (`:551`, `:600`, `:659`,
`:678` — out-of-session touches are ignored, not deferred), so the blast radius was
**under-trading and mis-alerting, not unauthorised entries**.

**Fixed** in `fix/s3-entry-session-gate-probe`: the gate now probes `fvg.entry_timestamp` for an
already-filled candidate and defers to the tracker for one that has not filled yet.

Measured impact on real data (16 as-of snapshots across 45 days, BTC/ETH/SOL,
`entry_sessions=NY_KZ`) — candidates surviving the gate:

| Symbol | Candidates | Old rejects | New rejects | Old survivors | New survivors | Admitted |
|---|---|---|---|---|---|---|
| BTC | 14 | 12 | 6 | 2 | 8 | **+6 (+300%)** |
| ETH | 19 | 13 | 5 | 6 | 14 | **+8 (+133%)** |
| SOL | 26 | 19 | 5 | 7 | 21 | **+14 (+200%)** |
| **Total** | **59** | **44** | **16** | **15** | **43** | **+28 (+187%)** |

The old gate was rejecting **75%** of its own candidates (44 of 59). Note these are *candidates*,
not filled trades — the tracker still enforces the window at fill and many pending setups will
expire, so the realised trade-count increase will be smaller than +187%.

> **Root cause, not yet addressed:** the S3 gate pipeline is implemented twice — inline in the
> backtester and in `select_gated_ltf_fvg()`. `select_gated_ltf_fvg()` has a single caller (the live
> path) and `fill_probe_ts_ms` is passed by nobody, so the two copies can drift silently again.
> Consolidating them is the durable fix.

### F-02 · HIGH · The sweep is required, but its stop-loss benefit is never used

Video 2 states the sweep exists *solely* to buy a safe stop. The implementation gates on the
sweep (`strategy_liquidity_sweep_fvg.py:190-202`) and then computes the stop exactly as Strategy 2
does — `min(c1.low, c2.low, c3.low)` for bullish (`strategy_extreme_fvg.py:1114`). The swept
high is detected, recorded, and discarded.

- **Impact:** the gate pays its cost in lost trades (rejects `NO_FRESH_SWEEP`: 102 in validation)
  without collecting the benefit. Per the source this is a no-op filter.
- **Secondary impact — this is the fix for the fee problem.** Measured median stop distance is
  0.23–0.32% of price, while a Hyperliquid perp taker round-trip costs ~0.09% (~0.28R). Stops at
  the swept high are structurally wider and are the only stop placement in the source material
  that clears the fee hurdle.
- **Fix:** for `liquidity_sweep_fvg`, set `stop_loss = swept_pool.level` (above the high for a
  bearish setup, below the low for a bullish one) and recompute R and all targets from it.

### F-03 · HIGH · LTF FVG selection uses "extreme" where the source says "first"

`strategy_extreme_fvg.py:1086-1091` and `strategy_liquidity_sweep_fvg.py:219-222` select
`min(bottom)` for bullish / `max(top)` for bearish. Video 3 says *"the first bullish fair value gap
that forms"* is the entry.

The extreme rule **is** correct — but for the **4H anchor**, where videos 1 and 3 both use it. The
implementation appears to have applied it to both levels. Selecting the deepest post-touch FVG
systematically prefers the most-degraded, worst-location gap over the earliest fresh one.

- **Fix:** rank LTF candidates by `formed_at` ascending (first to form), and reserve the extreme
  rule for the 4H anchor selection.

### F-04 · MED · No respect / rejection filter on the 4H zone

Videos 1 and 3 both make *"instantly and aggressively shoots off in the opposite direction"* the
confirmation that price is on the right side. The implementation accepts any touch
(`strategy_extreme_fvg.py:774-776`) and does not measure displacement or rejection speed.

- **Impact:** the source's stated confirmation step is absent. Live data supports that this
  matters: trades whose anchor read `"Currently Inside"` produced 26 trades / +0.00R / 26.9% WR
  — i.e. no edge at all.
- **Fix:** require price to vacate the 4H zone by ≥ X% of gap width within N candles of first
  touch.

### F-05 · MED · Entry sessions are narrower than specified

Video 1 specifies **Asia + London + New York**. S3 defaults to `NY_KZ` (13:00–16:00 UTC,
`strategy_liquidity_sweep_fvg.py` default params); `.env` enables `NY` (13:00–22:00 UTC).

`session_filter.py:18-34` already supports `"ASIA,LONDON,NY"` via comma-separated presets, so no
new code is needed — only a default change.

Measured, and it is genuinely symbol-dependent (60d, 5m, close invalidation):

| Symbol | No filter | `NY` entry | `NY_KZ` entry |
|---|---|---|---|
| BTC | PF 2.64 | PF 1.25 | PF 0.96 |
| ETH | PF 1.12 | PF 1.56 | PF 1.77 |
| SOL | PF 0.83 | PF 0.98 | PF 1.27 |

So a per-symbol session setting is warranted rather than one global value. Asia in particular is
never tested by the current defaults.

### F-06 · FIXED · Sweep was always measured on 5m candles

`check_fresh_sweep()` hardcoded `timeframe="5m"` when building the liquidity map for the sweep gate,
ignoring the configured `ltf_timeframe`. Video 2 is explicit that the sweep must be detected
*"on your entry time frame… That's the whole point."*

Any run on `ltf_timeframe != "5m"` silently gated on the wrong candle series.

**Fixed** in `refactor/s3-single-gate-pipeline`: the function now takes `ltf_timeframe` and both
callers pass their real LTF. Defaults to `"5m"`, so 5m behaviour is unchanged. Non-5m Strategy 3
reports should be regenerated.

### F-12 · FIXED (partially) · Lookahead in the backtester's pool clustering

Not from the source material — found while consolidating the duplicated gate pipeline.

`build_pool_templates()` derives its cluster tolerance from the mean close of the **last 50 bars it
is handed** (`liquidity.py:239`). The backtester's private `pools_asof()` helper built templates
from the **entire** series and then filtered as-of, so the tolerance was computed from bars *after*
the as-of point.

Measured on BTC 5m over 60 days, at an as-of point 45 days before the window end:

| | Tolerance | Swing clusters |
|---|---|---|
| Old (`pools_asof`, full series) | 41.93 | 619 |
| Correct (as-of view) | 31.83 | **150** |

A tolerance 31.7% too wide merges distinct swing extremes into fewer, broader pools, which changes
which qualify as `EQUAL_HIGHS`/`EQUAL_LOWS` and therefore which sweeps the gate accepts —
`NO_FRESH_SWEEP` rejects moved 102 → 73 on BTC.

**Fixed for the gate path** by consolidating on `find_liquidity_pools()`, which truncates candles to
the as-of time before building templates. `test_pool_construction_has_no_lookahead` pins the
invariant: appending later bars must not change an as-of pool view.

**Still open:** `pools_asof()` remains in use for take-profit pool resolution
(`backtest_liquidity_sweep_fvg.py:454`) and carries the same exposure. It should be retired in
favour of `find_liquidity_pools(..., now_ms=fill_ts)`.

### F-07 · INFO · `completion_target` default of `2R` deviates from the source, deliberately

Videos 2 and 3 both say 3:1. Measured results contradict this, so `2R` is retained:

| Config | 60d result |
|---|---|
| `2R` | BTC +41R / PF 2.64 · ETH +9R / PF 1.12 |
| `3R` | BTC +27R / PF 1.90 · ETH +7R / PF 1.11 |
| `3R` in the live ledger | 26 trades, −7R, 15.4% WR |

This deviation is **evidence-based and should stay**. Recorded here so it is not "corrected" back
to 3R by a future reader who trusts the video over the data.

### F-08 · INFO · Gap-band exclusion and anchor-age dead zone are not in the source

`gap_band_exclude="0.10,0.20"` and `anchor_age_guard [24h,48h)`
(`strategy_liquidity_sweep_fvg.py:63`, `:141`) were reverse-fitted from backtest output, not
derived from any video. They are legitimate empirical additions but are **curve-fit risk** — two
tuned windows over a 90-day sample. Flagged so they are re-validated out-of-sample before any
promotion to live.

### F-09 · INFO · No fee, commission or slippage model anywhere

Neither `backtest_extreme_fvg.py` nor the live path models trading costs. Every performance number
in this repo, and in the committed `*.html` reports, is gross of a ~0.28R/trade round-trip cost.
This is the single largest source of divergence between reported and realised results.

### F-10 · FIXED · `tp_mode` was an inert parameter in the S3 backtester

`run_liquidity_sweep_backtest()` accepted `tp_mode`, echoed it on the report, and then ignored it:
the fill loop called the pure `liquidity_take_profit()` helper directly, and that helper has no
`tp_mode` parameter. The live engine resolves TP through `apply_liquidity_tp()`, which *does*
branch on `tp_mode` — so only the backtester was wrong, and any run labelled `FIXED_R` silently
executed liquidity-first.

**Fixed** in `fix/s3-backtest-tp-mode-param`: the backtester now calls `apply_liquidity_tp()`, so
live and backtest share one TP resolver and parity holds by construction.

The first genuine measurement of the ablation (90d, 5m, close invalidation, S3 gates) is below —
it had never been obtainable before:

| Symbol | `LIQUIDITY` | `FIXED_R` | Better |
|---|---|---|---|
| BTC | +8.16R · PF 1.58 · DD 3.9R · WR 50.0% | **+11.00R · PF 1.73 · DD 3.0R · WR 46.4%** | FIXED_R |
| ETH | **+8.79R · PF 1.38 · DD 4.0R · WR 46.5%** | +8.00R · PF 1.31 · DD 4.0R · WR 39.5% | LIQUIDITY |
| SOL | **+16.68R · PF 1.73 · DD 5.0R · WR 52.1%** | +10.00R · PF 1.36 · DD 6.0R · WR 40.4% | LIQUIDITY |
| Portfolio | **+33.63R** | +29.00R | LIQUIDITY |

So the liquidity-first TP earns its keep on ETH and SOL but *hurts* on BTC — the video's
"target opposing liquidity" is not uniformly better than a plain fixed 2R.

> **Invalidation:** any previously generated Strategy 3 report labelled `tp_mode="FIXED_R"`
> (`strategy3_validation_report.html`, and the gate ablation in
> `scratch/run_s3_validation.py`) was produced with liquidity-first TP and must be regenerated.

### F-11 · MED · The sweep gate changes nothing

`require_sweep` rejects candidates (`NO_FRESH_SWEEP`: 102 on the BTC 90d run) but the executed
trade list is **byte-identical** with the gate on and off. The rejected candidates were never the
extreme-ranked winner, so the gate costs compute and produces a large-looking reject counter while
changing no outcome.

Measured decomposition of where S3's edge actually comes from:

| Component | Effect on expectancy |
|---|---|
| Entry-session gate (NY_KZ) | **+0.019 → +0.283** — carries essentially all of it |
| Anchor-age guard `[24h,48h)` | +0.233 → +0.283 without it |
| Gap band `[0.10,0.20)` | +0.279 → +0.283 without it — marginal |
| Sweep gate | **exactly 0.000** |

S3 is functionally "Strategy 2 + a session filter + an anchor-age filter", not a liquidity-sweep
model. The sweep gate is the one video-2 rule with no measured support here.

---

## 5. Cross-video consensus (the defensible core)

Stated in all three videos:

1. 4H FVG gives **bias/context**, and price must **return to it**.
2. The 4H FVG must be **respected** — aggressive departure in the bias direction.
3. Entry on a **new** LTF FVG in the same direction as the 4H FVG.
4. **Entry only in a key session** (Asia / London / New York).
5. **Stop beyond the obvious swing extreme** — never tight.
6. **Target ≥ 2R.**

This is the model to build toward. Everything else is video-specific embellishment.

---

## 6. Candidate changes

Per `CLAUDE.md`, each of these needs its own OpenSpec change off `develop`.

| ID | Change | Addresses |
|---|---|---|
| — | ~~Backtester honours `tp_mode`~~ — **done** (`fix/s3-backtest-tp-mode-param`) | F-10 |
| — | ~~Entry-session gate probes fill time~~ — **done** (`fix/s3-entry-session-gate-probe`) | F-01 |
| — | ~~Single shared gate pipeline~~ — **done** (`refactor-s3-single-gate-pipeline`) | F-01 root cause, F-06 |
| 11 | Retire `pools_asof()` for take-profit pools (same lookahead exposure) | F-12 |
| 2 | Stop loss at the swept pool level for `liquidity_sweep_fvg`; recompute R/targets | F-02 |
| 3 | Add fee + slippage parameters to both backtesters and the ledger | F-09 |
| 4 | Select the *first* post-touch LTF FVG; keep extreme rule for the 4H anchor | F-03 |
| 5 | Respect/displacement filter on the 4H zone | F-04 |
| 6 | Per-symbol session defaults; add Asia coverage | F-05 |
| 7 | Out-of-sample re-validation of gap-band and anchor-age windows | F-08 |
| 8 | Decide whether the sweep gate stays, given it is measurably inert | F-11 |
| 9 | Per-symbol `tp_mode` (BTC prefers FIXED_R, ETH/SOL prefer LIQUIDITY) | F-10 |

---

## Appendix A — verbatim transcripts

### Video 1 — `yg94t7WigEw` · "Unfortunately, You Need to Know This FVG Trading Strategy"

> Okay, so for this model, we're going to use the 4-hour chart and then we're going to use our
> entry time frame. Now, the only thing we need to look for is a fair value gap. All right, that's
> it. So, we don't need to look for liquidity. We don't need to look for liquidity sweeps. All we
> need is a fair value gap.
>
> Now, what we're going to do, and this is for your notes, right? When we're bearish, we are going
> to mark the highest fair value gap and when we're bullish, we're going to mark the lowest fair
> value gap. So, what that looks like is we're going to mark the highest fair value gap, the
> highest bearish fair value gap, right? We're not going to mark this fair value gap here or this
> one here. We're going to mark the highest fair value gap because we are bearish. And similarly,
> if we were bullish, which we're not, right? So, we're only looking at the bearish example in
> this case. If we were bullish, we would have marked this fair value gap here, right? Not this
> one here, the lowest one here. So, when you're bearish, you go for the highest one and when
> you're bullish, you go for the lowest one.
>
> And then, all we're going to do is we're just going to wait for price to hit this fair value
> gap, right? And the moment it hits the fair value gap, we're going to go on to our lower time
> frame or our entry time frame and we're just going to look for our entry. So, this is literally
> all we need to use the higher time frame for. We just need price to hit a 4-hour fair value gap
> and then we'll go in the lower time frames and look for our entries.
>
> *[sponsor segment 1:08–2:00 — prop firm challenge, omitted]*
>
> And we're going to start off on the 15-minute chart. Now, what we want to do, what we want to
> see is we want to see price hit the fair value gap. And this right here, this is the 4-hour fair
> value gap that we just talked about. We want to see price hit the 4-hour fair value gap,
> instantly shoot off in the opposite direction, and then enter on a fair value gap, a new fair
> value gap that forms on the lower time frame, right? So, the 15-minute, 5-minute, 1-minute, and
> then ride price down, right?
>
> Now, the reason we look for that is this gives us confirmation. All right, we're not going to
> guess — let me just remove this — we're not going to guess where, you know, the daily bias is.
> We're going to wait for confirmation, right? So, when price hits this fair value gap, this 4-hour
> fair value gap, we want to see a new bearish fair value gap to form, right? We want to see a new
> bearish fair value gap to form. And if you look, right here is our new bearish fair value gap.
> So, what you're going to do is you're going to mark this out, right? And what this is, this is
> confirmation that we're on the right side of the market.
>
> So, if you remember, this big fair value gap on the 4-hour chart was a bearish fair value gap,
> right? It's not enough — it's not enough just to short price as soon as it hits this fair value
> gap. We have to wait for confirmation. So, this new fair value gap that forms, this is our
> confirmation that we're bearish, and then we just want to enter on that fair value gap and ride
> price down, right?
>
> And this is something else that's for your notes that's really important. We only want to enter
> at a key time of day, right? So, if you notice, and my time is in New York Eastern Time, right?
> At 2:00 p.m., this is around 2:00 p.m. Eastern Time. This is not a key time of day, so we don't
> want to enter like around here, right? We only want to enter at key time of day. And if you
> notice, 7:00 p.m. all the way up until 12:00 a.m. Eastern Time, that is Asia session, right? So,
> you've got Asia, you've got London, and you've got New York. All right, those are the three
> sessions that you only want to look for this setup in. And of course, if you're trading indices,
> the a.m. and p.m. session, right? Anytime during indices open is all right. So, what we're going
> to do is we're only going to look for our entries around Asia, right? Or around the key session.
>
> So, our entry is going to be at this fair value gap here, right? So, this is the fair value gap
> that formed. It's completely fine for this fair value gap to form outside of the session, but the
> entry has to be during the key time of day, right?
>
> Now, your stop loss, this is where it gets interesting. We're on the 15-minute chart, right? So,
> unusually, right, I talk about liquidity sweeps and stuff, I would say to put your stop loss at
> this high here. I still think this is a fairly safe stop loss, right? 'Cuz if you're on the
> 15-minute chart, you're a day trader, and you're probably going to hold for a few hours. So, this
> is a day trading strategy on the 15-minute chart. Now, of course, we're going to get onto the
> 5-minute and even the 1-minute chart. So, I'll show you what it's like for a scalper. So, if you
> want a really tight stop loss, you wouldn't use the 15-minute chart, and you'd use the 1-minute
> or 5-minute chart.
>
> But, if you were to use the 15-minute chart, this is what you would do, right? You would have
> your stop loss at this high, just 'cuz it's nice and safe, very unlikely that price can come all
> the way up, hit your stop loss, and go back into the fair value gap, and then continue down,
> right? If it's bearish, it's not going to do that. If it does do this, it's because it's bullish,
> and you know what? It's a losing trade, but what you're going to do is you're going to target at
> least a two-to-one for this strategy, right? So, don't aim for like a one-to-one… and then
> you're just going to let price do its thing, right? Little retracement during London, and then
> let price do its thing, right? Right around when we've already hit take profit. So, typical day
> trading strategy — you set your limit order up, you get tagged in, you close the charts, and you
> let price do its thing, right?

### Video 2 — `twe4-7EdHVY` · "I Built a Trading Strategy With Just 3 Steps"

> Okay, so the first thing you need when you get on the charts and when you're trading any sort of
> ICT SMC strategy is you need to understand where price is likely to go, right? And to do that, we
> look for something called liquidity. So this is the first thing you do when you get on your
> charts. And what you're going to use is you're going to use either the 4-hour chart or the daily
> chart. And then you're just going to ask yourself: what high or low is price most likely to go
> for?
>
> Right? Is price more likely at this point right here when you get on the charts, is price more
> likely to come all the way up and take out this high here, or is price more likely to go for this
> low here? … Price is most likely to come and smash through this low here, right? And the reason
> why is because at key highs and key lows, there's something called liquidity, which is just
> stop-loss orders. … There's no respect of any order block, any fair value gap. Complete disrespect.
> Price is right next to these lows. And if you know anything about trading, price does not respect
> these lows. It either will take out these lows and go the other direction, or it will just smash
> right through.
>
> So what does that actually mean? It means right there we have our daily bias. We know that price is
> most likely to go for this low here. That's our daily bias. We are bearish. We don't need to do
> anything more for daily bias. … Within one minute, we've determined our daily bias. So, what that
> means is when we get onto the lower time frames or our entry time frame, we're only going to look
> for shorts. Because that is the highest probability setup that we can get.
>
> *[sponsor segment — AI liquidity toolkit, omitted]*
>
> Now, let's get back to the charts. Okay, so now that we've got our liquidity, we get down onto the
> lower time frame or our entry time frame, right? We don't need to get onto the 1-hour, 15-minute
> chart. We just look for whatever time frame we like to trade. And for me, I like the 5-minute. I
> like the 15-minute. But for this example, I'm going to show you it on the 5-minute chart.
>
> Now what we want to look for, now that we've got our direction, now that we've got our
> liquidity, is we want to look for a **liquidity sweep**. And what a liquidity sweep is, is 'cuz
> remember we are bearish — what we want to see is we want to see price come down, just wick above
> some old high, quickly reverse in the opposite direction, and then enter on some sort of fair
> value gap / order block, and then ride price down. Right?
>
> We want to see a stop run in the opposite direction of the daily bias, because that allows us to
> put our stop loss in the safest place possible.
>
> …So, if we just get on to here, right — can you see at this high here? At this high here, there
> are lots of stop-loss orders. This is early London session, key high, and we're on dollar yen. …
> Price is so, so close to this low, right? So, all we're looking for is a little bit of
> manipulation before we enter.
>
> And can you see at this point here? Can you see how price comes all the way up just enough to
> take out anybody who was short here, drops in the other direction, and then gives us our fair
> value gap, and then drops massively. And where's it going to keep dropping to? Straight to take
> profit, right? Straight to this liquidity pool right here.
>
> This right here, the moment price comes up and takes out this high here, that is our liquidity
> sweep. This is **not** a market structure shift. Because some people are going to say, the moment
> this candlestick closes above this high is a market structure shift. I'm telling you, after five
> years of trading, just ditch market structure, please. I have so many students who are so, so
> close to profitability, but they're obsessed with market structure and it just holds you back.
> The only thing you need is liquidity and liquidity sweeps, right, and a simple entry band. You do
> not need market structure.
>
> So, we have our liquidity sweep. … All we need to look for is liquidity on the higher time frames
> and then a liquidity sweep on our entry time frame. … **It has to be on your entry time frame**,
> right? That's the whole point. You know, we want to put our stop loss in the safest place.
>
> Now, in terms of entries — you can take an optimal trade entry, you can take an order block entry,
> you can get on the one-minute chart if you really want to. But for me, I'm just going to go for a
> fair value gap, 'cuz I think it's the easiest entry pattern that you can get. Right here is going
> to be your entry at this bearish fair value gap. **Your stop loss is going to be at this high right
> here.** And your take profit is going to be at least 2:1. But for me, if you know my style of
> trading, always 3:1. It's never more than a 3:1. It's just a fixed risk-to-reward ratio. **No
> break evens, no partials, no trailing stop loss.** Just simple, easy, consistent.
>
> And I'm going to quickly explain why we put our stop loss here. Because people always, I think the
> more times you can hear this, the better — people obsess, "oh, should I put my stop loss here,
> here, all the way up here?" You want to put it at this high here, because **the whole reason of
> waiting for a liquidity sweep is so we could put our stop loss in the safest place possible**.
> That's the only reason we wait for a liquidity sweep. … If you're not going to put your stop loss
> at this high, there's no point even waiting for a liquidity sweep, right? There's no point doing
> it. You may as well just have an aggressive entry, you know, on like the one-minute chart.
>
> … **Liquidity alone, getting a direction — that's not enough to make money. … A fair value gap
> isn't enough to make money. But when you combine liquidity, your direction, a fair value gap as
> your entry — but you wait for a liquidity sweep, that is what makes you profitable.** … I had the
> liquidity, I had the direction, I had the entry pattern, but I didn't wait for liquidity sweep,
> and I couldn't make money for four years, to be honest.
>
> *[outro — trading psychology video plug, omitted]*

### Video 3 — `-EEoeVVaO6M` · "Every Trader Should Know This 4H FVG Strategy"

> Okay, so for this strategy, all we need is a fair value gap. That's it. Now, we're going to look
> for the fair value gap on two time frames. The first time frame is going to be the 4-hour chart,
> which acts as our higher time frame. It tells us where the big banks, smart money — all these big
> institutions — are actually looking to move price. And then for our second fair value gap, we're
> going to look for it on our entry time frame. So the 5-minute, 15-minute, even the one-minute
> chart — really does not matter, as long as it's below the 4-hour chart.
>
> But what you're going to do is you're going to get the 4-hour chart up for whatever market you
> trade, because this model works on all markets, all pairs. And you're just going to mark out the
> first 4-hour fair value gap that you can see, right? The most recent 4-hour fair value gap that
> you can see in price action. And all you're going to do is mark out the fair value gap, which is
> the gap between this wick high and this wick low.
>
> And then all you're going to do at this point is just wait for price to return to the 4-hour fair
> value gap. That's it. That's all we need to use the higher time frames for. The moment this
> happens, we're going to get onto the lower time frames and we're going to start to look for our
> entries.
>
> Okay. So, now that we've marked out our 4-hour fair value gap over here, all we need to do now is
> we need to wait for price to return back to the 4-hour fair value gap, which it does right here.
> So, price slowly touches the 4-hour fair value gap and then — watch this — look how it instantly
> and aggressively shoots off in the opposite direction.
>
> That right there is what we refer to as **respect** of the fair value gap, right? Or, you know,
> this is essentially smart money showing you, or telling you, that price is bullish, and that it's
> respecting this fair value gap. **Confirmation that we're on the right side of the market**, which
> is why we actually wait till this 4-hour fair value gap.
>
> So all we need to do now is, now that price has shot off aggressively off this fair value gap, we
> just need to wait for a new bullish fair value gap to form. Right? A new fair value gap to form in
> the direction of this 4-hour fair value gap. **And the first bullish fair value gap that forms we
> want to enter on**, because the first bullish fair value gap that forms is essentially smart money
> screaming at you that price wants to go off in the opposite direction.
>
> So as you can see, price comes down, touches the 4-hour fair value gap, shoots off, gives us our
> retracement, and then shoots off again. … What we want to do is the moment price hits the 4-hour
> fair value gap, we want to wait for a new fair value gap to form, which we have right here. …
> And all we're going to do now is we're just going to wait for price to return to the fair value
> gap and enter. And just look at this — look how beautiful. How the wick comes all the way down,
> touches the fair value gap, and instantly shoots off.
>
> … So what that actually looks like — if we just remove this and we just mark this out, this is our
> 15-minute fair value gap, right? And this is going to be our entry.
>
> So, if you look at the PDF, you'll see many examples of this… what we're going to do is we're going
> to enter on the fair value gap, and then for our **stop loss**, you're going to put it **at a
> minimum at this low here**. Right? The reason we're going to put our stop loss at this wick's low
> is because this is the candlestick low that forms the fair value gap, right? This wick's low forms
> the fair value gap, right? **Not this wick, not this wick, not this wick. It's this wick's low that
> forms the fair value gap.** So, a minimum, your stop has to be there.
>
> Now, if you really want to be safe, you can put your stop there, but it's not really necessary,
> right? If we're bullish, which we are, price is not going to come all the way down and take out
> this low. So, put your stop here. Nice, easy, safe stop. **Do not put your stop right here. You do
> not need, especially for this model, a really tight stop loss.** All you need is just a stop loss
> at the low of the candlestick that forms the fair value gap.
>
> And then all we're going to do now is look for a take profit. Now, you can target a 2:1, but
> honestly, after five years of trading, bro, like just target 3:1. Make your life easier. So, I
> always target a minimum of a three risk-to-reward ratio, right? … This is more of a day trading
> strategy because we're on the 15-minute chart, but you can easily do this on the 1-minute or the
> 5-minute chart. You have actual logic and actual confirmation that you're on the right side of the
> market — which we have right here: price hits the 4-hour fair value gap and then shoots off,
> that's our confirmation that we're on the right side of the market — and the fact that it leaves
> another fair value gap is even more confirmation. And then all we need is a simple entry: enter on
> the fair value gap, stop at this low, and then ride price off to wherever you want to go.