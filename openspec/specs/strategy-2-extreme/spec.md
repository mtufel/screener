# Strategy 2: ⚡ Extreme LTF FVG Strategy Specification

## Purpose
Specifies the requirements and architecture for Strategy 2, an extreme precision day-trading system executing on Lower Timeframe (1m/5m/15m/1h) Fair Value Gaps formed strictly post-4H-touch, with an Immutable Active Trade Ledger.

---

## Requirements

### Requirement: Incremental 4H FVG Cache
The system SHALL maintain an in-memory, incremental 4H FVG cache per `{symbol}:{mode}` with $O(1)$ delta updates, automatically invalidating breached zones and detecting newly formed closed 4H FVGs without full historical rescanning.

#### Scenario: Delta update on new closed 4H bar
- **WHEN** a new 4H candle closes
- **THEN** the cache SHALL evaluate rolling 3-candle imbalance and add any newly formed FVG
- **AND** invalidates any existing 4H FVG breached by the new bar.

---

### Requirement: 4H Anchor Selection & First Touch Pinpointing
The system MUST prioritize 4H FVG zones currently containing live price or the most recently touched 4H zone, and MUST pinpoint the exact timestamp (`first_touch_timestamp`) when price first touched the 4H zone post-close.

#### Scenario: Priority given to currently containing price
- **GIVEN** multiple active 4H FVG zones exist
- **WHEN** live market price is inside an active 4H FVG zone
- **THEN** the system SHALL select that zone as the primary 4H anchor
- **AND** record its exact first touch timestamp.

---

### Requirement: Post-Touch LTF FVG Discovery & Minimum Gap Filter
The system SHALL scan closed LTF candles whose Candle 3 closed at or after `first_touch_timestamp`,
enforcing a minimum gap size threshold (`EXTREME_MIN_GAP_PCT`, default 0.05%) and defaulting to
**close-based 4H invalidation**. Formation-session filtering is optional and governed by
`EXTREME_SESSION_FILTER_ENABLED` / `EXTREME_SESSIONS`, which **default to disabled / `ALL`**
(all-hours trading is the out-of-the-box behavior). The system SHALL additionally apply the
configurable bias filters (distance-from-4H confluence, momentum impulse, gap ceiling, and
LTF-FVG age ceiling) defined in the `strategy-biases` capability while discovering and ranking
candidates.

#### Scenario: Filter out pre-touch and sub-tick gaps
- **WHEN** scanning candidate LTF FVGs
- **THEN** any FVG formed prior to `first_touch_timestamp` SHALL be discarded
- **AND** any FVG with gap width `< EXTREME_MIN_GAP_PCT` SHALL be excluded.

#### Scenario: Close invalidation is the default
- **WHEN** the engine boots without an explicit invalidation override
- **THEN** 4H FVGs SHALL be evaluated with close-based invalidation (a bullish FVG remains valid
  until a subsequent candle closes below its bottom; a bearish FVG until a subsequent candle
  closes above its top).

#### Scenario: Session filter is off by default (all-hours)
- **WHEN** the engine boots without an explicit session override
- **THEN** `EXTREME_SESSION_FILTER_ENABLED` SHALL default to disabled and `EXTREME_SESSIONS` to
  `ALL`, so LTF FVGs are accepted in any session unless the operator opts into session gating.
  (Note: when a session string like `NY` is configured, the effective filter resolves to that
  session; keeping the string `ALL` is required to retain all-hours behavior.)

#### Scenario: Bias filters enrich the discovery pass
- **WHEN** scanning and ranking post-touch LTF FVGs with one or more bias filters enabled
- **THEN** any candidate rejected by a distance, momentum, gap-ceiling, or age-ceiling filter
  SHALL NOT be selected as the extreme FVG or executed as a setup.

#### Scenario: Extreme ranking unchanged in direction
- **GIVEN** multiple valid, bias-passing Bullish LTF FVGs formed post-touch
- **WHEN** evaluating the extreme ranking
- **THEN** the system SHALL still select the Bullish FVG with the minimum bottom price
  (`arg min bottom`) and the Bearish FVG with the maximum top price (`arg max top`).

