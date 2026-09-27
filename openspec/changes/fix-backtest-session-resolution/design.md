# Design: Fix Backtest Session Parameter Resolution

## Precedence Architecture in `from_legacy()`

```text
Incoming Call: from_legacy(session_filter, sessions, entry_session_filter, entry_sessions)
                            │
              Is `sessions` non-empty string?
                    ├── YES ──> resolved_fvg = sessions.strip()
                    └── NO  ──> Is session_filter is False?
                                  ├── YES ──> resolved_fvg = "ALL"
                                  └── NO  ──> Is session_filter is True?
                                                ├── YES ──> resolved_fvg = default_session ("NY")
                                                └── NO  ──> resolved_fvg = "ALL"
```

The same symmetric precedence applies to `entry_sessions` and `entry_session_filter`.

## API Endpoint Defaulting in `main.py`
In `api_extreme_backtest`:
```python
sess_filter = (
    session_filter
    if session_filter is not None
    else (
        (sessions.strip().upper() != "ALL")
        if (sessions and sessions.strip())
        else os.getenv("EXTREME_SESSION_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes")
    )
)
```
