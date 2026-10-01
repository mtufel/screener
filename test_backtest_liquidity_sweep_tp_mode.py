"""
Tests for the Strategy 3 backtester's take-profit resolution.

Regression cover for the `tp_mode` dead-parameter defect
(openspec/changes/fix-s3-backtest-tp-mode-param): the backtester accepted
`tp_mode` and echoed it on the report but always resolved liquidity-first,
because it called the pure `liquidity_take_profit()` helper directly instead of
the live engine's `apply_liquidity_tp()`, which is the only place `tp_mode` is
actually honoured.

The contract these tests pin down:
  - `tp_mode="FIXED_R"`  -> TP sits at entry +/- fallback_target_r * risk_r, no
    pool attached, every trade tagged FIXED_R.
  - `tp_mode="LIQUIDITY"` -> TP is the nearest qualifying opposing pool when one
    exists, and the realised R is therefore NOT pinned to fallback_target_r.
  - The two modes are not interchangeable. Pre-fix they returned byte-identical
    trade lists, which is exactly what these tests forbid.
"""

import time
from typing import Any, Dict, List

import pytest

from backtest_liquidity_sweep_fvg import run_liquidity_sweep_backtest

FIVE_MIN_MS = 5 * 60 * 1000
FOUR_H_MS = 4 * 3600 * 1000

# 4H bullish FVG: c1 high 2402, c3 low 2415 -> zone [2402, 2415].
FOUR_H_ZONE = (2402.0, 2415.0)
# 5m bullish FVG: c1 high 2418, c3 low 2429 -> zone [2418, 2429].
LTF_BOTTOM, LTF_TOP = 2418.0, 2429.0
LTF_ENTRY = LTF_TOP          # bullish entry is the zone top
LTF_SL = 2414.0              # min low across the 3 formation candles
LTF_RISK = LTF_ENTRY - LTF_SL          # 15.0


def _c(ts: int, o: float, h: float, low: float, cl: float) -> Dict[str, Any]:
    return {"t": ts, "o": o, "h": h, "l": low, "c": cl, "v": 10.0}


def _fixture() -> tuple:
    """4H + 5m series that yields exactly one bullish S3 trade.

    Layout (all timestamps derived from real `now` so the backtester's closed
    -candle filter keeps them):
      4H  : 4 candles forming a bullish FVG, last one closes the zone.
      5m  : warm-up above the 4H zone, a structural high (liquidity pool) well
            above entry, then the 3 FVG candles (c1 also serves as the first
            touch of the 4H zone), then a fill candle, then an advance that
            runs far enough for either target to resolve.
    """
    now = int(time.time() * 1000)
    t0 = (now - 20 * FOUR_H_MS) // FOUR_H_MS * FOUR_H_MS

    four_h = [
        _c(t0, 2400.0, 2402.0, 2390.0, 2395.0),
        _c(t0 + FOUR_H_MS, 2395.0, 2450.0, 2394.0, 2445.0),
        _c(t0 + 2 * FOUR_H_MS, 2445.0, 2460.0, 2415.0, 2455.0),
        _c(t0 + 3 * FOUR_H_MS, 2455.0, 2470.0, 2410.0, 2465.0),
    ]

    # 5m series starts after the 4H zone has closed (t0 + 4 * FOUR_H_MS).
    base = t0 + 4 * FOUR_H_MS + FIVE_MIN_MS
    ltf: List[Dict[str, Any]] = []

    # Warm-up: hold above the 4H zone so it is not touched early. The repeated
    # push to ~2459/2460 builds an equal-highs liquidity pool above entry.
    for i in range(6):
        ltf.append(_c(base + i * FIVE_MIN_MS, 2440.0, 2459.0, 2438.0, 2455.0))
    for i in range(6, 10):
        ltf.append(_c(base + i * FIVE_MIN_MS, 2450.0, 2460.0, 2445.0, 2452.0))

    # The 3 FVG candles (geometry proven by qa_harness.core.ltf_formation):
    # c1.high 2418 / c3.low 2429 -> gap [2418, 2429]. c1 (low 2414) also serves
    # as the first touch of the 4H zone.
    f = base + 10 * FIVE_MIN_MS
    ltf.append(_c(f, 2412.0, 2418.0, 2414.0, 2416.0))
    ltf.append(_c(f + FIVE_MIN_MS, 2416.0, 2427.0, 2416.0, 2426.0))
    ltf.append(_c(f + 2 * FIVE_MIN_MS, 2426.0, 2431.0, 2429.0, 2430.5))

    # Fill: dips to entry (2429) without reaching the stop (2414).
    ltf.append(_c(f + 3 * FIVE_MIN_MS, 2430.0, 2432.0, 2420.0, 2424.0))
    # Advance: rally through both the 2R fixed target (2459) and the liquidity
    # pool just in front of it, so the two modes resolve to different R.
    ltf.append(_c(f + 4 * FIVE_MIN_MS, 2424.0, 2450.0, 2422.0, 2448.0))
    ltf.append(_c(f + 5 * FIVE_MIN_MS, 2448.0, 2462.0, 2446.0, 2460.0))
    ltf.append(_c(f + 6 * FIVE_MIN_MS, 2460.0, 2464.0, 2458.0, 2462.0))

    return four_h, ltf


