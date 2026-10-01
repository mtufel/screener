## MODIFIED Requirements

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

## ADDED Requirements

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
