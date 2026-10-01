"""
Tests for the shared Strategy 3 gate pipeline (openspec/changes/refactor-s3-single-gate-pipeline).

The gate pipeline used to exist twice — inline in `backtest_liquidity_sweep_fvg.py` and in
`strategy_liquidity_sweep_fvg.select_gated_ltf_fvg()` — and the copies had already drifted twice
(F-01 entry-session timestamp, F-10 `tp_mode`). These tests pin the shared rules so the two
callers cannot diverge again.

The most important test here is `test_pool_construction_has_no_lookahead`: the backtester's old
private `pools_asof()` helper built liquidity templates from the **entire** series, so the cluster
tolerance was derived from bars up to 45 days in the future. Consolidating on `find_liquidity_pools()`
removed that.
"""

import time
from typing import Any, Dict, List

import pytest

from liquidity import (
    DEFAULT_CLUSTER_TOL_PCT,
    Candle,
    _tolerance,
    build_pool_templates,
    find_liquidity_pools,
)
from session_filter import SessionFilterConfig
from strategy_extreme_fvg import FVG, TouchedAnchor
from strategy_liquidity_sweep_fvg import (
    GATE_ANCHOR_AGE,
    GATE_ENTRY_SESSION,
    GATE_ENTRY_WEEKDAY,
    GATE_GAP_BAND,
    check_gap_band,
    evaluate_fill_gates,
    evaluate_formation_gates,
    select_extreme_gated_fvg,
)

FIVE_MIN_MS = 5 * 60 * 1000


def _mk(ts: int, o: float, h: float, low: float, cl: float) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=low, close=cl, volume=10.0)


def _bull_fvg(gap_pct_target: float = 0.2, formed_at: int = 0) -> FVG:
    """Bullish FVG whose gap_pct is approximately `gap_pct_target`."""
    bottom = 2400.0
    width = bottom * gap_pct_target / 100.0
    top = bottom + width
    c1 = _mk(formed_at, bottom - 2, bottom, bottom - 1, bottom)
    c2 = _mk(formed_at + FIVE_MIN_MS, bottom, top - 1, bottom, top - 1)
    c3 = _mk(formed_at + 2 * FIVE_MIN_MS, top - 1, top + 1, top, top + 0.5)
    return FVG(direction="Bullish", top=top, bottom=bottom, c1=c1, c2=c2, c3=c3,
               formed_at=formed_at, timeframe="5m")


class TestGapBand:
    def test_in_band_is_rejected(self):
        fvg = _bull_fvg(gap_pct_target=0.15)      # inside [0.10, 0.20)
        assert fvg.gap_pct == pytest.approx(0.15, abs=0.01)
        assert check_gap_band(fvg, (0.10, 0.20)) is False

    def test_outside_band_passes(self):
        assert check_gap_band(_bull_fvg(gap_pct_target=0.05), (0.10, 0.20)) is True
        assert check_gap_band(_bull_fvg(gap_pct_target=0.30), (0.10, 0.20)) is True

    def test_none_band_passes(self):
        assert check_gap_band(_bull_fvg(gap_pct_target=0.15), None) is True


class TestFormationGates:
    def test_gap_band_rejection_reports_the_key(self):
        res = evaluate_formation_gates(_bull_fvg(gap_pct_target=0.15), gap_band=(0.10, 0.20))
        assert res.passed is False
        assert res.reason == GATE_GAP_BAND

    def test_gap_band_checked_before_sweep(self):
        """Gap band is cheap and ordering is part of the shared contract."""
        res = evaluate_formation_gates(_bull_fvg(gap_pct_target=0.15),
                                       gap_band=(0.10, 0.20), require_sweep=True)
        assert res.reason == GATE_GAP_BAND, "sweep must not be consulted for an in-band FVG"

    def test_passes_when_no_gates_requested(self):
        res = evaluate_formation_gates(_bull_fvg(), require_sweep=False)
        assert res.passed is True and res.reason == "OK"

    def test_sweep_requires_its_inputs(self):
        with pytest.raises(ValueError):
            evaluate_formation_gates(_bull_fvg(), require_sweep=True)

    def test_ltf_timeframe_is_threaded_through(self):
        """F-06: the sweep pool builder used to hardcode 5m regardless of the
        configured LTF. Assert the timeframe reaches `find_liquidity_pools`."""
        seen = {}

        import strategy_liquidity_sweep_fvg as mod
        real = mod.find_liquidity_pools

        def spy(candles, timeframe="5m", **kw):
            seen["timeframe"] = timeframe
            return []

        mod.find_liquidity_pools = spy
        try:
            evaluate_formation_gates(
                _bull_fvg(), require_sweep=True,
                anchor_first_touch_ts=0, candles_ltf=[_mk(0, 1, 1, 1, 1)],
                ltf_timeframe="15m",
            )
        finally:
            mod.find_liquidity_pools = real

        assert seen["timeframe"] == "15m", "ltf_timeframe did not reach the pool builder"


