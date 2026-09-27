"""
qa_harness — Modular QA harness for the Crypto FVG Screener (Strategy 2 / Extreme LTF).

Replaces the single-file `qa_live_sim.py` with a composable package implementing the
full non-Bug-2 scenario matrix across six orthogonal dimensions:
  - lifecycle       : same-candle fill + SL/TP across bull/bear + completion targets
  - notifications   : single-fire anti-spam, Redis dedup across restart, telegram-failure resilience
  - ws_resilience   : WS drop→REST fallback, reconnect/resubscribe (Bug 5), Binance partial-frame merge (Bug 1), CCXT no-frame fallback (Bug 4)
  - formation       : formation negatives (no 4H touch, open-candle exclusion, min_gap_pct, session/weekday, best-of-FVGs)
  - timing          : open-candle overwrite, anchor-breach invalidation, absent expiry, pending refresh, curr_px exit + latency (excl. Bug 2 wick)
  - persistence     : persistence reload, /api/config toggles, KPI summary assertions

Each scenario module exports a `SCENARIOS` dict mapping name -> scenario factory
with signature `Scenario(direction, completion_target) -> ScenarioRunner`.

Usage:
    from qa_harness.runner import run_all_scenarios
    run_all_scenarios()
"""
__version__ = "0.1.0"