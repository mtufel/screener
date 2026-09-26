## Purpose

Defines the configurable trade-selection bias filters that improve the quality of
Strategy-2 Extreme LTF FVG setups: distance-from-4H-zone confluence, momentum impulse,
gap-size ceiling, and LTF-FVG age ceiling. These filters are shared by the live screener,
trade ledger monitor, and historical backtester.

## ADDED Requirements

### Requirement: Distance-from-4H-Zone Confluence Filter
The system SHALL reject a candidate LTF FVG whose price location is more than a configurable
maximum percentage away from the nearest boundary of the selected 4H anchor zone, so only
setups with 4H-imbalance confluence are kept.

* **Bullish**: reject when `(ltf_fvg.bottom - anchor.bottom) / anchor.bottom * 100 > max_dist_pct`.
* **Bearish**: reject when `(anchor.top - ltf_fvg.top) / anchor.top * 100 > max_dist_pct`.
* `max_dist_pct` is configurable; `<=0` or `ALL` disables the filter.

#### Scenario: Close-to-zone bullish FVG retained
- **WHEN** a Bullish LTF FVG bottom lies 1% above the Bullish 4H anchor bottom and `max_dist_pct = 2.0`
- **THEN** the LTF FVG SHALL remain a candidate for selection.

#### Scenario: Far-from-zone bullish FVG rejected
- **WHEN** a Bullish LTF FVG bottom lies 3% above the Bullish 4H anchor bottom and `max_dist_pct = 2.0`
- **THEN** the LTF FVG SHALL be excluded from the candidate pool.

### Requirement: Momentum Impulse-Candle Filter
The system SHALL reject an LTF FVG candidate when its middle (impulse) candle c2 is not a strong
directional body — defined as `|c2.close - c2.open| / max(c2.high - c2.low, epsilon) >= min_momentum_body_ratio`
AND the body direction matches the FVG direction (positive body for Bullish, negative for Bearish).
The filter SHALL be configurable and disabled by default.

#### Scenario: Strong directional impulse retained
- **WHEN** a Bullish LTF FVG's impulse candle has a filled body spanning 70% of its range with a higher close
  and `min_momentum_body_ratio = 0.50`
- **THEN** the LTF FVG SHALL remain a candidate.

#### Scenario: Weak or counter-directional impulse rejected
- **WHEN** a Bullish LTF FVG's impulse candle has a body spanning 20% of its range (or closes lower)
  and `min_momentum_body_ratio = 0.50`
- **THEN** the LTF FVG SHALL be excluded from the candidate pool.

### Requirement: LTF FVG Gap-Size Ceiling
The system SHALL reject a candidate LTF FVG whose `gap_pct` exceeds a configurable maximum
(`EXTREME_MAX_GAP_PCT`), while continuing to enforce the existing minimum gap floor. A ceiling
value `<=0` or `ALL` disables the ceiling.

#### Scenario: Oversized gap rejected
- **WHEN** an LTF FVG has `gap_pct = 0.45` and `EXTREME_MAX_GAP_PCT = 0.30`
- **THEN** the LTF FVG SHALL be excluded, even though it meets the 0.05% minimum.

#### Scenario: Ceiling disabled retains smaller gaps
- **WHEN** `EXTREME_MAX_GAP_PCT` is `ALL` and an LTF FVG has `gap_pct = 0.45` above the minimum
- **THEN** the LTF FVG SHALL remain a candidate.

### Requirement: LTF FVG Age Ceiling
The system SHALL reject a candidate LTF FVG that forms more than a configurable number of LTF
candles after the 4H anchor's first touch (`EXTREME_MAX_LTF_FVG_AGE_CANDLES`), removing
decayed imbalances. An age ceiling `<=0` or `ALL` disables the filter.

* Age is measured as the number of closed LTF candles between the 4H `first_touch_timestamp`
  and the LTF FVG Candle-3 close.

#### Scenario: Moderately fresh FVG retained
- **WHEN** an LTF FVG forms 15 candles after the 4H first touch and `EXTREME_MAX_LTF_FVG_AGE_CANDLES = 40`
- **THEN** the LTF FVG SHALL remain a candidate.

#### Scenario: Stale FVG rejected
- **WHEN** an LTF FVG forms 60 candles after the 4H first touch and `EXTREME_MAX_LTF_FVG_AGE_CANDLES = 40`
- **THEN** the LTF FVG SHALL be excluded from the candidate pool.

### Requirement: Runtime Configuration & Dashboard Sync for Biases
All bias-filter parameters SHALL be runtime-configurable, reflected in the server status/config
endpoints, and synchronized to the Web dashboard alongside existing Extreme parameters, sharing
the same env → state → daemon → backtest resolution path.

#### Scenario: Bias config reflected in status endpoint
- **WHEN** the runtime is configured with `EXTREME_MAX_DIST_FROM_4H_PCT=2.0`, `EXTREME_REQUIRE_MOMENTUM=true`,
  `EXTREME_MAX_GAP_PCT=0.30`, and `EXTREME_MAX_LTF_FVG_AGE_CANDLES=40`
- **THEN** the Extreme status/config endpoints SHALL report those values and the live daemon and
  backtester SHALL apply them consistently.
