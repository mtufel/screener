## ADDED Requirements

### Requirement: Video FVG Registry Identity
The strategy SHALL register in the pluggable strategy registry under the stable name `video_fvg`, with display name `Video FVG (4H Anchor)` and description `4H FVG Bias + HTF Respect Confirmation + First LTF FVG Entry + 3R Target`. It SHALL coexist with `extreme_fvg` (Strategy 2) and `liquidity_sweep_fvg` (Strategy 3) in a single registry snapshot.

#### Scenario: Registry snapshot lists all three strategies
- **WHEN** the registry snapshot is built at import time
- **THEN** `extreme_fvg`, `liquidity_sweep_fvg`, and `video_fvg` SHALL all resolve from the registry
- **AND** each SHALL expose a non-empty description.

#### Scenario: Video FVG is numbered Strategy 4
- **WHEN** a client reads the display metadata for `video_fvg`
- **THEN** the display name SHALL be `Video FVG (4H Anchor)`
- **AND** the adapter class SHALL be `Strategy4VideoFVG` (`strategies/strategy4_video_fvg.py`).

### Requirement: Video FVG Default Parameters
The strategy SHALL declare the following declarative defaults: `ltf_timeframe` = `"5m"`, `min_gap_pct` = `0.03`, `completion_target` = `"3R"`, `htf_confirm_body_pct` = `0.5`, `session_filter` = `False`, `weekday_filter` = `False`, `sessions` = `"ALL"`.

#### Scenario: Session gating is off by default
- **WHEN** the engine boots without an explicit session override
- **THEN** `session_filter` SHALL default to `False`, `weekday_filter` to `False`, and `sessions` to `ALL`
- **AND** LTF FVG setups SHALL be accepted in any session unless the operator opts into gating.

#### Scenario: Defaults resolve through the uniform interface
- **WHEN** a caller invokes `resolve_params({})` on the strategy
- **THEN** the resolved parameters SHALL contain every key listed above at its declared default.

### Requirement: Multi-Timeframe Algorithmic Flow
The adapter SHALL delegate to the `video_fvg` engine, which identifies the most recently closed non-invalidated 4H FVG as the directional anchor, requires an HTF Respect confirmation close, then selects the first LTF FVG forming after that confirmation as the trade setup. Targets SHALL be 1R/2R telemetry with a 3R primary completion target.

#### Scenario: 4H anchor and HTF confirmation precede entry
- **GIVEN** a non-invalidated 4H FVG anchor exists
- **WHEN** price touches the zone and the 4H candle closes with a body satisfying `|close - open| / (high - low) >= htf_confirm_body_pct` in the bias direction
- **THEN** HTF respect SHALL be recorded as confirmed
- **AND** the first LTF FVG forming after that close SHALL become the active setup.

#### Scenario: Engine modules import lazily
- **WHEN** `strategies.strategy4_video_fvg` is imported
- **THEN** the `strategy_video_fvg` and `backtest_video_fvg` engine modules SHALL NOT be imported at module load time
- **AND** they SHALL be imported at call time, so unit tests that patch the engine remain import-safe.

### Requirement: Strategy-Parameterized API and UI Integration
The strategy SHALL be reachable through the strategy-parameterized API surface, where `{strategy}` resolves to the registered name `video_fvg`. `/api/strategies` SHALL include Strategy 4 in its response list, and the UI strategy dropdown SHALL display its display name and description.

#### Scenario: Registry listing endpoint includes Strategy 4
- **WHEN** a client calls `GET /api/strategies`
- **THEN** the response SHALL include an entry for `video_fvg` with its display name, description, and default parameters.

#### Scenario: Metadata endpoint resolves through the templated route
- **WHEN** a client calls `GET /api/{strategy}/info` with `{strategy}` = `video_fvg`
- **THEN** the response SHALL return the metadata, description, and default parameters
- **AND** the resolved path `/api/video_fvg/info` SHALL be served without any per-strategy route registration.

#### Scenario: Status endpoint resolves through the templated route
- **WHEN** a client calls `GET /api/{strategy}/status` with `{strategy}` = `video_fvg`
- **THEN** the response SHALL return the runtime status and ledger summary for the Video FVG daemon.