class TestFillGates:
    def _anchor(self, close_ts: int) -> TouchedAnchor:
        fvg = _bull_fvg(formed_at=close_ts - 3 * FIVE_MIN_MS)
        return TouchedAnchor(fvg=fvg, first_touch_timestamp=close_ts,
                             most_recent_touch_timestamp=close_ts,
                             is_currently_inside=False, touch_timeframe="5m")

    @staticmethod
    def _ts(hour_utc: int, weekday: bool = True) -> int:
        import datetime as _dt
        base = _dt.datetime.now(_dt.timezone.utc).replace(
            hour=hour_utc, minute=0, second=0, microsecond=0)
        while base.weekday() >= 5:
            base += _dt.timedelta(days=1)
        return int(base.timestamp() * 1000)

    @staticmethod
    def _saturday(hour_utc: int = 14) -> int:
        import datetime as _dt
        base = _dt.datetime.now(_dt.timezone.utc).replace(
            hour=hour_utc, minute=0, second=0, microsecond=0)
        while base.weekday() != 5:
            base += _dt.timedelta(days=1)
        return int(base.timestamp() * 1000)

    def test_allows_when_unconstrained(self):
        a = self._anchor(0)
        assert evaluate_fill_gates(self._ts(3), a, entry_session_config=SessionFilterConfig()) is None

    def test_rejects_out_of_session(self):
        a = self._anchor(0)
        cfg = SessionFilterConfig(entry_sessions="NY_KZ")     # 13:00-16:00 UTC
        assert evaluate_fill_gates(self._ts(3), a, entry_session_config=cfg) == GATE_ENTRY_SESSION

    def test_allows_in_session(self):
        a = self._anchor(0)
        cfg = SessionFilterConfig(entry_sessions="NY_KZ")
        assert evaluate_fill_gates(self._ts(14), a, entry_session_config=cfg) is None

    def test_weekend_reports_the_weekday_key(self):
        a = self._anchor(0)
        cfg = SessionFilterConfig(entry_sessions="ALL", entry_weekdays_only=True)
        assert evaluate_fill_gates(self._saturday(14), a,
                                   entry_session_config=cfg) == GATE_ENTRY_WEEKDAY

    def test_anchor_age_dead_zone(self):
        # anchor closed 30h before the fill -> inside the default [24, 48) dead zone
        fill = self._ts(14)
        a = self._anchor(fill - 30 * 3600 * 1000)
        assert evaluate_fill_gates(fill, a, anchor_age_dead_zone=(24.0, 48.0)) == GATE_ANCHOR_AGE

    def test_anchor_age_outside_dead_zone_passes(self):
        fill = self._ts(14)
        a = self._anchor(fill - 10 * 3600 * 1000)            # 10h old
        assert evaluate_fill_gates(fill, a, anchor_age_dead_zone=(24.0, 48.0)) is None

    def test_none_dead_zone_disables_the_guard(self):
        fill = self._ts(14)
        a = self._anchor(fill - 30 * 3600 * 1000)
        assert evaluate_fill_gates(fill, a, anchor_age_dead_zone=None) is None

    def test_session_precedence_over_anchor_age(self):
        """Session is evaluated first, so an out-of-session fill in the dead zone
        reports the session key."""
        fill = self._ts(3)
        a = self._anchor(fill - 30 * 3600 * 1000)
        got = evaluate_fill_gates(fill, a,
                                  entry_session_config=SessionFilterConfig(entry_sessions="NY_KZ"),
                                  anchor_age_dead_zone=(24.0, 48.0))
        assert got == GATE_ENTRY_SESSION


