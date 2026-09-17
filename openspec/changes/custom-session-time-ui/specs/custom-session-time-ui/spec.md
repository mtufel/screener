# Capability Specification: Custom Session Time UI

## Invariants

### 1. Dropdown Selection Invariant
- The session dropdowns (`extremeSessions`, `extremeEntrySessions`, `extremeBtSession`, `extremeBtEntrySession`) SHALL include a `Custom (UTC)...` option with value `"CUSTOM"`.
- Selecting `"CUSTOM"` SHALL reveal the corresponding custom text input field.
- Selecting any preset option SHALL hide the custom text input field and apply the preset.

### 2. Custom Input Invariant
- Entering a valid time range (e.g. `13:30-20:00` or `08:00-12:00,13:00-17:00`) in the custom text input SHALL pass that string directly to the configuration API or backtest runner.
- The UI placeholder SHALL explicitly indicate UTC formatting (`e.g. 13:30-20:00 (UTC)`).

### 3. Bidirectional Sync Invariant
- When runtime configuration reports a non-preset string (such as `14:30-21:00`), `syncExtremeConfigUI` SHALL set the dropdown to `"CUSTOM"`, display the custom input, and populate the input value.
