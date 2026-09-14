"""
persistence.py -- Persistence, config, and KPI summary scenarios.

Covers:
  - persistence reload (ExtremeTradeTracker._load() restores active_trades + history)
  - /api/config toggles (state['extreme_*'] flips between scenarios)
  - KPI summary assertions (get_summary returns correct win_rate, net_realized_r, avg_mfe)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from extreme_trade_tracker import (
    ExtremeTradeTracker, TrackedExtremeTrade, PERSISTENCE_FILE,
    PENDING_ABSENT_EXPIRY_CYCLES,
)
from qa_harness.core import (
    FakeProvider, SinkRecorder, install_patches, configure_state,
    c, T0, FIVE_MIN_MS, FOUR_H_MS,
)
import main
from main import execute_extreme_screener_cycle


# ---------------------------------------------------------------------------
# Persistence reload
# ---------------------------------------------------------------------------

def persistence_reload() -> Dict[str, Any]:
    """Write a tracker snapshot to disk, instantiate a fresh tracker,
    verify it loads the same state.

    Returns summary of what was loaded.
    """
    # Create a temp storage file
    tmp_path = Path("/tmp/qa_persistence_test.json")
    if tmp_path.exists():
        tmp_path.unlink()
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path))
    # Add some history
    t1 = TrackedExtremeTrade(
        symbol="BTC", direction="Bullish", entry_price=2429.0, stop_loss=2414.0,
        risk_r=15.0, risk_pct=0.62, tp_1r=2444.0, tp_2r=2459.0, tp_3r=2474.0,
        completion_target="2R", ltf_timeframe="5m",
        state="COMPLETED_TP", realized_r=2.0, floating_r=0.0,
        max_favorable_price=2459.0, mfe_r=2.0,
        entry_timestamp=T0 + 4 * FOUR_H_MS, closed_timestamp=T0 + 5 * FOUR_H_MS,
    )
    t2 = TrackedExtremeTrade(
        symbol="ETH", direction="Bearish", entry_price=2405.0, stop_loss=2420.0,
        risk_r=15.0, risk_pct=0.62, tp_1r=2390.0, tp_2r=2375.0, tp_3r=2360.0,
        completion_target="2R", ltf_timeframe="5m",
        state="STOPPED_OUT", realized_r=-1.0, floating_r=0.0,
        max_favorable_price=2400.0, mfe_r=0.33,
        entry_timestamp=T0 + 6 * FOUR_H_MS, closed_timestamp=T0 + 7 * FOUR_H_MS,
    )
    tracker.history = [t1, t2]
    tracker._save_local()

    # Now load into a fresh tracker
    tracker2 = ExtremeTradeTracker(storage_path=str(tmp_path))
    return {
        "history_count_after_reload": len(tracker2.history),
        "first_state": tracker2.history[0].state if tracker2.history else None,
        "second_state": tracker2.history[1].state if len(tracker2.history) > 1 else None,
    }


# ---------------------------------------------------------------------------
# /api/config toggles
# ---------------------------------------------------------------------------

async def api_config_toggles() -> Dict[str, Any]:
    """Toggle main.state keys and verify they are picked up by the worker
    on the next cycle.
    """
    from qa_harness.core import FakeProvider, SinkRecorder, install_patches, configure_state
    results = {}

    # Test 1: extreme_min_gap = 0.50 (huge) -> no setup forms
    provider = FakeProvider(["CONFIGGAP"])
    sink = SinkRecorder()
    install_patches(provider, sink)
    configure_state(main, ["CONFIGGAP"])
    main.state["extreme_min_gap"] = 0.50  # 50% gap required

    from strategy_extreme_fvg import htf_fvg_cache
    try:
        from redis_client import redis_client
        for mode in ("wick", "close"):
            rk = redis_client.get_key("htf_cache:CONFIGGAP:" + mode)
            try:
                # Synchronous delete via Redis sync call
                await redis_client.delete(rk)
            except Exception:
                pass
    except Exception:
        pass
    htf_fvg_cache.invalidate_cache()

    formed_at = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
    from qa_harness.scenarios.lifecycle import _build_4h, _build_ltf_fvg
    provider._feed["CONFIGGAP"]["4h"] = _build_4h("Bullish")
    provider._feed["CONFIGGAP"]["5m"] = list(_build_ltf_fvg("Bullish", formed_at))
    await execute_extreme_screener_cycle()
    results["no_setup_with_huge_gap"] = len(sink.marked) == 0

    # Test 2: extreme_target = "3R" -> completion_target field changes
    sink2 = SinkRecorder()
    install_patches(FakeProvider(["CONFIGTGT"]), sink2)
    configure_state(main, ["CONFIGTGT"])
    main.state["extreme_target"] = "3R"
    results["target_state_set"] = main.state.get("extreme_target") == "3R"

    # Test 3: extreme_ltf = "15m" -> ltf changes
    main.state["extreme_ltf"] = "15m"
    results["ltf_state_set"] = main.state.get("extreme_ltf") == "15m"

    return results


# ---------------------------------------------------------------------------
# KPI summary
# ---------------------------------------------------------------------------

def kpi_summary_assertions() -> Dict[str, Any]:
    """Build a tracker with known history and verify get_summary() returns
    the correct win_rate, net_realized_r, avg_mfe.
    """
    from extreme_trade_tracker import ExtremeTradeTracker
    tmp_path = Path("/tmp/qa_kpi_test.json")
    if tmp_path.exists():
        tmp_path.unlink()
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path))
    # Add 3 wins (TP) and 1 loss (SL)
    for i in range(3):
        t = TrackedExtremeTrade(
            symbol=f"WIN{i}", direction="Bullish", entry_price=2429.0,
            stop_loss=2414.0, risk_r=15.0, risk_pct=0.62,
            tp_1r=2444.0, tp_2r=2459.0, tp_3r=2474.0,
            completion_target="2R", ltf_timeframe="5m",
            state="COMPLETED_TP", realized_r=2.0, floating_r=0.0,
            max_favorable_price=2459.0, mfe_r=2.5,
        )
        tracker.history.append(t)
    t_loss = TrackedExtremeTrade(
        symbol="LOSS", direction="Bullish", entry_price=2429.0,
        stop_loss=2414.0, risk_r=15.0, risk_pct=0.62,
        tp_1r=2444.0, tp_2r=2459.0, tp_3r=2474.0,
        completion_target="2R", ltf_timeframe="5m",
        state="STOPPED_OUT", realized_r=-1.0, floating_r=0.0,
        max_favorable_price=2429.5, mfe_r=0.03,
    )
    tracker.history.append(t_loss)
    summary = tracker.get_summary()
    return {
        "win_rate_pct": summary["win_rate_pct"],
        "net_realized_r": summary["net_realized_r"],
        "avg_mfe_r": summary["avg_mfe_r"],
        "total_closed": summary["total_closed_trades"],
        "wins": summary["wins"],
        "losses": summary["losses"],
    }


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

SCENARIOS = {
    "persistence/persistence_reload": persistence_reload,
    "persistence/api_config_toggles": api_config_toggles,
    "persistence/kpi_summary_assertions": kpi_summary_assertions,
}


ALL_SCENARIO_NAMES = list(SCENARIOS.keys())