class TestExtremeSelection:
    def _f(self, bottom: float, top: float, formed_at: int) -> FVG:
        fvg = _bull_fvg(formed_at=formed_at)
        fvg.bottom, fvg.top = bottom, top
        return fvg

    def test_bullish_takes_the_deepest(self):
        a = self._f(100.0, 110.0, 0)
        b = self._f(95.0, 105.0, FIVE_MIN_MS)
        assert select_extreme_gated_fvg([(a, None, None), (b, None, None)],
                                        "Bullish")[0] is b

    def test_bearish_takes_the_highest(self):
        a = self._f(100.0, 110.0, 0)
        b = self._f(105.0, 115.0, FIVE_MIN_MS)
        assert select_extreme_gated_fvg([(a, None, None), (b, None, None)],
                                        "Bearish")[0] is b

    def test_ties_break_on_formation_time(self):
        """The two implementations disagreed here: the backtester tie-broke on
        formed_at and the live path did not. The shared rule must too."""
        early = self._f(100.0, 110.0, 0)
        late = self._f(100.0, 110.0, FIVE_MIN_MS)
        assert select_extreme_gated_fvg([(late, None, None), (early, None, None)],
                                        "Bullish")[0] is early
        assert select_extreme_gated_fvg([(early, None, None), (late, None, None)],
                                        "Bullish")[0] is early

    def test_bearish_tie_breaks_on_formation_time(self):
        early = self._f(100.0, 110.0, 0)
        late = self._f(100.0, 110.0, FIVE_MIN_MS)
        assert select_extreme_gated_fvg([(early, None, None), (late, None, None)],
                                        "Bearish")[0] is early

    def test_sweep_metadata_is_carried_through(self):
        f = self._f(100.0, 110.0, 0)
        sentinel = object()
        got = select_extreme_gated_fvg([(f, sentinel, 42)], "Bullish")
        assert got[1] is sentinel and got[2] == 42

    def test_empty_returns_none(self):
        assert select_extreme_gated_fvg([], "Bullish") is None


class TestPoolConstructionNoLookahead:
    """
    Regression guard for the bug the unification exposed.

    The backtester used a private `pools_asof()` that built pool templates from the
    ENTIRE series. `build_pool_templates` derives its cluster tolerance from the mean
    close of the last 50 bars it is given, so for a candidate 45 days back that
    tolerance was computed from future prices — and was ~32% too wide, merging
    619 swing clusters where the correct as-of view had 150.

    The shared pipeline calls `find_liquidity_pools()`, which truncates to the as-of
    time first. An as-of view must therefore be independent of what comes after it.
    """

    @staticmethod
    def _series(n: int, start: int) -> List[Candle]:
        out = []
        price = 2400.0
        for i in range(n):
            drift = 0.02 if (i // 37) % 2 == 0 else -0.02
            price += drift * (1 + (i % 5) * 0.3)
            out.append(_mk(start + i * FIVE_MIN_MS, price, price + 3,
                           price - 3, price + drift))
        return out

    def test_asof_pools_do_not_depend_on_future_bars(self):
        start = int(time.time() * 1000) - 20_000 * FIVE_MIN_MS
        series = self._series(600, start)

        asof_idx = 200
        asof = series[asof_idx].timestamp

        # as-of view computed from the FULL series (must slice internally)
        from_full = find_liquidity_pools(series, timeframe="5m", now_ms=asof)
        # the same view computed from an already-truncated series
        truncated = [c for c in series if c.timestamp <= asof]
        from_truncated = find_liquidity_pools(truncated, timeframe="5m", now_ms=asof)

        key = lambda ps: sorted((p.side, p.kind, round(p.price, 8)) for p in ps)
        assert key(from_full) == key(from_truncated), (
            "as-of pool view changed when later bars were appended -> lookahead")

    def test_full_series_templates_use_future_bars(self):
        """Documents *why* the old helper was wrong: the tolerance is derived from
        the tail of whatever series it is handed."""
        start = int(time.time() * 1000) - 600 * FIVE_MIN_MS
        series = self._series(600, start)
        asof = series[150].timestamp
        truncated = [c for c in series if c.timestamp <= asof]

        def tol_of(candles):
            mid = sum(c.close for c in candles[-50:]) / len(candles[-50:])
            return _tolerance(mid, DEFAULT_CLUSTER_TOL_PCT)

        # The full-series tolerance is contaminated by bars after `asof`.
        assert tol_of(series) != pytest.approx(tol_of(truncated))

    def test_templates_record_a_future_contaminated_tolerance(self):
        """The mechanism, asserted deterministically: the tolerance stored on the
        templates is derived from the tail of whatever series was supplied, so
        building from the full series bakes future prices into an as-of view."""
        start = int(time.time() * 1000) - 600 * FIVE_MIN_MS
        series = self._series(600, start)
        asof = series[150].timestamp
        truncated = [c for c in series if c.timestamp <= asof]

        full_tol = build_pool_templates(series, timeframe="5m").tolerance
        asof_tol = build_pool_templates(truncated, timeframe="5m").tolerance

        assert full_tol != pytest.approx(asof_tol)
        # ...and the as-of view must report the tolerance of its own window.
        visible_mid = sum(c.close for c in truncated[-50:]) / len(truncated[-50:])
        assert asof_tol == pytest.approx(_tolerance(visible_mid, DEFAULT_CLUSTER_TOL_PCT))