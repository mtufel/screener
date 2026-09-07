## Purpose

Filters Strategy 2 extreme backtest trades to NY session hours (13:00–22:00 UTC) and weekdays only (Monday–Friday), enabling A/B testing of session-constrained vs unconstrained performance.

## ADDED Requirements

### Requirement: NY Session Filter for Backtest Trades
The backtest engine SHALL support a configurable NY session filter that excludes trades whose LTF FVG c3 close timestamp falls outside 13:00–22:00 UTC when enabled.

#### Scenario: Session filter disabled (default)
- **GIVEN** `EXTREME_SESSION_FILTER_ENABLED=false` or `--session-filter` CLI arg not passed
- **WHEN** a backtest runs
- **THEN** trades formed at any hour SHALL be included in the backtest results

#### Scenario: Session filter enabled with trade inside NY session
- **GIVEN** `EXTREME_SESSION_FILTER_ENABLED=true` or `--session-filter` CLI arg passed
- **WHEN** an LTF FVG's c3 close timestamp is at 15:30 UTC (within 13:00–22:00)
- **THEN** the trade setup SHALL be counted and included in backtest results

#### Scenario: Session filter enabled with trade outside NY session
- **GIVEN** `EXTREME_SESSION_FILTER_ENABLED=true` or `--session-filter` CLI arg passed
- **WHEN** an LTF FVG's c3 close timestamp is at 03:00 UTC (outside 13:00–22:00)
- **THEN** the trade setup SHALL be excluded from backtest results

#### Scenario: Session boundary inclusive at start, exclusive at end
- **GIVEN** session filter is enabled
- **WHEN** an LTF FVG's c3 close timestamp is exactly 13:00:00 UTC or exactly 22:00:00 UTC
- **THEN** the 13:00:00 UTC trade SHALL be included
- **AND** the 22:00:00 UTC trade SHALL be excluded

### Requirement: Weekday Filter for Backtest Trades
The backtest engine SHALL support a configurable weekday filter that excludes trades whose LTF FVG c3 close timestamp falls on Saturday or Sunday when enabled.

#### Scenario: Weekday filter disabled (default)
- **GIVEN** `EXTREME_WEEKDAY_FILTER_ENABLED=false` or `--weekday-filter` CLI arg not passed
- **WHEN** a backtest runs
- **THEN** trades formed on any day SHALL be included in the backtest results

#### Scenario: Weekday filter enabled with trade on weekday
- **GIVEN** `EXTREME_WEEKDAY_FILTER_ENABLED=true` or `--weekday-filter` CLI arg passed
- **WHEN** an LTF FVG's c3 close timestamp is on Monday, Tuesday, Wednesday, Thursday, or Friday (UTC)
- **THEN** the trade setup SHALL be counted and included in backtest results

#### Scenario: Weekday filter enabled with trade on weekend
- **GIVEN** `EXTREME_WEEKDAY_FILTER_ENABLED=true` or `--weekday-filter` CLI arg passed
- **WHEN** an LTF FVG's c3 close timestamp is on Saturday or Sunday (UTC)
- **THEN** the trade setup SHALL be excluded from backtest results

### Requirement: Filter Configuration Propagation
The session and weekday filter flags SHALL propagate through the `run_extreme_backtest` function signature and be reflected in backtest reports.

#### Scenario: Filter flags passed to backtest function
- **GIVEN** session and/or weekday filter is enabled via env var or CLI
- **WHEN** `run_extreme_backtest` is called
- **THEN** the function SHALL accept `session_filter` and `weekday_filter` boolean parameters
- **AND** the filter state SHALL be stored in `ExtremeBacktestReport`

#### Scenario: Backtest report shows active filters
- **GIVEN** session filter is enabled and weekday filter is disabled
- **WHEN** a backtest completes and prints the report
- **THEN** the report output SHALL indicate "Session Filter: NY (13:00–22:00 UTC)"
- **AND** the report output SHALL indicate "Weekday Filter: Disabled"

### Requirement: Both Filters Combine with AND Logic
When both session filter and weekday filter are enabled, both conditions MUST be satisfied for a trade to be included.

#### Scenario: Both filters enabled with trade passing both
- **GIVEN** both `session_filter=true` and `weekday_filter=true`
- **WHEN** an LTF FVG's c3 close is at 15:00 UTC on a Wednesday
- **THEN** the trade SHALL be included (inside session AND on weekday)

#### Scenario: Both filters enabled with trade failing one
- **GIVEN** both `session_filter=true` and `weekday_filter=true`
- **WHEN** an LTF FVG's c3 close is at 15:00 UTC on a Saturday
- **THEN** the trade SHALL be excluded (inside session BUT on weekend)

#### Scenario: Both filters enabled with trade failing both
- **GIVEN** both `session_filter=true` and `weekday_filter=true`
- **WHEN** an LTF FVG's c3 close is at 03:00 UTC on a Sunday
- **THEN** the trade SHALL be excluded (outside session AND on weekend)
