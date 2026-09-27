"""
runner.py -- Orchestrates the full QA harness suite.

Usage:
    from qa_harness.runner import run_all_scenarios, run_scenario
    asyncio.run(run_all_scenarios())
    asyncio.run(run_scenario("lifecycle/Bullish_2R_same_candle_fill_tp"))
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from typing import Any, Callable, Dict, List, Tuple

from qa_harness.scenarios import (
    lifecycle as lifecycle_mod,
    notifications as notifications_mod,
    ws_resilience as ws_resilience_mod,
    formation as formation_mod,
    timing as timing_mod,
    persistence as persistence_mod,
)
from qa_harness.core import AtomicScenarioContext


logger = logging.getLogger("qa_harness.runner")


# Build a single name->factory map for ALL scenarios. Each scenario's factory
# signature differs: lifecycle/notifications/formation/timing take
# (direction, completion_target); ws_resilience/persistence take no args.
def _collect_scenarios() -> Dict[str, Callable]:
    out: Dict[str, Callable] = {}
    for mod in (lifecycle_mod, notifications_mod, formation_mod, timing_mod):
        for name, factory in mod.SCENARIOS.items():
            out[name] = factory
    for name, factory in ws_resilience_mod.SCENARIOS.items():
        out[name] = factory
    for name, factory in persistence_mod.SCENARIOS.items():
        out[name] = factory
    return out


ALL_SCENARIOS: Dict[str, Callable] = _collect_scenarios()


def _parse_scenario_name(name: str) -> Tuple[str, str]:
    """Returns (direction, completion_target) extracted from a scenario name.
    Examples:
      "lifecycle/Bullish_2R_same_candle_fill_tp" -> ("Bullish", "2R")
      "persistence/persistence_reload"          -> ("", "")
    """
    parts = name.split("/")
    if len(parts) != 2:
        return ("", "")
    category, rest = parts
    if category in ("lifecycle", "notifications", "formation", "timing"):
        bits = rest.split("_")
        if len(bits) >= 2:
            return (bits[0], bits[1])
    return ("", "")


async def run_scenario(name: str) -> Any:
    """Run a single scenario by name.

    Returns the SinkRecorder (or raw result for sync scenarios).
    """
    if name not in ALL_SCENARIOS:
        raise KeyError(f"Unknown scenario: {name}. Available: {list(ALL_SCENARIOS.keys())[:10]}...")
    factory = ALL_SCENARIOS[name]
    direction, completion_target = _parse_scenario_name(name)
    if direction and completion_target:
        runner = factory(direction, completion_target)
    else:
        # ws_resilience and persistence scenarios take no direction arg.
        # Some are sync (return result directly), some are async coroutines.
        runner = factory()
    async with AtomicScenarioContext():
        # If runner is a ScenarioRunner (has .run method)
        if hasattr(runner, "run") and callable(getattr(runner, "run")):
            return await runner.run()
        # If runner is a coroutine
        if inspect.iscoroutine(runner):
            return await runner
        # Otherwise runner is a sync callable or sync result
        if callable(runner):
            result = runner()
            # If result is awaitable (coroutine), await it
            if inspect.iscoroutine(result):
                return await result
            return result
        return runner


async def run_all_scenarios(filter_categories: List[str] = None) -> Dict[str, bool]:
    """Run all scenarios sequentially. Returns {name: passed}.

    Each scenario is wrapped in AtomicScenarioContext for clean reset.
    """
    results: Dict[str, bool] = {}
    total = len(ALL_SCENARIOS)
    print(f"\n========== QA HARNESS: {total} scenarios ==========\n")
    for i, (name, factory) in enumerate(ALL_SCENARIOS.items(), 1):
        if filter_categories and not any(name.startswith(f"{c}/") for c in filter_categories):
            results[name] = "skipped"
            continue
        print(f"\n[{i}/{total}] === {name} ===")
        try:
            await run_scenario(name)
            results[name] = True
            print(f"  PASS")
        except Exception as exc:
            results[name] = False
            print(f"  FAIL: {type(exc).__name__}: {exc}")
    return results


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    results = asyncio.run(run_all_scenarios())
    passed = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v == "skipped")
    print(f"\n========== SUMMARY: {passed} passed, {failed} failed, {skipped} skipped ==========")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
