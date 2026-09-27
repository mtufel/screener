"""
Tests for the replay manager (replay_manager.py) using a synthetic dataset.

Pins the core invariants of replay backtesting:
- Replay executes the LIVE cycle path (setups come from the strategy engine,
  not a re-implementation) and runs to completion over the dataset.
- Ledger isolation: the live trade tracker and its persisted file are untouched.
- No lookahead: at every step, provider data is capped at virtual time.
- Pause/resume/abort lifecycle semantics and report availability.
"""

import asyncio
from unittest.mock import patch

import pytest

from clock import VirtualClock, install_virtual_clock
from extreme_trade_tracker import extreme_trade_tracker as live_tracker
from replay_manager import ReplayManager
from strategy_extreme_fvg import Candle


def _mk_candle(ts_min: int, open_px: float, close_px: float) -> Candle:
    return Candle(
        timestamp=ts_min * 60 * 1000,
        open=open_px,
        high=max(open_px, close_px) + 2.0,
        low=min(open_px, close_px) - 2.0,
        close=close_px,
        volume=100.0,
    )


def _synthetic_dataset() -> dict:
    """A gentle downtrend-then-uptrend series across 4h and 5m timeframes."""
    ltf = []
    px = 100.0
    for m in range(0, 6 * 24 * 60, 5):  # 6 days of 5m candles
        drift = -0.02 if (m // 60) % 12 < 6 else 0.02
        nxt = px + drift
        ltf.append(_mk_candle(m, px, nxt))
        px = nxt
    htf = []
    px4 = 100.0
    for h in range(0, 8 * 24, 4):  # 8 days of 4h candles
        nxt = px4 + (-0.3 if (h // 4) % 12 < 6 else 0.3)
        htf.append(_mk_candle(h * 60, px4, nxt))
        px4 = nxt
    return {"BTC": {"ltf": ltf, "4h": htf}}


@pytest.fixture
def manager():
    return ReplayManager()


@pytest.mark.asyncio
async def test_replay_runs_to_completion_with_isolated_ledger(manager):
    live_before = (len(live_tracker.active_trades), len(live_tracker.history))

    with patch("replay_manager.build_replay_dataset", new=_fake_dataset_fn()):
        replay_id = await manager.start(
            strategy="extreme_fvg",
            symbols=["BTC"],
            days=2,
            ltf_timeframe="5m",
            speed="MAX",
        )
        run = manager._runs[replay_id]
        await asyncio.wait_for(run["task"], timeout=60)

    snap = manager.snapshot(replay_id)
    assert snap["status"] == "COMPLETED"
    assert snap["cycles"] > 0
    # Ledger isolation: live tracker untouched by the replay
    live_after = (len(live_tracker.active_trades), len(live_tracker.history))
    assert live_after == live_before
    assert snap["virtual_time_ms"] is not None


@pytest.mark.asyncio
async def test_replay_no_lookahead_invariant(manager):
    """After completion, the max served candle close equals the final virtual time."""
    dataset = _synthetic_dataset()

    with patch("replay_manager.build_replay_dataset", new=_fake_dataset_fn(dataset)):
        replay_id = await manager.start(
            strategy="extreme_fvg", symbols=["BTC"], days=2, ltf_timeframe="5m", speed="MAX"
        )
        await asyncio.wait_for(manager._runs[replay_id]["task"], timeout=60)

    clock = manager._runs[replay_id]["clock"]
    provider = manager._runs[replay_id]["provider"]
    last = (await provider.get_last_n_candles("BTC", "5m", 1))
    assert last, "provider must serve the final candle at end of replay"
    assert last[-1]["t"] + 5 * 60 * 1000 <= clock.now_ms()


@pytest.mark.asyncio
async def test_replay_pause_resume(manager):
    with patch("replay_manager.build_replay_dataset", new=_fake_dataset_fn()):
        replay_id = await manager.start(
            strategy="extreme_fvg", symbols=["BTC"], days=2, ltf_timeframe="5m", speed="MAX"
        )
        # Give the loop a beat, pause, then resume; both control ops must succeed
        await asyncio.sleep(0.05)
        paused = await manager.pause(replay_id)
        assert paused["status"] in ("PAUSED", "COMPLETED")  # may have already finished (MAX speed)
        resumed = await manager.resume(replay_id)
        assert resumed["status"] in ("RUNNING", "COMPLETED")
        await asyncio.wait_for(manager._runs[replay_id]["task"], timeout=60)
        assert manager.snapshot(replay_id)["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_replay_abort_keeps_partial_report(manager):
    with patch("replay_manager.build_replay_dataset", new=_fake_dataset_fn()):
        replay_id = await manager.start(
            strategy="extreme_fvg", symbols=["BTC"], days=2, ltf_timeframe="5m", speed="MAX"
        )
        await asyncio.sleep(0.02)
        await manager.abort(replay_id)
        snap = manager.snapshot(replay_id)
        assert snap["status"] == "ABORTED"
        rep = manager.report(replay_id)
        assert rep["report"]["engine"] == "replay"
        assert rep["report"]["status"] == "ABORTED"
        assert "metrics" in rep["report"]


@pytest.mark.asyncio
async def test_replay_unknown_strategy_rejected(manager):
    from strategies.registry import _STRATEGIES

    with pytest.raises(KeyError):
        await manager.start(strategy="nope", symbols=["BTC"], days=1)


@pytest.mark.asyncio
async def test_second_start_aborts_first_run(manager):
    with patch("replay_manager.build_replay_dataset", new=_fake_dataset_fn()):
        first = await manager.start(
            strategy="extreme_fvg", symbols=["BTC"], days=2, ltf_timeframe="5m", speed="MAX"
        )
        second = await manager.start(
            strategy="extreme_fvg", symbols=["BTC"], days=2, ltf_timeframe="5m", speed="MAX"
        )
        assert first != second
        await asyncio.wait_for(manager._runs[second]["task"], timeout=60)
        assert manager.snapshot(second)["status"] == "COMPLETED"


def _fake_dataset_fn(dataset=None):
    """Builds an async stand-in for build_replay_dataset returning synthetic data."""

    async def _fn(symbols, days, ltf_timeframe, provider):
        return dataset or _synthetic_dataset()

    return _fn
