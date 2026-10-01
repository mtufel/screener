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

from pathlib import Path

import pytest

_ORIGINAL_TRACKER_INIT = None


def _isolated_tracker_init(scratch_path):
    """Wrap ExtremeTradeTracker.__init__ so a directly-constructed tracker that
    did NOT choose its own path reads/writes a scratch ledger, not the real one.

    An explicit `storage_path` (keyword or positional) is always respected:
    tests that deliberately point at their own file are testing exactly that
    path, and overriding it would break them. Only the implicit default is
    substituted. Every other argument is forwarded untouched, so
    session-filter wiring continues to behave as before.
    """
    import extreme_trade_tracker as ett

    global _ORIGINAL_TRACKER_INIT
    if _ORIGINAL_TRACKER_INIT is None:
        _ORIGINAL_TRACKER_INIT = ett.ExtremeTradeTracker.__init__

    original = _ORIGINAL_TRACKER_INIT

    def __init__(self, *args, **kwargs):
        # Positional arg 0 of the original signature is `storage_path`.
        if "storage_path" not in kwargs and not args:
            kwargs["storage_path"] = str(scratch_path)
        original(self, *args, **kwargs)

    return __init__


@pytest.fixture(autouse=True)
def _isolate_shared_singletons(monkeypatch, tmp_path):
    import extreme_trade_tracker as ett
    from extreme_trade_tracker import extreme_trade_tracker
    from strategy_extreme_fvg import htf_fvg_cache

    # Ledger writes from the global tracker land in a scratch file, never the real one.
    monkeypatch.setattr(
        extreme_trade_tracker, "storage_path", tmp_path / "ledger_test.json"
    )

    # Tests that construct their own `ExtremeTradeTracker()` are isolated the same
    # way. `storage_path` is a __init__ DEFAULT argument, bound once at import, so
    # patching the module-level PERSISTENCE_FILE constant (the pattern used in
    # test_strategy_interface.py) does NOT affect an already-imported class body.
    # Such a tracker therefore loaded the real data/extreme_live_trades_local.json
    # and any locally-recorded trade leaked into those tests' expected-empty
    # counts. Redirect the default to a per-test scratch file instead.
    monkeypatch.setattr(
        ett.ExtremeTradeTracker, "__init__",
        _isolated_tracker_init(tmp_path / "ledger_direct_test.json"),
    )

    # The tracker also round-trips Redis on load/save; never let tests touch
    # the real Upstash instance.
    async def _tracker_no_load(*_args, **_kwargs):
        return False

    async def _tracker_no_save(*_args, **_kwargs):
        return False

    monkeypatch.setattr(extreme_trade_tracker, "load_async", _tracker_no_load)
    monkeypatch.setattr(extreme_trade_tracker, "save_async", _tracker_no_save)

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
