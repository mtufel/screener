# strategy-2-extreme spec delta: extreme-pending-touch-scoping-and-expiry

## MODIFIED Requirements

### Requirement: Immutable Active Trade Ledger & Stale Pending Invalidation
* **Single Active Position per Symbol**: While a trade is in `TRADE_ACTIVE`, entry price, stop loss, targets, and FVG anchor are strictly locked and immutable.
* **Alias Resolution**: Symbol aliases (e.g. `GOLD` $\rightarrow$ `PAXG`) MUST resolve to valid live mid-prices for floating $R$ and MFE tracking.
* **Stale Pending Invalidation**: `PENDING_RETRACE` setups that breach stop loss or break 4H anchor boundaries before entry fill MUST transition to `INVALIDATED` and archive to history — evaluated exclusively against candles formed at or after the setup's FVG (`timestamp >= formed_at`).
* **Candle Extreme TP/SL Resolution**: Closed candles formed strictly post-entry (`timestamp >= entry_timestamp`) evaluate `candle.high` and `candle.low` to trigger `COMPLETED_TP` (+2.0R) or `STOPPED_OUT` (-1.0R).
* **Pending Same-Bar Fill Completion**: A `PENDING_RETRACE` setup that fills entry and reaches its completion target within the same closed candle MUST transition to `COMPLETED_TP`, emit `TP_HIT`, and archive to history — even though the scanner classifies the FVG as completed and no longer emits the setup. The fill/TP evidence MUST come from candles formed at or after the FVG's formation timestamp.
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

#### Scenario: Backfill completion reports evidence-derived stats
* **GIVEN** a daemon that starts at 19:38 while a pending setup's target was reached at 19:10 by historical candles
* **WHEN** the first monitor cycle resolves the trade as `COMPLETED_TP` from those candles
* **THEN** the archived record SHALL report the first post-fill candle touching entry as the fill time and the first candle reaching the target as the close time
* **AND** `duration_min` SHALL reflect the candle distance between fill and close, and `mfe_r` SHALL reflect the true post-fill extreme (not `0.0` with `duration_min: 1`).

---

## ADDED Requirements

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