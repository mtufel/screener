"""
Test-isolation guards (root conftest).

The module-level singletons `extreme_trade_tracker` and `htf_fvg_cache` are shared
global state. Without these guards, pytest runs:

  1. Overwrite the REAL local trade ledger (data/extreme_live_trades_local.json)
     whenever a test mutates the global tracker and triggers a save.
  2. Bleed HTF-cache state between tests through the process-wide cache singleton.

Every test runs with the tracker pointed at a throwaway file and the HTF cache
cleared; the real ledger path is restored automatically after each test.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_shared_singletons(monkeypatch):
    from extreme_trade_tracker import extreme_trade_tracker
    from strategy_extreme_fvg import htf_fvg_cache

    # Ledger writes from the global tracker land in a scratch file, never the real one.
    monkeypatch.setattr(
        extreme_trade_tracker, "storage_path", "data/extreme_live_trades_test.json"
    )

    # Fresh HTF cache per test: no cross-test FVG/anchor bleed via the singleton.
    htf_fvg_cache.invalidate_cache()

    # The cache persists to Upstash Redis when .env credentials are present.
    # Without this guard, tests can both READ stale real-world caches (silently
    # breaking candle-based tests) and WRITE fake candle data to the live store
    # via the fire-and-forget save in get_active_4h_fvgs_for_symbol.
    async def _no_load(*_args, **_kwargs):
        return False

    async def _no_save(*_args, **_kwargs):
        return False

    monkeypatch.setattr(htf_fvg_cache, "load_from_redis", _no_load)
    monkeypatch.setattr(htf_fvg_cache, "save_to_redis", _no_save)
    yield
