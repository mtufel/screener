# Specification: Consolidated SessionFilterConfig

## Requirement: Unified Session Filter Invariants

### 1. Default Permissive Configuration
- **WHEN** `SessionFilterConfig()` is instantiated with default values
- **THEN** `fvg_sessions` SHALL be `"ALL"`
- **AND** `entry_sessions` SHALL be `"ALL"`
- **AND** `fvg_weekdays_only` SHALL be `False`
- **AND** `entry_weekdays_only` SHALL be `False`
- **AND** `is_fvg_valid(ts_ms)` SHALL return `True` for any UTC timestamp
- **AND** `is_entry_valid(ts_ms)` SHALL return `True` for any UTC timestamp.

### 2. Session Invariant Validation
- **GIVEN** `fvg_sessions = "NY"` (13:00–22:00 UTC)
- **WHEN** `is_fvg_valid(ts_ms)` is called with timestamp at 14:00 UTC
- **THEN** it SHALL return `True`
- **WHEN** `is_fvg_valid(ts_ms)` is called with timestamp at 04:00 UTC
- **THEN** it SHALL return `False`.

### 3. Multi-Session Invariant Validation
- **GIVEN** `entry_sessions = "LONDON,NY"` (07:00–16:00 and 13:00–22:00 UTC)
- **WHEN** `is_entry_valid(ts_ms)` is called with timestamp at 08:30 UTC
- **THEN** it SHALL return `True`
- **WHEN** `is_entry_valid(ts_ms)` is called with timestamp at 20:00 UTC
- **THEN** it SHALL return `True`
- **WHEN** `is_entry_valid(ts_ms)` is called with timestamp at 02:00 UTC
- **THEN** it SHALL return `False`.

### 4. Weekday Invariant Validation
- **GIVEN** `fvg_weekdays_only = True`
- **WHEN** timestamp falls on Saturday or Sunday UTC
- **THEN** `is_fvg_valid(ts_ms)` SHALL return `False` regardless of session.

### 5. Legacy Translation Invariant
- **WHEN** `SessionFilterConfig.from_legacy(session_filter=True, sessions="LONDON")` is called
- **THEN** `fvg_sessions` SHALL be `"LONDON"`
- **WHEN** `SessionFilterConfig.from_legacy(session_filter=False)` is called
- **THEN** `fvg_sessions` SHALL be `"ALL"`.
