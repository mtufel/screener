# Design: Strategy-Based TDD Test Suite

## Context

Four placeholder test files map to Strategy 2 Steps 3–6, and several modules have no tests at all. The suite must verify rules documented in `STRATEGIES.md` and the OpenSpec capability specs without coupling to implementation internals, and must run fully offline.

## Goals / Non-Goals

- Goals: behavior-contract tests for FVG math, post-touch discovery, extreme ranking, execution params, ledger lifecycle (pending + active), charts, Telegram formatting/anti-spam, Strategy 1 tracker milestones/exits/persistence.
- Non-Goals: no production code changes (bug fixes triggered by tests are in-scope but minimal); no network touches; no new dependencies.

## Decisions

### D1 — Strategy-first, not snapshot tests
Each test docstring cites the rule it verifies (e.g. "The candle that formed the FVG can NEVER count as its own touch"). Assertions target documented outputs (zone bounds, entry/SL/targets, states, timestamps), not private internals.

### D2 — 100% offline
No Hyperliquid/Telegram network calls: trackers take `tmp_path` storage; Telegram send tests rely on missing-credential early-return (no HTTP); charts force the `Agg` backend. Live-clock branches use monkeypatched `time.time` and real aligned epoch-ms timestamps so both live and candle-evidence paths are exercised deterministically.

### D3 — Builders over fixtures
Each test file carries small local builders (`mk_candle`, `mk_setup`, `mk_anchor`) mirroring the exact dict shapes the production scanners emit, so failures are readable and files stay independent.

### D4 — Pending/active ledger depth (user priority)
Dedicated groups: pending replacement (newer wins, older ignored, no duplicate rows), pre-entry invalidation (SL breach, anchor breach, absent-expiry, pre-formation-candle immunity — the PAXG spam-loop regression), same-bar fill+exit resolution, active immutability against re-emissions, SL-before-TP precedence on shared candles, chronological multi-candle resolution, MFE math, per-target completion (1R/2R/3R), and restart persistence.

### D5 — Event-discipline assertions
Anti-spam is asserted at the event level: every lifecycle event (`NEW_SETUP`, `ENTRY_FILLED`, `TP_HIT`, `SL_HIT`, milestone alerts) fires exactly once; repeated identical cycles emit nothing.

## Risks / Trade-offs

- Tests mirror scanner-emitted dict shapes; if the scanner's output contract changes, builders must be updated (acceptable — that contract is already the module boundary consumed by `main.py`).
- Some boundary semantics (inclusive zone bounds, strict `>` FVG inequality) are pinned by tests; if the spec interpretation changes, these tests are the intended tripwire.
