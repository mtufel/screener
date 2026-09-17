# Design: Consolidated SessionFilterConfig Architecture

## Architecture Overview

```
                          SessionFilterConfig (session_filter.py)
                         ┌────────────────────────────────────────┐
                         │ fvg_sessions: str ("ALL", "NY", ...)   │
                         │ entry_sessions: str ("ALL", "NY", ...) │
                         │ fvg_weekdays_only: bool                │
                         │ entry_weekdays_only: bool              │
                         │────────────────────────────────────────│
                         │ + is_fvg_valid(ts_ms) -> bool          │
                         │ + is_entry_valid(ts_ms) -> bool        │
                         │ + from_legacy(...) -> Config           │
                         └───────────────────┬────────────────────┘
                                             │
               ┌─────────────────────────────┼────────────────────────────┐
               ▼                             ▼                            ▼
  strategy_extreme_fvg.py       extreme_trade_tracker.py       backtest_extreme_fvg.py
  (FVG Formation Check)          (Entry Trigger Check)          (Simulated Backtest)
```

## SessionFilterConfig Data Model

```python
@dataclass
class SessionFilterConfig:
    fvg_sessions: str = "ALL"
    entry_sessions: str = "ALL"
    fvg_weekdays_only: bool = False
    entry_weekdays_only: bool = False

    def is_fvg_valid(self, ts_ms: int) -> bool:
        if self.fvg_weekdays_only and not is_weekday(ts_ms):
            return False
        if not self.fvg_sessions or self.fvg_sessions.strip().upper() == "ALL":
            return True
        return is_in_session(ts_ms, self.fvg_sessions)

    def is_entry_valid(self, ts_ms: int) -> bool:
        if self.entry_weekdays_only and not is_weekday(ts_ms):
            return False
        if not self.entry_sessions or self.entry_sessions.strip().upper() == "ALL":
            return True
        return is_in_session(ts_ms, self.entry_sessions)
```

## Backward Compatibility Bridge

To guarantee that existing test suites (SOT) and callers continue functioning without changes:
```python
    @classmethod
    def from_legacy(
        cls,
        session_filter: Optional[bool] = None,
        weekday_filter: Optional[bool] = None,
        entry_session_filter: Optional[bool] = None,
        entry_weekday_filter: Optional[bool] = None,
        sessions: Optional[str] = None,
        entry_sessions: Optional[str] = None,
        default_session: str = "NY",
    ) -> "SessionFilterConfig":
        # If session_filter is False -> "ALL". If True -> sessions or "NY".
        # If entry_session_filter is False -> "ALL". If True -> entry_sessions or "NY".
```
And re-export `is_in_ny_session(ts_ms)` and `is_weekday(ts_ms)` in `session_filter.py`, `strategy_extreme_fvg.py`, and `backtest_extreme_fvg.py`.
