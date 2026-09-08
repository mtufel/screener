# Specification: Session & Weekday Filter Engine (Live & Backtest)

## Purpose
Enforces independent NY Session (13:00–22:00 UTC) and Weekday (Mon–Fri UTC) constraints on FVG formation and Entry fill across both real-time live scanning and historical backtesting.

## Requirements

### Requirement: Unified Time Filter Helper Functions
The system SHALL provide deterministic UTC-based helper functions `is_in_ny_session(timestamp_ms)` and `is_weekday(timestamp_ms)`.

#### Scenario: NY Session Evaluation
- **GIVEN** a UTC timestamp in milliseconds
- **WHEN** the UTC hour is between 13 and 21 inclusive (13:00:00 to 21:59:59.999 UTC)
- **THEN** `is_in_ny_session(timestamp_ms)` SHALL return `True`
- **WHEN** the UTC hour is outside 13 to 21 (e.g. 08:30 or 22:00 UTC)
- **THEN** `is_in_ny_session(timestamp_ms)` SHALL return `False`

#### Scenario: Weekday Evaluation
- **GIVEN** a UTC timestamp in milliseconds
- **WHEN** the day of the week is Monday through Friday (UTC)
- **THEN** `is_weekday(timestamp_ms)` SHALL return `True`
- **WHEN** the day of the week is Saturday or Sunday (UTC)
- **THEN** `is_weekday(timestamp_ms)` SHALL return `False`

---

### Requirement: FVG Formation Session & Weekday Filtering
The live scanner and backtest engines SHALL support filtering setups based on the completion time (`close_timestamp`) of the candidate extreme LTF FVG.

#### Scenario: Live Scanner Formation Filter
- **GIVEN** `EXTREME_SESSION_FILTER_ENABLED=true` or `session_filter=true`
- **WHEN** an LTF FVG completes outside NY session hours (e.g. 08:30 UTC)
- **THEN** the live scanner SHALL NOT emit the setup into `PENDING_RETRACE`
- **AND** SHALL NOT send a `NEW_SETUP` Telegram alert

#### Scenario: Backtest Formation Filter
- **GIVEN** `session_filter=true` in `run_extreme_backtest`
- **WHEN** an LTF FVG completes outside NY session hours
- **THEN** the backtest engine SHALL skip the setup and increment `trades_filtered_out`

---

### Requirement: Entry Fill Session & Weekday Filtering
The live trade tracker and backtest engines SHALL support filtering trade execution based on the timestamp when price fills the limit entry.

#### Scenario: Live Trade Tracker Entry Filter
- **GIVEN** `EXTREME_ENTRY_SESSION_FILTER_ENABLED=true`
- **WHEN** price touches a pending setup's entry price outside NY session hours (e.g. 10:00 UTC)
- **THEN** the setup SHALL NOT transition to `TRADE_ACTIVE`
- **AND** SHALL NOT trigger an `ENTRY_FILLED` Telegram alert

#### Scenario: Backtest Entry Filter
- **GIVEN** `entry_session_filter=true` in `run_extreme_backtest`
- **WHEN** entry fill occurs outside NY session hours
- **THEN** the backtest engine SHALL skip the trade simulation and increment `trades_filtered_out`

---

### Requirement: Independent Multi-Dimension Filter Operation
The system SHALL evaluate all four filter flags independently without conflict.

#### Scenario: FVG formed off-session, entry filled in-session with entry_session_filter=true
- **GIVEN** `session_filter=false` and `entry_session_filter=true`
- **WHEN** an FVG forms at 08:45 UTC (London) and price retraces to entry at 14:15 UTC (NY session)
- **THEN** the trade SHALL execute successfully because entry occurred inside NY session
