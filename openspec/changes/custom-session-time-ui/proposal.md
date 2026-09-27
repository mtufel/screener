# Change Proposal: Custom Time Range Input for Session Filters in UI

## 1. Problem Statement
The backend engine (`session_filter.py`) already supports custom UTC time ranges (e.g. `13:30-20:00`, `08:00-12:00,13:00-17:00`, or cross-midnight intervals like `22:00-04:00`). However, the Web Dashboard UI (`templates/index.html`) only provides `<select>` dropdowns with pre-defined session presets (`ALL`, `NY`, `LONDON`, `LONDON,NY`, `ASIA`, `LONDON_KZ`, `NY_KZ`). Users currently have no way in the UI to enter or test arbitrary custom session hours for FVG formation or trade entry fills.

## 2. Proposed Solution
1. **Interactive Custom Time Range Controls in UI**:
   - Add a `Custom (UTC Range)...` option to the FVG Session and Entry Session dropdowns in both:
     - The Extreme Screener Control Bar (live daemon configuration).
     - The Extreme Backtest Form (historical backtesting).
   - When `CUSTOM` is selected, display an inline text input (`placeholder="e.g. 13:30-20:00 or 08:00-12:00,13:00-17:00 (UTC)"`).
   - When a preset is selected, automatically hide the custom text input.
2. **Seamless Bidirectional UI Synchronization**:
   - When polling `/api/extreme/status` or restoring config, if the configured session matches a known preset, select that preset in the dropdown.
   - If the configured session is a custom range (e.g. `14:00-21:30`), automatically select `CUSTOM`, unhide the input, and populate it with the custom string.
3. **Scan & Backtest Request Construction**:
   - `loadExtremeSetups()` and `runExtremeBacktest()` will read the custom input value whenever `CUSTOM` is selected.

## 3. Impact Analysis
- **Non-Breaking:** Presets and existing API parameters remain completely intact.
- **Enhanced Flexibility:** Users can filter trades to exact exchange trading hours, macro data releases, or custom operational windows.
