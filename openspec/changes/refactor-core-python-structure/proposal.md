# Change: Refactor core Python structure (Phase 0-2)

Why: Strategy 1 removed; remaining Strategy 2 engine, tracker, and main need structural cleanup without behavior change.

What:
- Phase 0: hygiene (constants/imports, drop unused imports, lookup_mid at top)
- Phase 1: shared models (models.py — union fields; re-exports so imports stay working)
- Phase 2: tracker method extraction (process_live_setups → _ingest / _monitor_pending / _monitor_active — frozen event tuples)

Constraints: No public API change; SOT tests untouched; SL/TP order preserved; session resolution frozen.
Co-Authored-By: Claude Code <noreply@anthropic.com>
Co-Authored-By: Claude Code <noreply@anthropic.com>
