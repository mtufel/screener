# Strategy 2: ⚡ Extreme LTF FVG Strategy Specification

## Purpose
Specifies the requirements and architecture for Strategy 2, an extreme precision day-trading system executing on Lower Timeframe (15m/5m) Fair Value Gaps formed strictly post-4H-touch, with an Immutable Active Trade Ledger.

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
The system SHALL scan closed LTF candles whose Candle 3 closed at or after `first_touch_timestamp`, enforcing a minimum gap size threshold ($\text{Gap \%} \ge 0.05\%$).

#### Scenario: Filter out pre-touch and sub-tick gaps
- **WHEN** scanning candidate LTF FVGs
- **THEN** any FVG formed prior to `first_touch_timestamp` SHALL be discarded
- **AND** any FVG with gap width $< 0.05\%$ SHALL be excluded.

---

### Requirement: #1 Extreme FVG Ranking & Selection
The system MUST select the single deepest unmitigated LTF FVG closest to the 4H anchor: the lowest price bottom for Bullish setups, and highest price top for Bearish setups.

#### Scenario: Long extreme selection
- **GIVEN** multiple valid Bullish LTF FVGs formed post-touch
- **WHEN** evaluating the extreme ranking
- **THEN** the system SHALL select the FVG with the minimum bottom price ($\arg\min \text{bottom}$).

---

### Requirement: Execution Parameters & Target Matrix
The system SHALL place the entry price at the outer FVG boundary, stop loss at the extreme 3-candle wick $\min(c_1.l, c_2.l, c_3.l)$ for Bullish and $\max(c_1.h, c_2.h, c_3.h)$ for Bearish, and calculate 1R, 2R (Primary $\star$), and 3R targets.

#### Scenario: Bullish parameter calculation
- **WHEN** a Bullish LTF FVG is selected with top at $60,000 and 3-candle lowest wick at $59,000
- **THEN** the entry price SHALL be $60,000, stop loss SHALL be $59,000 (Risk: $1,000)
- **AND** targets SHALL be set at $61,000 (1R), $62,000 (2R), and $63,000 (3R).

---

### Requirement: Immutable Active Trade Ledger & Stale Pending Invalidation
* **Single Active Position per Symbol**: While a trade is in `TRADE_ACTIVE`, entry price, stop loss, targets, and FVG anchor are strictly locked and immutable.
* **Alias Resolution**: Symbol aliases (e.g. `GOLD` $\rightarrow$ `PAXG`) MUST resolve to valid live mid-prices for floating $R$ and MFE tracking.
* **Stale Pending Invalidation**: `PENDING_RETRACE` setups that breach stop loss or break 4H anchor boundaries before entry fill MUST transition to `INVALIDATED` and archive to history.
* **Candle Extreme TP/SL Resolution**: Closed candles formed strictly post-entry (`timestamp >= entry_timestamp`) evaluate `candle.high` and `candle.low` to trigger `COMPLETED_TP` (+2.0R) or `STOPPED_OUT` (-1.0R).

#### Scenario: Pending setup breached before fill
* **GIVEN** a setup is registered in `PENDING_RETRACE` with entry at $60,000 and stop loss at $59,000
* **WHEN** live market price drops to $58,500 without touching $60,000
* **THEN** the setup transitions to `INVALIDATED`, emits `SETUP_INVALIDATED`, and moves to history.

#### Scenario: Alias symbol mid-price resolution
* **GIVEN** an active trade on symbol `GOLD` with entry at $2,500
* **WHEN** Hyperliquid mid-prices return `{"PAXG": 2530.0}`
* **THEN** the trade tracker resolves `GOLD` via `PAXG`, updating floating $R$ to `+1.5R` instead of remaining frozen at `0.00R`.
