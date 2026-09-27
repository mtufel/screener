# Spec: Core Python Structure Refactor

Requirements:
- Shared IST / TIMEFRAME_MS in one module (models.py), re-exported
- Candles and FVG types unified (default gap_pct if not used)
- Tracker process_live_setups split into private methods; event names/keys frozen
- Main.py split: cycle extracted to screener_extreme.py, re-exported for qa_harness
- No change to session from_legacy precedence; SL before TP kept