### Requirement: #1 Extreme FVG Ranking & Selection
The system MUST select the single deepest unmitigated LTF FVG closest to the 4H anchor: the lowest price bottom for Bullish setups, and highest price top for Bearish setups.

#### Scenario: Long extreme selection
- **GIVEN** multiple valid Bullish LTF FVGs formed post-touch
- **WHEN** evaluating the extreme ranking
- **THEN** the system SHALL select the FVG with the minimum bottom price ($\arg\min \text{bottom}$).

---

### Requirement: Execution Parameters & Target Matrix
The system SHALL place the entry price at the outer FVG boundary, stop loss at the extreme 3-candle wick $\min(c_1.l, c_2.l, c_3.l)$ for Bullish and $\max(c_1.h, c_2.h, c_3.h)$ for Bearish, and calculate 1R, 2R (Primary $\star$), and 3R targets.

* **Backtest Fill-Candle Evaluation**: The backtest forward-simulation MUST include the entry/fill candle itself in exit resolution, evaluating the fill candle's high/low against TP and stop levels exactly as the live ledger does (candles where `timestamp >= entry_timestamp`).
* **Backtest Stop Loss Precedence**: On any candle in forward-simulation where both Stop Loss and Take Profit levels are reached, Stop Loss MUST be evaluated first and take precedence (`STOPPED_OUT`, -1.0R), guaranteeing conservative risk accounting without sub-bar tick data.

#### Scenario: Bullish parameter calculation
- **WHEN** a Bullish LTF FVG is selected with top at $60,000 and 3-candle lowest wick at $59,000
- **THEN** the entry price SHALL be $60,000, stop loss SHALL be $59,000 (Risk: $1,000)
- **AND** targets SHALL be set at $61,000 (1R), $62,000 (2R), and $63,000 (3R).

#### Scenario: Backtest same-bar fill and TP resolution
* **GIVEN** a Bullish LTF FVG with entry at $100 and 2R target at $120
* **WHEN** the candle that first touches $100 (fill candle) also reaches a high of $125
* **THEN** the backtest SHALL resolve the trade as a 2R win (`hit_2r = True`), not skip the fill candle and defer resolution to subsequent candles.

#### Scenario: Backtest same-bar fill and SL resolution
* **GIVEN** a Bullish LTF FVG with entry at $100 and stop at $90
* **WHEN** the fill candle's low breaches $90
* **THEN** the backtest SHALL resolve the trade as a loss (`STOPPED_OUT`) using the fill candle's extreme.

#### Scenario: Backtest same-bar SL and TP collision precedence
* **GIVEN** an active trade entered at $100 with SL at $90 and 3R TP at $130
* **WHEN** a simulation candle has low <= $90 AND high >= $130
* **THEN** the trade MUST resolve as `STOPPED_OUT` with `hit_1r = hit_2r = hit_3r = False` and realized R equal to `-1.0R` for all policy targets.

---

### Requirement: Immutable Active Trade Ledger & Stale Pending Invalidation
* **Single Active Position per Symbol**: While a trade is in `TRADE_ACTIVE`, entry price, stop loss, targets, and FVG anchor are strictly locked and immutable.
* **Alias Resolution**: Symbol aliases (e.g. `GOLD` $\rightarrow$ `PAXG`) MUST resolve to valid live mid-prices for floating $R$ and MFE tracking.
* **Stale Pending Invalidation**: `PENDING_RETRACE` setups that breach stop loss or break 4H anchor boundaries before entry fill MUST transition to `INVALIDATED` and archive to history — evaluated exclusively against candles formed at or after the setup's FVG (`timestamp >= formed_at`).
* **Candle Extreme TP/SL Resolution**: Closed candles formed strictly post-entry (`timestamp >= entry_timestamp`) evaluate `candle.high` and `candle.low` to trigger `COMPLETED_TP` (+2.0R) or `STOPPED_OUT` (-1.0R). All candidate candles MUST be evaluated in strict chronological ascending timestamp order.
* **Stop Loss Precedence**: On any candle or batch where both Stop Loss and Take Profit levels are crossed, Stop Loss MUST take precedence and resolve the trade as `STOPPED_OUT` (-1.0R). An earlier stop-out MUST NEVER be superseded by a subsequent price touch at the target.
* **Pending Same-Bar Fill Completion**: A `PENDING_RETRACE` setup that fills entry and reaches its completion target within the same closed candle MUST transition to `COMPLETED_TP`, emit `TP_HIT`, and archive to history — provided Stop Loss was not touched first. The fill/TP evidence MUST come from candles formed at or after the FVG's formation timestamp.
* **Honest Backfill Resolution**: Catch-up resolutions performed from historical candle evidence (e.g. the first monitor cycle after daemon start) MUST derive entry time, exit time, duration, and maximum favorable excursion from the actual candle extremes rather than stamping cycle time or zeroed stats.