class _StubProvider:
    """Serves the fixture regardless of the requested symbol/range."""

    def __init__(self, four_h: List[Dict[str, Any]], ltf: List[Dict[str, Any]]):
        self._four_h = four_h
        self._ltf = ltf

    async def get_historical_candles_range(self, symbol, timeframe, start_ms, end_ms):
        return list(self._four_h if timeframe == "4h" else self._ltf)


def _run(**kwargs):
    four_h, ltf = _fixture()
    kwargs.setdefault("require_sweep", False)   # isolate the TP-mode axis
    kwargs.setdefault("anchor_age_guard", False)
    kwargs.setdefault("entry_sessions", None)
    return _StubProvider(four_h, ltf), kwargs


@pytest.mark.asyncio
async def test_fixture_actually_produces_a_trade():
    """Guard: if the synthetic feed stops yielding a trade, every other
    assertion below would vacuously pass."""
    provider, kw = _run(tp_mode="FIXED_R")
    rep = await run_liquidity_sweep_backtest(
        symbol="TEST", days=20, ltf_timeframe="5m", use_close_invalidation=True,
        min_gap_pct=0.05, client=provider, **kw)
    assert rep.total_trades >= 1, "synthetic fixture produced no trades"


@pytest.mark.asyncio
async def test_fixed_r_places_tp_at_fallback_multiple():
    """FIXED_R must put the TP at entry +/- fallback_target_r * risk exactly,
    so a hit realises precisely fallback_target_r."""
    provider, kw = _run(tp_mode="FIXED_R", fallback_target_r=2.0)
    rep = await run_liquidity_sweep_backtest(
        symbol="TEST", days=20, ltf_timeframe="5m", use_close_invalidation=True,
        min_gap_pct=0.05, client=provider, **kw)

    assert rep.total_trades >= 1
    for t in rep.trades:
        assert t.tp_mode == "FIXED_R", f"trade resolved as {t.tp_mode}, expected FIXED_R"
        assert t.tp_pool_kind is None
        assert t.tp_pool_price is None
        # Either stopped out, or hit the fixed multiple exactly.
        assert t.realized_r in (-1.0, 2.0), f"realized_r={t.realized_r}"


@pytest.mark.asyncio
async def test_fixed_r_respects_custom_fallback_multiple():
    """fallback_target_r must actually move the target, not be ignored."""
    provider, kw = _run(tp_mode="FIXED_R", fallback_target_r=3.0)
    rep = await run_liquidity_sweep_backtest(
        symbol="TEST", days=20, ltf_timeframe="5m", use_close_invalidation=True,
        min_gap_pct=0.05, client=provider, **kw)

    assert rep.total_trades >= 1
    for t in rep.trades:
        assert t.realized_r in (-1.0, 3.0), f"realized_r={t.realized_r}"


@pytest.mark.asyncio
async def test_report_counts_fixed_tp():
    """A FIXED_R run must be fully accounted for by fixed_tp_count."""
    provider, kw = _run(tp_mode="FIXED_R")
    rep = await run_liquidity_sweep_backtest(
        symbol="TEST", days=20, ltf_timeframe="5m", use_close_invalidation=True,
        min_gap_pct=0.05, client=provider, **kw)

    assert rep.total_trades >= 1
    assert rep.fixed_tp_count == rep.total_trades
    assert rep.liquidity_tp_count == 0


@pytest.mark.asyncio
async def test_liquidity_mode_still_resolves_pools():
    """Regression guard: LIQUIDITY keeps its liquidity-first behaviour."""
    provider, kw = _run(tp_mode="LIQUIDITY", min_rr_for_liquidity=1.5)
    rep = await run_liquidity_sweep_backtest(
        symbol="TEST", days=20, ltf_timeframe="5m", use_close_invalidation=True,
        min_gap_pct=0.05, client=provider, **kw)

    assert rep.total_trades >= 1
    assert any(t.tp_pool_kind is not None for t in rep.trades), \
        "LIQUIDITY mode attached no pool -- liquidity-first path regressed"
    assert rep.liquidity_tp_count >= 1


@pytest.mark.asyncio
async def test_the_two_modes_are_not_interchangeable():
    """The defect itself: FIXED_R and LIQUIDITY produced identical output.
    They must differ in realised R."""
    provider_a, kw_a = _run(tp_mode="FIXED_R")
    fixed = await run_liquidity_sweep_backtest(
        symbol="TEST", days=20, ltf_timeframe="5m", use_close_invalidation=True,
        min_gap_pct=0.05, client=provider_a, **kw_a)

    provider_b, kw_b = _run(tp_mode="LIQUIDITY")
    liq = await run_liquidity_sweep_backtest(
        symbol="TEST", days=20, ltf_timeframe="5m", use_close_invalidation=True,
        min_gap_pct=0.05, client=provider_b, **kw_b)

    fixed_r = [round(t.realized_r, 6) for t in fixed.trades]
    liq_r = [round(t.realized_r, 6) for t in liq.trades]
    assert fixed_r != liq_r, (
        "tp_mode had no effect -- FIXED_R and LIQUIDITY are identical "
        f"(both {fixed_r})")