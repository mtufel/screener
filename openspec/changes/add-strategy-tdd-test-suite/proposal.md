## Why

The repo has ~135 passing tests but four placeholder test files for Strategy 2 are empty (`test_ltf_fvg_discovery.py`, `test_extreme_ranking.py`, `test_execution_params.py`, `test_trade_lifecycle.py`), and chart rendering, Telegram formatting/chunking, and the Strategy 1 trade tracker's milestone/exit/persistence paths have zero or minimal coverage. Strategy rules live in `STRATEGIES.md` and `openspec/specs/strategy-1-standard` / `strategy-2-extreme`; the suite should be written strategy-first (rule → test) so future refactors are guarded by behavior contracts, not implementation snapshots.

## What Changes

- Fill the four empty placeholder test files with strategy-rule-driven cases for Strategy 2 Steps 3–6: post-first-touch LTF FVG discovery (no lookahead), extreme-anchor ranking, exact execution parameters (entry/SL/1R-2R-3R), and the immutable trade-ledger lifecycle.
- Deepen ledger coverage for **pending** trades (replacement by newer emission, older-emission rejection, pre-entry invalidation, anchor breach, absent-expiry, same-bar fill+exit, pre-formation-candle immunity) and **active** trades (immutability against re-emissions, SL-precedence-over-TP, chronological multi-candle resolution, MFE tracking, per-target completion 1R/2R/3R, persistence across restart).
- Add new test coverage where none exists today:
  - Chart generation (`chart_generator.py`): both chart functions return non-empty PNG bytes across setup states; empty-candle guards; `output_path` writes.
  - Telegram (`telegram_client.py`): message content correctness, 4096-char chunking, empty-input, and missing-credential no-send behavior.
  - Strategy 1 trade tracker (`trade_tracker.py`): 1R/1.5R milestone alerts fire once, 2R/SL exit alerts fire once, closed trades never re-checked, disk persistence round-trip, HTF-FVG occupancy suppression.
- All tests stay 100% offline (no network): dummy clients, `tmp_path` storage, monkeypatched clocks/constants.

## Capabilities

### New Capabilities

- `strategy-tdd-test-suite`: The repository-wide offline pytest suite verifying documented strategy behavior — FVG math, entry/exit detection, TP/SL resolution, formation-candle exclusion, timestamp correctness, extreme-anchor ranking, ledger tracking (pending + active), chart rendering, and Telegram reporting/anti-spam. Each requirement/scenario maps 1:1 to test cases implemented under this change.

### Modified Capabilities

<!-- None: trading behavior itself is unchanged; this change only adds verification. -->

## Impact

- **Affected code**: test files only — the four placeholder files get filled; new files `test_chart_generation.py`, `test_telegram_formatting.py`, `test_trade_tracker_stage2.py` are added. **No production code changes**; if a new test exposes a real bug, the fix is scoped into this change with the test as the contract.
- **Dependencies**: none added (pytest, pytest-asyncio, matplotlib already present).
- **Docs**: test-count references in `openspec/project.md` updated after the suite runs.