#### Scenario: Pending setup breached before fill
* **GIVEN** a setup is registered in `PENDING_RETRACE` with entry at $60,000 and stop loss at $59,000
* **WHEN** live market price drops to $58,500 without touching $60,000
* **THEN** the setup transitions to `INVALIDATED`, emits `SETUP_INVALIDATED`, and moves to history.

#### Scenario: Alias symbol mid-price resolution
* **GIVEN** an active trade on symbol `GOLD` with entry at $2,500
* **WHEN** Hyperliquid mid-prices return `{"PAXG": 2530.0}`
* **THEN** the trade tracker resolves `GOLD` via `PAXG`, updating floating $R$ to `+1.5R` instead of remaining frozen at `0.00R`.

#### Scenario: Pending setup fills and completes in one candle
* **GIVEN** a `PENDING_RETRACE` trade on BTC with entry at $100.25, stop at $100.05, and 2R target at $100.65
* **WHEN** the next scanner cycle receives no setup for BTC (FVG already classified `COMPLETED`) but recent candles formed after the FVG include a single closed candle with `low <= 100.25` and `high >= 100.65`
* **THEN** the pending trade transitions to `COMPLETED_TP` with `realized_r = +2.0`, emits `TP_HIT`, and moves to history.

#### Scenario: Pre-formation candles never complete a pending setup
* **GIVEN** a Bullish `PENDING_RETRACE` setup with entry at $4,397.80 and 2R target at $4,424.45 whose FVG formed at 18:45
* **WHEN** the monitor window contains candles from before 18:45 whose lows cross the entry and later candles whose highs reach the target, but no candle formed at or after 18:45 touches the entry
* **THEN** the pending setup SHALL NOT transition to `COMPLETED_TP` and SHALL NOT emit `TP_HIT`
* **AND** the record remains `PENDING_RETRACE` until real post-formation evidence or expiry resolves it.

#### Scenario: Pre-formation extremes never invalidate a pending setup
* **GIVEN** a `PENDING_RETRACE` setup whose 4H anchor bottom is $97.23 and stop loss is $98.22
* **WHEN** the monitor window contains pre-formation candles whose extremes breach the stop or anchor, but every candle formed at or after the FVG respects both boundaries
* **THEN** the pending setup SHALL NOT transition to `INVALIDATED` while the scanner still offers the setup.

#### Scenario: Earlier SL hit is never overridden by later TP touch
* **GIVEN** a Bearish active trade entered at $4,466.30 with SL at $4,476.70 and 2R TP at $4,445.50
* **WHEN** Candle 1 spikes up to $4,476.90 (crossing SL) and Candle 50 drops to $4,380.00 (crossing TP)
* **THEN** the trade MUST resolve as `STOPPED_OUT` with `realized_r = -1.0` on Candle 1
* **AND** the trade SHALL NOT be recorded as `COMPLETED_TP` or awarded positive $R$.

#### Scenario: Backfill completion reports evidence-derived stats
* **GIVEN** a daemon that starts at 19:38 while a pending setup's target was reached at 19:10 by historical candles
* **WHEN** the first monitor cycle resolves the trade as `COMPLETED_TP` from those candles
* **THEN** the archived record SHALL report the first post-fill candle touching entry as the fill time and the first candle reaching the target as the close time
* **AND** `duration_min` SHALL reflect the candle distance between fill and close, and `mfe_r` SHALL reflect the true post-fill extreme (not `0.0` with `duration_min: 1`).

