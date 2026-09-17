# Specification: Session Parameter Resolution

## Precedence Invariants

- **WHEN** `SessionFilterConfig.from_legacy(sessions="LONDON", session_filter=False)` is called
  **THEN** it SHALL set `fvg_sessions="LONDON"` because explicit session string takes precedence over defaulted boolean flag.
- **WHEN** `SessionFilterConfig.from_legacy(session_filter=False)` is called without `sessions`
  **THEN** it SHALL set `fvg_sessions="ALL"`.
- **WHEN** `SessionFilterConfig.from_legacy(session_filter=True)` is called without `sessions`
  **THEN** it SHALL set `fvg_sessions="NY"` (or the configured `default_session`).
- **WHEN** `SessionFilterConfig.from_legacy(entry_sessions="NY", entry_session_filter=False)` is called
  **THEN** it SHALL set `entry_sessions="NY"`.
- **WHEN** `api_extreme_backtest` receives `sessions="LONDON"` and `entry_sessions="NY"` without `session_filter` query parameters
  **THEN** the returned report and execution SHALL enforce London for FVG formation and NY for Entry.
