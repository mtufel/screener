# CLAUDE.md

## Development Workflow
- **Every feature must use OpenSpec**: propose → design → tasks → apply → archive.
- **All dev work in new branches out of `master`**: create a branch per change (e.g. `feat/<change-slug>`), merge via PR.

## Commands
- Backtest: `python3 backtest_extreme_fvg.py --symbol BTC --days 30 --ltf 5m --min-gap-pct 0.03 --session-filter`
- Live screener: `python3 live_screener_extreme.py --ltf 5m`
- Tests: `pytest -v`
- OpenSpec: `openspec view`, `openspec list`, `openspec change`

## Environment
- Python venv: `.venv`
- Main branch: `main` (default)