---

### Requirement: Pending Monitor Candle Scoping
All `PENDING_RETRACE` monitor evaluations — same-bar fill/TP detection and stop-loss/anchor breach detection — MUST consume only closed candles whose open timestamp is at or after the tracked setup's LTF FVG `formed_at` timestamp, mirroring the `timestamp >= entry_timestamp` scoping applied to `TRADE_ACTIVE` exit resolution. The rolling market snapshot window (`recent_candles_map`) MAY span pre-formation candles, but they MUST NOT contribute to any pending-monitor predicate.

#### Scenario: Scoping mirrors active-trade semantics
* **WHEN** the pending monitor and the active-trade monitor evaluate the same symbol in the same cycle
* **THEN** both SHALL restrict candle evidence to timestamps at or after their respective floor (`formed_at` for pending, `entry_timestamp` for active).

### Requirement: Stale Pending Expiry & Anchor Refresh
* **Absent-Setup Expiry**: A `PENDING_RETRACE` record whose scanner setup is absent for N consecutive scan cycles (default N = 40 at a 30 s cadence) MUST transition to `INVALIDATED` with an expiry-specific `status_detail`, emit `SETUP_INVALIDATED`, and archive to history. The absence counter MUST advance only on cycles that actually executed a scan, so daemon downtime does not expire records on the first post-restart cycle.
* **Fresh Emission Refresh**: When the scanner emits a newer setup for a symbol whose existing record is `PENDING_RETRACE` (different `trade_id` due to a newer FVG `formed_at`), the tracker MUST refresh the pending record's entry price, stop loss, targets, risk, completion target, 4H anchor, LTF FVG, timeframe, and creation timestamp to the newest emission. `TRADE_ACTIVE` records MUST NOT be refreshed or mutated by scanner emissions.

#### Scenario: Quiet anchor eventually expires
* **GIVEN** a `PENDING_RETRACE` record whose 4H anchor has not re-touched and whose FVG the scanner no longer emits
* **WHEN** 40 consecutive scan cycles pass without the setup being offered
* **THEN** the record transitions to `INVALIDATED` with `status_detail` indicating setup absence, emits `SETUP_INVALIDATED`, and moves to history
* **AND** the symbol becomes eligible for fresh setup registration on the next emission.

#### Scenario: Scanner offers a fresher setup for a stale pending record
* **GIVEN** a pending record pairing a 03-Sep LTF FVG with a 19-Aug 4H anchor zone `[1905–1910]` while the current scanner emits a fresh FVG with a 04-Sep anchor containing live price
* **WHEN** the newer emission arrives for the same symbol
* **THEN** the pending record SHALL adopt the newer entry, stop, targets, anchor, and FVG metadata in place
* **AND** subsequent invalidation geometry and alert charts SHALL reference the fresh anchor zone, not the stale 19-Aug zone.

#### Scenario: Daemon restart does not mass-expire pending records
* **GIVEN** the daemon was stopped for 3 hours with 4 pending records in the ledger
* **WHEN** the daemon restarts and executes its first scan cycle
* **THEN** no pending record SHALL be expired due to the downtime window
* **AND** absence counting resumes only from the first executed cycle onward.

---

### Requirement: Standardized Candle Close / Ending Timestamps
All candle-derived lifecycle milestone timestamps (`fvg_formation_time_ist`, `entry_filled_at_ist`, `closed_at_ist` / `exit_time_ist`) MUST consistently report the **Candle Close / Ending Time** (`timestamp + duration_ms`) across live tracking, backtesting, charts, Telegram alerts, and dashboard tables.

#### Scenario: Differentiate formation from fill on first bar
* **GIVEN** a 5m FVG forms at Candle 3 close `06:10 PM IST`
* **WHEN** Candle 4 (open `06:10 PM`, close `06:15 PM`) retraces and fills entry
* **THEN** FVG formation timestamp SHALL be reported as `06:10 PM IST`
* **AND** entry fill timestamp SHALL be reported as `06:15 PM IST` (candle ending time), preventing confusing duplicate timestamps.

---

