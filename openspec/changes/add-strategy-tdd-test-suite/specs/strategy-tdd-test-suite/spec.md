## Purpose

Defines the repository-wide offline pytest suite that verifies documented strategy behavior (FVG math, entry/exit detection, TP/SL resolution, formation-candle exclusion, timestamp correctness, extreme-anchor ranking, ledger tracking, chart rendering, Telegram reporting/anti-spam). Every requirement-scenario here maps to concrete test cases implemented under this change.

## ADDED Requirements

### Requirement: FVG math correctness
The suite SHALL verify that a Fair Value Gap zone is exactly `[c1.high, c3.low]` for bullish and `[c3.high, c1.low]` for bearish three-candle patterns, that equal-boundary candles (`c3.low == c1.high` bullish, `c3.high == c1.low` bearish) produce no gap, that gaps narrower than 0.05% of the zone midpoint are excluded, and that formation time equals the third candle's close time.

#### Scenario: Bullish zone boundaries
- **WHEN** three ascending candles form a bullish FVG
- **THEN** the zone top is `c3.low`, bottom is `c1.high`, and `gap_pct` matches width over midpoint

#### Scenario: Equality edge produces no gap
- **WHEN** `c3.low` equals `c1.high` (bullish) or `c3.high` equals `c1.low` (bearish)
- **THEN** no FVG is discovered (strict inequality)

#### Scenario: Minimum-gap boundary
- **WHEN** the zone width is just below 0.05% of the midpoint, and separately just above it
- **THEN** the narrow gap is rejected and the wider gap is discovered

#### Scenario: Formation time is third-candle close
- **WHEN** an FVG forms from a 4H candle opened at time T
- **THEN** formation time equals T plus the 4H candle duration (the close), rendered in IST

### Requirement: Post-first-touch discovery without lookahead
The suite SHALL verify LTF FVG discovery only considers candles after the anchor's first touch, excludes FVGs whose formation precedes the touch (formation exactly at the touch timestamp remains qualified), rejects zones outside the anchor, and never treats the formation candle as its own entry touch.

#### Scenario: No discovery before first touch
- **WHEN** a qualifying FVG forms before the anchor's first-touch timestamp
- **THEN** it is not discovered from that touch

#### Scenario: Formation candle cannot be its own entry
- **WHEN** no candle after formation touches the zone
- **THEN** the setup is `PENDING_RETRACE` with no entry timestamp and entry price at the zone's outer boundary

#### Scenario: Entry on first post-formation touch
- **WHEN** a candle after formation first touches the zone's outer boundary
- **THEN** the setup is `TRADE_ACTIVE` with entry timestamp equal to that candle's open time

#### Scenario: Pre-entry stop breach discards candidate
- **WHEN** a post-formation candle breaches the derived stop loss before touching entry
- **THEN** that FVG is discarded (a later qualifying FVG may still be discovered)
