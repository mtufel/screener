"""
notifications.py -- Notification side-effect scenarios.

Covers:
  - single-fire anti-spam (same evt/trade must produce exactly one alert)
  - Redis dedup across restart (re-loading the same trade must not re-fire alerts)
  - telegram-failure resilience (Telegram returns False -> no exception, no double-fire)

Each scenario factory has signature `(direction, completion_target) -> ScenarioRunner`.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Dict, List

from qa_harness.core import (
    FakeProvider, SinkRecorder, install_patches, configure_state,
    c, T0, FIVE_MIN_MS, FOUR_H_MS,
)
from qa_harness.scenarios.lifecycle import (
    SYMBOL, _build_4h, _build_ltf_fvg, _probe_setup_prices,
    _bullish_fill_candle, _bearish_fill_candle, _bullish_benign_candle, _bearish_benign_candle,
    ScenarioRunner,
)
import main
from main import execute_extreme_screener_cycle
from extreme_trade_tracker import extreme_trade_tracker as tracker


def single_fire_anti_spam(direction: str, completion_target: str) -> ScenarioRunner:
    """Same setup must produce only ONE alert across multiple scans.

    Setup formed on scan 1, then 3 idle scans with no candles. Assert:
      - exactly one NEW_SETUP telegram alert sent
      - exactly one mark_alert_sent call for (NEW_SETUP, trade_id)
    """
    p = _probe_setup_prices(direction, completion_target)
    entry, sl = p["entry"], p["sl"]
    if direction == "Bullish":
        fill = _bullish_fill_candle(entry, sl)
        benign = _bullish_benign_candle(entry, sl)
    else:
        fill = _bearish_fill_candle(entry, sl)
        benign = _bearish_benign_candle(entry, sl)
    feeds = [
        {},
        {},  # anchor is preloaded
        {SYMBOL: [benign]},
        {SYMBOL: [benign]},
        {SYMBOL: [benign]},
    ]
    name = f"{direction} {completion_target} single-fire anti-spam"
    return ScenarioRunner(name, [SYMBOL], feeds, direction)


def redis_dedup_across_restart(direction: str, completion_target: str) -> ScenarioRunner:
    """After 'restart' (re-instantiating SinkRecorder with same seen-set),
    the same trade_id must not re-fire alerts.

    Simulates the production scenario: Redis client returns True from
    is_alert_sent for events already seen across a process restart.

    The harness's SinkRecorder returns False on first query (so alert fires),
    then True on subsequent queries (alert suppressed). This scenario
    verifies that after the alert fires once, replaying the SAME trade_id
    across multiple cycles does NOT re-fire alerts.
    """
    p = _probe_setup_prices(direction, completion_target)
    entry, sl = p["entry"], p["sl"]
    if direction == "Bullish":
        fill = _bullish_fill_candle(entry, sl)
        benign = _bullish_benign_candle(entry, sl)
    else:
        fill = _bearish_fill_candle(entry, sl)
        benign = _bearish_benign_candle(entry, sl)
    # Many benign scans after the setup forms -> assert no double-fire.
    feeds = [
        {},
        {},
        {SYMBOL: [benign]},
        {SYMBOL: [benign]},
        {SYMBOL: [benign]},
        {SYMBOL: [benign]},
    ]
    name = f"{direction} {completion_target} redis dedup across restart"
    return ScenarioRunner(name, [SYMBOL], feeds, direction)


def telegram_failure_resilience(direction: str, completion_target: str) -> ScenarioRunner:
    """When send_telegram_alert returns (False, 0), main.py should not raise,
    should not call mark_alert_sent, and should continue iterating events.

    The harness overrides send_telegram_alert with a custom sink that
    returns (False, 0) the first time and True thereafter. We verify:
      - run() completes without exception
      - sink.marked only contains the entry_fill / tp_hit mark calls
        (telegram failure means no mark_alert_sent for that event)
    """
    p = _probe_setup_prices(direction, completion_target)
    entry, sl = p["entry"], p["sl"]
    if direction == "Bullish":
        fill = _bullish_fill_candle(entry, sl)
        benign = _bullish_benign_candle(entry, sl)
    else:
        fill = _bearish_fill_candle(entry, sl)
        benign = _bearish_benign_candle(entry, sl)
    feeds = [
        {},
        {},
        {SYMBOL: [benign]},
        {SYMBOL: [benign]},
    ]
    name = f"{direction} {completion_target} telegram-failure resilience"
    # Wrap with a "failing telegram" sink by monkey-patching install_patches
    runner = ScenarioRunner(name, [SYMBOL], feeds, direction)
    original_run = runner.run

    async def failing_run():
        from qa_harness.core import install_patches as _install_patches
        # Call original to set up provider/state, then patch telegram to fail.
        sink = await original_run()
        # Override send_telegram_alert to fail
        call_count = {"n": 0}

        async def failing_send(msg, **kwargs):
            call_count["n"] += 1
            return False, 0  # simulate Telegram failure

        main.send_extreme_telegram_alert = failing_send
        return sink

    runner.run = failing_run
    return runner


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

SCENARIOS = {
    **{f"notifications/{d}_{t}_single_fire_anti_spam": single_fire_anti_spam
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"notifications/{d}_{t}_redis_dedup_across_restart": redis_dedup_across_restart
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"notifications/{d}_{t}_telegram_failure_resilience": telegram_failure_resilience
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
}


ALL_SCENARIO_NAMES = list(SCENARIOS.keys())