### Requirement: Universal FVG Formation Display
The system MUST prominently display the FVG formation timestamp across all operational surfaces.

#### Scenario: Formation time rendered on every surface
- **WHEN** an FVG qualifies as a setup
- **THEN** its formation timestamp SHALL be displayed on the TradingView Chart Generator as an amber bounding-box badge (`FVG Formed: <time>`) and in the chart subtitle
- **AND** Telegram Bot Alerts SHALL include explicit `4H Anchor Formed` and `LTF FVG Formed` metadata rows on `NEW_SETUP`, `ENTRY_FILLED`, `TP_HIT`, and `SL_HIT`
- **AND** the Web Dashboard SHALL show a dedicated `Formed: <time>` line in Live Setups, Tracked Trades Log, and Backtest results tables.

---

### Requirement: Gentle Async Rate Limiter & Inter-Request Pacing
The Hyperliquid market data client MUST enforce token-bucket rate limiting (`RATE_LIMIT_RPS=3.0`, `MAX_CONCURRENT_REQUESTS=3`) with minimum inter-request pacing (`min_interval=0.25s`) and reuse recently fetched candle caches across background daemons and on-demand chart generators to prevent rate limit spikes and HTTP 429 penalties.

#### Scenario: Paced, cached requests avoid 429 penalties
- **WHEN** background daemons and on-demand chart generators issue Hyperliquid market-data requests concurrently
- **THEN** the client SHALL enforce token-bucket rate limiting (`RATE_LIMIT_RPS=3.0`, `MAX_CONCURRENT_REQUESTS=3`) and minimum inter-request pacing (`min_interval=0.25s`)
- **AND** it SHALL reuse recently fetched candle caches across daemons and chart generators to prevent rate-limit spikes and HTTP 429 penalties.

---

### Requirement: Multi-Timeframe LTF Support (1m, 5m, 15m, 1h)
The system MUST support full-pipeline execution across 1m, 5m, 15m, and 1h lower timeframes across real-time scanning, background daemon monitoring, historical backtesting, chart visualization, and UI selection controls.

#### Scenario: 1h timeframe execution and serialization
* **WHEN** a 1h LTF timeframe is configured in `.env` (`EXTREME_LTF_TIMEFRAME=1h`) or via runtime API
* **THEN** candidate LTF FVGs SHALL be evaluated on 1h bars with 1h candle gap offsets applied to timestamp calculations
* **AND** historical backtests SHALL include `ltf_timeframe` across all serialized trade results and metrics.

---

### Requirement: Runtime Configuration & Dashboard UI Synchronization
The application MUST dynamically synchronize runtime configuration parameters (`coins_whitelist`, `ltf_timeframe`, `use_close_invalidation`, `completion_target`, `min_gap_pct`, `interval_seconds`) between environment defaults, background daemons, API status endpoints (`/api/extreme/status`, `/api/extreme/config`), and frontend dashboard controls on initial load and polling cycles.

#### Scenario: Automatic UI initialization from environment configuration
* **GIVEN** custom configuration in `.env` (e.g. `EXTREME_LTF_TIMEFRAME=1h` and `EXTREME_USE_CLOSE_INVALIDATION=true`)
* **WHEN** a user loads the Web Dashboard
* **THEN** the UI dropdowns, toggles, and backtest selector defaults SHALL automatically reflect the active server configuration without manual user intervention.

### Requirement: Backtest Coverage of Bias Filters
The historical backtester SHALL compute the same bias-filter parameters (distance, momentum,
gap ceiling, age ceiling, session, invalidation) as the live engine and report per-configuration
metrics, so filtered-vs-baseline performance is directly comparable.

#### Scenario: Backtest honors bias config
- **WHEN** a backtest is configured with `EXTREME_MAX_DIST_FROM_4H_PCT=2.0`,
  `EXTREME_REQUIRE_MOMENTUM=true`, `EXTREME_MAX_GAP_PCT=0.30`, and
  `EXTREME_MAX_LTF_FVG_AGE_CANDLES=40`
- **THEN** the backtest SHALL apply the same candidate rejections as the live engine and report
  trades, win rate, net R, profit factor, and drawdown for that configuration.
