## Purpose

Defines the pluggable strategy framework: a name-keyed strategy registry with declarative per-strategy parameters, config↔strategy reconciliation, and a strategy-agnostic trade ledger / daemon / API so new trading strategies can be added drop-in without modifying core orchestration.

## ADDED Requirements

### Requirement: Strategy registry keyed by stable name
The system SHALL maintain a registry of available strategies, each addressable by a stable unique name (e.g. `extreme_fvg` for the existing Strategy 2). The daemon and API MUST select which strategy to run by name rather than importing an engine directly. The registry SHALL expose the set of available strategy names so callers can discover, validate, and enumerate strategies at runtime.

#### Scenario: Enumerate available strategies
- **WHEN** a caller asks the registry for the available strategy names
- **THEN** the system SHALL return a list of at least the `extreme_fvg` strategy name, one entry per registered strategy.

#### Scenario: Resolve a strategy by name
- **WHEN** a caller requests a strategy by its registered name
- **THEN** the system SHALL return the strategy instance whose name matches, ready to be run.

#### Scenario: Request for an unknown strategy name fails
- **WHEN** a caller requests a strategy by a name that is not registered
- **THEN** the system SHALL raise an explicit error identifying the unknown name and listing available strategy names.

### Requirement: Declarative per-strategy parameters
Each strategy SHALL declare its own set of tunable parameters and their default values as part of the strategy definition itself (not scattered across global configuration). The system SHALL honor freqtrade-style precedence when a runtime config overrides a strategy default: **explicit runtime configuration > strategy-declared default > built-in default**.

#### Scenario: Strategy default parameter applied
- **WHEN** a strategy is run with no explicit runtime override for a parameter it declares
- **THEN** the strategy's declared default SHALL be used for that parameter.

#### Scenario: Runtime config overrides strategy default
- **WHEN** a caller provides an explicit runtime value for a declared parameter
- **THEN** the explicit runtime value SHALL take precedence over the strategy's declared default.

#### Scenario: Effective parameters are observable
- **WHEN** a strategy is resolved for a run
- **THEN** the daemon SHALL be able to obtain the effective (resolved) parameter set after reconciliation so it can pass them through to the strategy and downstream systems.

### Requirement: Strategy-agnostic trade ledger
The trade ledger SHALL record trades for any strategy without strategy-specific columns. Each recorded trade SHALL carry (a) the strategy name that produced it and (b) a strategy-owned parameter blob capturing the exact configuration under which the trade was opened, in addition to the trade's entry/stop/targets and lifecycle state.

#### Scenario: Trades from different strategies coexist
- **WHEN** trades are opened by more than one strategy name
- **THEN** the ledger SHALL store each trade with its originating strategy name and its strategy-parameter blob, and SHALL be able to query or report trades filtered by strategy name.

#### Scenario: Trade records preserve entry configuration
- **WHEN** a trade is opened
- **THEN** the recorded trade SHALL include the strategy-parameter blob that documents the effective parameters at open time, independent of any later configuration changes.

### Requirement: Strategy-agnostic daemon orchestration
The screener daemon's scan-orchestration loop SHALL be strategy-agnostic: it SHALL resolve the active strategy from configuration by name and delegate setup-generation to that strategy, rather than hard-wiring a specific engine. The active strategy SHALL be configurable at runtime and selectable from the registry without code changes.

#### Scenario: Daemon runs the configured active strategy
- **WHEN** the daemon performs a scan cycle and the active strategy name is `extreme_fvg`
- **THEN** the daemon SHALL resolve `extreme_fvg` from the registry and run that strategy's setup generator, producing the same lifecycle outcomes as today (no behavior change for Strategy 2).

#### Scenario: Active strategy change requires no core changes
- **WHEN** the active strategy name in configuration is changed to another registered strategy
- **THEN** the daemon SHALL run that strategy through the same orchestration loop without any modification to orchestration code.

### Requirement: Strategy setup and backtest interface
The system SHALL expose a uniform interface that each strategy implements for (a) generating trade setups for a symbol and (b) running a backtest. Strategy update/backtest operations SHALL be reachable through the daemon/API without import-time coupling to a specific engine.

#### Scenario: Uniform setup generator
- **WHEN** the daemon needs setups for a symbol on the active strategy
- **THEN** it SHALL invoke the strategy's uniform setup-generator method and receive a strategy-agnostic setup structure the orchestration loop can process.

#### Scenario: Uniform backtest entry point
- **WHEN** the API runs a backtest for a selected strategy
- **THEN** it SHALL invoke that strategy's uniform backtest method with the strategy's effective parameters and return the backtest report.

### Requirement: Backward-compatible API addressing
The system SHALL keep the existing Strategy 2 API paths usable unchanged, and MAY additionally expose strategy-parameterized paths (e.g. `/api/{strategy}/...`). Invoking an existing Strategy 2 path SHALL behave identically to today.

#### Scenario: Existing Strategy 2 endpoint unchanged
- **WHEN** a client calls the existing Strategy 2 API paths with the active strategy set to `extreme_fvg`
- **THEN** the responses SHALL match current behavior (same scan, backtest, config, status, settings endpoints).
