# Strategy 1: 2-Stage Standard Multi-Timeframe FVG Specification

## Purpose
Specifies the requirements and architecture for Strategy 1, a multi-timeframe Fair Value Gap screener evaluating macro 4H structure confluence with micro (5m/15m) retrace entry setups.

---

## Requirements

### Requirement: Fair Value Gap Detection
A rolling 3-candle sequence `[c1, c2, c3]` SHALL qualify as a Fair Value Gap when `c3.low > c1.high` (Bullish) or `c3.high < c1.low` (Bearish), evaluated strictly on closed bars.

#### Scenario: Bullish FVG validation
- **WHEN** candle 3 low exceeds candle 1 high on a closed bar
- **THEN** the system SHALL construct a Bullish FVG spanning `[c1.high, c3.low]`.

---

### Requirement: 4H Higher Timeframe Anchoring
The system SHALL evaluate 4H candles to detect active, non-invalidated 4H FVGs across `ANY_VALID`, `RECENT_FORMED`, and `TOUCH_WINDOW` modes with wick-based or close-based invalidation rules.

#### Scenario: 4H zone containment
- **GIVEN** an active 4H FVG zone
- **WHEN** current market price is within the 4H zone boundaries
- **THEN** the coin SHALL pass Phase 1 screening.

---

### Requirement: LTF Micro Confirmation & Scoring
The system SHALL scan the LTF (5m/15m) for a matching FVG in the same direction and calculate a composite score combining 4H tightness, LTF tightness, and 4H center proximity.

#### Scenario: Setup scoring and ranking
- **WHEN** both 4H and LTF FVGs are confirmed in the same direction
- **THEN** the system SHALL compute composite score $\text{Score} = 0.35 \times \text{Tightness}_{\text{4H}} + 0.35 \times \text{Tightness}_{\text{LTF}} + 0.30 \times \text{CenterProximity}$.

---

### Requirement: Strategy 1 Trade Tracker Lifecycle
The system SHALL track trade lifecycle through `PENDING_RETRACE`, `TRADE_ACTIVE`, `COMPLETED_TP`, and `STOPPED_OUT` states, broadcasting alerts with chart attachments.

#### Scenario: Trade target completion
- **GIVEN** an active Strategy 1 trade position
- **WHEN** market price hits the 2R target level
- **THEN** the tracker SHALL transition the trade to `COMPLETED_TP` and emit a Telegram alert.
