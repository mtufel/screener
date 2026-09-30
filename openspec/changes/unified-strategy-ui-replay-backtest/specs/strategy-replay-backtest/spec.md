## ADDED Requirements

### Requirement: Virtual clock injection
The system SHALL provide a process-wide injectable time source. All wall-clock reads in the strategy engines, the trade tracker, and the session filter SHALL resolve through this source, which SHALL return real system time when no virtual clock is installed. Installing a virtual clock SHALL affect only the execution scopes that explicitly install it.

#### Scenario: Real time is unchanged by default
- **WHEN** no virtual clock is installed
- **THEN** every wall-clock read in engines, tracker, and session filter SHALL return the real system time, identical to prior behavior.

#### Scenario: Virtual time drives execution
- **WHEN** a virtual clock is installed and advanced to timestamp T
- **THEN** closed-candle filtering, session/weekday gates, expiry bookkeeping, and timestamp stamps SHALL evaluate as if T were the current time.

#### Scenario: Engine time parameters take precedence
- **WHEN** an engine function receives an explicit `current_time_ms`/`now_ms` argument
- **THEN** that explicit value SHALL be used instead of the clock value.

### Requirement: Replay data provider serves strictly as-of data
The system SHALL provide a market-data provider implementation that serves historical candles from a pre-fetched in-memory dataset, returning only candles whose close time is at or before the current (virtual) time, and synthesizing current prices from the replay head. It MUST NOT serve any candle that closes after the current time.

#### Scenario: No lookahead in candle access
- **WHEN** the provider is asked for the latest N candles at virtual time T
- **THEN** it SHALL return only candles with `close_time <= T`, newest-last, limited to N.

#### Scenario: Current price reflects replay head
- **WHEN** the provider is asked for current mid prices at virtual time T
- **THEN** it SHALL report, per replay symbol, the close of the latest candle closing at or before T.

#### Scenario: Warmup data is included
- **WHEN** a replay dataset is constructed for a lookback of D days
- **THEN** the dataset SHALL include the requested lookback plus a warmup margin so anchors and indicators are warm at simulation start.

### Requirement: Backtest runs the live execution path
The system SHALL be able to run a backtest by executing the production screener scan-cycle against the replay data provider under a virtual clock, reusing the same orchestration loop, strategy setup generation, and trade-tracking state machine as live operation. The replay execution SHALL use an isolated in-memory trade ledger and SHALL NOT mutate the live trade ledger, its persisted files, or Redis-persisted state.

#### Scenario: Cycle reuse
- **WHEN** a replay step advances virtual time to a closed-candle boundary
- **THEN** the system SHALL invoke the same scan-cycle orchestration used by the live daemon, resolving the selected strategy by name and passing the effective (config-driven) parameters.

#### Scenario: Ledger isolation
- **WHEN** trades are opened, filled, or closed during replay
- **THEN** they SHALL be recorded in the replay's isolated ledger and the live ledger and its persistence (file/Redis) SHALL remain unchanged.

#### Scenario: No lookahead across the run
- **WHEN** any replay step executes
- **THEN** no code path SHALL observe candles or prices dated after that step's virtual time.

### Requirement: Replay watch mode with controllable speed
The replay runner SHALL execute as a managed background run exposing: speed control (virtual-time-per-real-time multiplier and an unlimited "max" mode), pause, resume, and abort. Replay progress SHALL be streamable over WebSocket, including virtual time, the current replay head, newly produced events, and a ledger summary; a final report SHALL be produced on completion and after abort.

#### Scenario: Watch progress streams
- **WHEN** a replay is running and a client subscribes to the replay stream
- **THEN** the client SHALL receive progress frames containing the replay status, virtual time, replay head, and ledger summary as steps complete.

#### Scenario: Pause and resume
- **WHEN** a running replay is paused and later resumed
- **THEN** virtual time SHALL stop advancing while paused and SHALL continue from the paused point on resume without restarting the run.

#### Scenario: Abort preserves partial results
- **WHEN** a replay is aborted
- **THEN** the run SHALL stop, be marked aborted, and a report covering the simulated period so far SHALL remain retrievable.

### Requirement: Config-driven backtest parameters
The backtest API SHALL accept the full parameter set declared by the selected strategy and resolve it with the framework precedence (explicit request parameters > strategy defaults). Parameters not declared by the strategy SHALL be rejected or ignored with an indication, and the effective resolved parameter set SHALL be reported with the result.

#### Scenario: Strategy defaults applied
- **WHEN** a backtest is requested without explicit parameters
- **THEN** the strategy's declared defaults SHALL be used.

#### Scenario: Request overrides applied
- **WHEN** a backtest request supplies explicit values for declared parameters
- **THEN** those values SHALL take precedence over the strategy defaults.

#### Scenario: Effective parameters reported
- **WHEN** a backtest completes
- **THEN** the response SHALL include the effective resolved parameter set used.

### Requirement: Unified strategy-agnostic dashboard
The dashboard SHALL present a strategy selector populated from the strategy registry and render a single strategy-agnostic panel for the selected strategy, including its live screener controls and its backtest controls. Backtest controls SHALL be generated from the strategy's declared parameters. Selecting a strategy SHALL set it active for the daemon.

#### Scenario: Strategy selection drives the panel
- **WHEN** the user selects a strategy in the dropdown
- **THEN** the dashboard SHALL render that strategy's live and backtest panels and SHALL activate it for the screener daemon.

#### Scenario: Backtest controls generated from declared params
- **WHEN** a strategy is selected
- **THEN** the backtest tab SHALL render controls for each of its declared parameters and submit the full set to the backtest API.

#### Scenario: Existing live capabilities preserved
- **WHEN** any strategy is selected
- **THEN** the dashboard SHALL retain the live trade log, 4H FVG map, daemon controls, WebSocket updates, chart modal, and run comparison features for that strategy.
