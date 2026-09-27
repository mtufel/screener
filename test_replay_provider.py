"""
Tests for the replay data provider (replay_provider.py).

Pins the no-lookahead contract: candles are only served once fully closed
as-of the (virtual) current time, mids reflect the replay head, and the range
fetch (chart path) is capped at the current time.
"""

import pytest

from clock import VirtualClock, install_virtual_clock
from strategy_extreme_fvg import Candle

from replay_provider import ReplayMarketDataProvider


def _mk_candle(ts_min: int, close: float) -> Candle:
    return Candle(
        timestamp=ts_min * 60 * 1000,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=100.0,
    )


def _provider() -> ReplayMarketDataProvider:
    # 5m candles at t=0,5,10,15,20,25 minutes; 4h candles at t=0 and t=240
    ltf = [_mk_candle(m * 5, 100.0 + m) for m in range(6)]
    htf = [_mk_candle(0, 100.0), _mk_candle(240, 110.0)]
    return ReplayMarketDataProvider({"BTC": {"ltf": ltf, "4h": htf}}, ltf_timeframe="5m")


@pytest.mark.asyncio
async def test_no_lookahead_candle_access():
    p = _provider()
    vc = VirtualClock(start_ms=12 * 60 * 1000)  # 12 min: candles t=0,5 closed; t=10 closes at 15
    with install_virtual_clock(vc):
        out = await p.get_last_n_candles("BTC", "5m", 200)
        closes = [c["c"] for c in out]
        assert closes == [100.0, 101.0]  # strictly closed candles only


@pytest.mark.asyncio
async def test_candles_appear_as_clock_advances():
    p = _provider()
    vc = VirtualClock(start_ms=0)
    with install_virtual_clock(vc):
        assert len(await p.get_last_n_candles("BTC", "5m", 200)) == 0
        vc.advance_to(5 * 60 * 1000)   # candle t=0 closes at 5
        assert len(await p.get_last_n_candles("BTC", "5m", 200)) == 1
        vc.advance_to(26 * 60 * 1000)  # candles t=0..20 closed (t=25 closes at 30)
        assert len(await p.get_last_n_candles("BTC", "5m", 200)) == 5


@pytest.mark.asyncio
async def test_mids_reflect_replay_head():
    p = _provider()
    vc = VirtualClock(start_ms=11 * 60 * 1000)  # candle t=5 closed at 10; t=10 closes at 15
    with install_virtual_clock(vc):
        mids = await p.get_all_mids()
        assert mids == {"BTC": 101.0}


@pytest.mark.asyncio
async def test_range_fetch_capped_at_now():
    p = _provider()
    vc = VirtualClock(start_ms=12 * 60 * 1000)
    with install_virtual_clock(vc):
        out = await p.get_historical_candles_range("BTC", "5m", 0, 999 * 60 * 1000)
        assert [c["c"] for c in out] == [100.0, 101.0]


@pytest.mark.asyncio
async def test_4h_timeframe_served_separately():
    p = _provider()
    vc = VirtualClock(start_ms=250 * 60 * 1000)  # 4h candle t=0 closed at 240; t=240 closes at 480
    with install_virtual_clock(vc):
        out = await p.get_last_n_candles("BTC", "4h", 10)
        assert len(out) == 1
        vc.advance_to(480 * 60 * 1000)
        out2 = await p.get_last_n_candles("BTC", "4h", 10)
        assert len(out2) == 2


@pytest.mark.asyncio
async def test_next_close_boundary():
    p = _provider()
    vc = VirtualClock(start_ms=11 * 60 * 1000)
    with install_virtual_clock(vc):
        assert p.next_close_boundary("5m") == 15 * 60 * 1000
        vc.advance_to(30 * 60 * 1000)
        assert p.next_close_boundary("5m") is None


@pytest.mark.asyncio
async def test_unknown_symbol_serves_empty():
    p = _provider()
    with install_virtual_clock(VirtualClock(start_ms=10**13)):
        assert await p.get_last_n_candles("NOPE", "5m", 10) == []
        assert await p.get_all_mids() == {"BTC": 105.0}
