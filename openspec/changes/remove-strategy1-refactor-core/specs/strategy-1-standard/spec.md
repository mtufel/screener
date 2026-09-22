## ADDED Requirements

### Requirement: Strategy 1 is retired
The system SHALL NOT implement, schedule, or expose Strategy 1 (2-stage standard FVG) scanning, tracking, or backtesting.

#### Scenario: No Strategy 1 runtime
- **WHEN** the FastAPI app starts
- **THEN** no Strategy 1 background worker is created and `ENABLE_STRATEGY_1` is not read

#### Scenario: No Strategy 1 API keys
- **WHEN** a client calls `/api/health` or `/api/status`
- **THEN** the payload does not require a Strategy 1 engine to be present
