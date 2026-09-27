## ADDED Requirements

### Requirement: Runtime strategy activation
The system SHALL expose an API to set the active strategy (validated against the registry) at runtime, in addition to the environment default. The daemon SHALL run the strategy currently set as active.

#### Scenario: Activate a registered strategy
- **WHEN** a client activates a registered strategy by name
- **THEN** the daemon's active strategy SHALL be set to that name and subsequent scan cycles SHALL resolve and run it.

#### Scenario: Activation validates the name
- **WHEN** a client attempts to activate an unregistered strategy name
- **THEN** the API SHALL fail with the list of available strategy names.

### Requirement: Strategy enumeration for the dashboard
The system SHALL expose an API listing every registered strategy with its display name and whether it is currently active, so the dashboard can populate the strategy selector without hard-coding strategy names.

#### Scenario: List strategies
- **WHEN** a client requests the strategy list
- **THEN** the response SHALL include every registered strategy's name, display name, and active flag.
