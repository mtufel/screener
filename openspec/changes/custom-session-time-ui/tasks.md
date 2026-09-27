# Tasks: Custom Time Range Input for Session Filters in UI

- [x] 1. Markup Updates (`templates/index.html`)
  - [x] 1.1 Add `Custom (UTC)...` option and hidden custom text input for `extremeSessions` in Control Bar.
  - [x] 1.2 Add `Custom (UTC)...` option and hidden custom text input for `extremeEntrySessions` in Control Bar.
  - [x] 1.3 Add `Custom (UTC)...` option and hidden custom text input for `extremeBtSession` in Backtest Form.
  - [x] 1.4 Add `Custom (UTC)...` option and hidden custom text input for `extremeBtEntrySession` in Backtest Form.
- [x] 2. JavaScript Handler Updates (`templates/index.html`)
  - [x] 2.1 Implement `handleExtremeSessionSelect()` and `handleBtSessionSelect()`.
  - [x] 2.2 Update `syncExtremeConfigUI()` to detect custom strings, select `CUSTOM`, and populate input.
  - [x] 2.3 Update `loadExtremeSetups()` and `runExtremeBacktest()` to extract custom input values when `CUSTOM` is selected.
- [x] 3. Automated & Manual Verification
  - [x] 3.1 Run unit test suite (`pytest -v`) to confirm 100% pass rate.
  - [x] 3.2 Verify custom time inputs with end-to-end Python test.

