## MODIFIED Requirements

### Requirement: Minimum 3:1 Take Profit Target
The system SHALL set a minimum 3:1 risk-reward target for all Strategy 4 trades. The 2R and 1R levels are calculated as intermediate milestones but the primary completion target is 3R.

#### Scenario: 3R target set as default completion target
- **WHEN** a Strategy 4 setup is generated
- **THEN** the completion target SHALL default to `"3R"` — the backtest and ledger SHALL resolve `COMPLETED_TP` when the 3R target is reached.

### Requirement: HTF FVG Anchor Metadata in Trade Record
Every trade opened by Strategy 4 SHALL record the 4H anchor FVG metadata (bottom, top, formed timestamp, direction) in addition to the LTF FVG metadata, so the ledger captures the full two-timeframe confirmation context for later analysis.

#### Scenario: Trade record contains full two-TF context
- **WHEN** a Strategy 4 trade is opened and recorded
- **THEN** the ledger record SHALL contain the 4H anchor FVG block alongside the LTF FVG block, enabling per-anchor performance analysis post-archive.

### Requirement: Multi-Timeframe LTF Support (1m, 5m, 15m)
The system SHALL support full-pipeline execution of Strategy 4 across 1m, 5m, and 15m lower timeframes for real-time scanning, background daemon monitoring, and historical backtesting.

#### Scenario: 15m timeframe backtest
- **WHEN** a Strategy 4 backtest is run with `ltf=15m`
- **THEN** LTF FVG setups SHALL be evaluated on 15m bars and all results SHALL include `ltf_timeframe = "15m"` in serialized output.

### Requirement: Strategy Registration and Daemon Interoperability
Strategy 4 SHALL be registered in the strategy registry with `name="video_fvg"` and SHALL be runnable as the active daemon strategy by setting `EXTREME_ACTIVE_STRATEGY=video_fvg`. The daemon SHALL invoke `Strategy4VideoFVG.find_setups()` through the same orchestration loop used for Strategy 2, with no changes to `screener_cycle.py`.

#### Scenario: Strategy 3 selected as active daemon strategy
- **WHEN** `EXTREME_ACTIVE_STRATEGY=video_fvg` is set in the environment
- **THEN** the daemon SHALL resolve `video_fvg` from the registry and run `find_setups()` for each symbol, producing setups with `strategy="video_fvg"` in the ledger.

#### Scenario: Strategy 3 backtest via API
- **WHEN** a client calls `GET /api/video_fvg/backtest?symbol=BTC&days=30`
- **THEN** the system SHALL invoke `Strategy4VideoFVG.backtest()` and return the backtest report with the `video_fvg` strategy name and effective parameters.

#### Scenario: Strategy 4 selected as active daemon strategy
- **WHEN** `EXTREME_ACTIVE_STRATEGY=video_fvg` is set in the environment
- **THEN** the daemon SHALL resolve `video_fvg` from the registry and run `find_setups()` for each symbol, producing setups with `strategy="video_fvg"` in the ledger.

#### Scenario: Strategy 4 backtest via API
- **WHEN** a client calls `GET /api/{strategy}/backtest?symbol=BTC&days=30` with `{strategy}` = `video_fvg`
- **THEN** the system SHALL invoke `Strategy4VideoFVG.backtest()` and return the backtest report with the `video_fvg` strategy name and effective parameters.
