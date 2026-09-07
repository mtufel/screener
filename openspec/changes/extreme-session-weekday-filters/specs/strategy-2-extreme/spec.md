## MODIFIED Requirements

### Requirement: Multi-Timeframe LTF Support (1m, 5m, 15m, 1h)
The system MUST support full-pipeline execution across 1m, 5m, 15m, and 1h lower timeframes across real-time scanning, background daemon monitoring, historical backtesting, chart visualization, and UI selection controls.

#### Scenario: 1h timeframe execution and serialization
* **WHEN** a 1h LTF timeframe is configured in `.env` (`EXTREME_LTF_TIMEFRAME=1h`) or via runtime API
* **THEN** candidate LTF FVGs SHALL be evaluated on 1h bars with 1h candle gap offsets applied to timestamp calculations
* **AND** historical backtests SHALL include `ltf_timeframe` across all serialized trade results and metrics.

#### Scenario: Backtest respects session filter configuration
* **WHEN** `EXTREME_SESSION_FILTER_ENABLED=true` is set in `.env`
* **THEN** the backtest engine SHALL only count trades whose LTF FVG c3 close timestamp falls within 13:00–22:00 UTC
* **AND** trades formed outside the session window SHALL be excluded from report totals

#### Scenario: Backtest respects weekday filter configuration
* **WHEN** `EXTREME_WEEKDAY_FILTER_ENABLED=true` is set in `.env`
* **THEN** the backtest engine SHALL only count trades formed on Monday–Friday (UTC)
* **AND** trades formed on Saturday or Sunday SHALL be excluded from report totals
