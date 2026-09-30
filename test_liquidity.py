"""
Unit tests for liquidity.py — pool detection, sweep semantics, freshness,
liquidity-first TP, gap-band parsing, and anchor-age guard.

All fixtures use synthetic candles with explicit timestamps so every assertion
is deterministic and free of network or wall-clock dependence.
"""

from liquidity import (
    Candle,
    LiquidityPool,
    anchor_age_guard_ok,
    detect_sweep,
    detect_swing_points,
    find_liquidity_pools,
    find_pool_sweep,
    has_fresh_sweep,
    liquidity_take_profit,
    nearest_opposing_pool,
    parse_gap_band,
    pools_for_direction,
)
from strategy_extreme_fvg import Candle as EngineCandle

TS_BASE = 1_700_000_000_000  # arbitrary fixed epoch ms
FIVE_MIN = 5 * 60 * 1000


def mk(i, o, h, l, c):
    """Builds a 5m candle at slot i with fixed OHLC."""
    return Candle(timestamp=TS_BASE + i * FIVE_MIN, open=o, high=h, low=l, close=c, volume=1000.0)


def equal_highs_series():
    """
    10 candles with swing highs at exactly 12.0 on bars 2 and 7 (both
    fractal-confirmed within the series) → one EQUAL_HIGHS pool.
    """
    return [
        mk(0, 11.0, 11.5, 10.5, 11.0),
        mk(1, 11.0, 11.8, 10.8, 11.5),
        mk(2, 11.5, 12.0, 11.2, 11.6),
        mk(3, 11.6, 11.7, 11.0, 11.2),
        mk(4, 11.2, 11.5, 10.9, 11.0),
        mk(5, 11.0, 11.4, 10.8, 11.1),
        mk(6, 11.1, 11.9, 10.9, 11.5),
        mk(7, 11.5, 12.0, 11.3, 11.7),
        mk(8, 11.7, 11.6, 11.1, 11.2),
        mk(9, 11.2, 11.4, 10.7, 10.9),
    ]


class TestSwingDetection:
    def test_finds_confirmed_swing_high(self):
        swings = detect_swing_points(equal_highs_series())
        highs = [s for s in swings if s.kind == "HIGH"]
        assert [s.price for s in highs] == [12.0, 12.0]
        assert highs[0].index == 2 and highs[1].index == 7

    def test_flat_series_has_no_swings(self):
        flat = [mk(i, 10.0, 10.1, 9.9, 10.0) for i in range(12)]
        assert detect_swing_points(flat) == []

    def test_requires_confirmation_bars(self):
        # Only 4 candles: no bar has k=2 bars on both sides.
        short = equal_highs_series()[:4]
        assert detect_swing_points(short) == []


class TestPoolDetection:
    def test_two_touches_form_equal_highs_pool(self):
        pools = find_liquidity_pools(equal_highs_series())
        eq = [p for p in pools if p.kind == "EQUAL_HIGHS"]
        assert len(eq) == 1
        assert eq[0].price == 12.0
        assert eq[0].touches == 2
        assert eq[0].side == "ABOVE"
        assert eq[0].formed_at == TS_BASE + 2 * FIVE_MIN

    def test_single_touch_is_minor_and_excludable(self):
        pools_all = find_liquidity_pools(equal_highs_series())
        assert any(p.kind == "MINOR" for p in pools_all)
        pools_major = find_liquidity_pools(equal_highs_series(), include_minor=False)
        assert all(p.kind != "MINOR" for p in pools_major)
        assert any(p.kind == "EQUAL_HIGHS" for p in pools_major)

    def test_no_lookahead_pool_needs_confirmed_touches(self):
        # At bar 4 only the first 12.0 swing is confirmed → no EQUAL pool yet.
        cutoff = TS_BASE + 4 * FIVE_MIN
        pools = find_liquidity_pools(equal_highs_series(), now_ms=cutoff)
        assert all(p.kind != "EQUAL_HIGHS" for p in pools)
        # Full series (now_ms=None) yields the EQUAL_HIGHS pool.
        pools_full = find_liquidity_pools(equal_highs_series())
        assert any(p.kind == "EQUAL_HIGHS" for p in pools_full)

    def test_pdh_pdl_from_prior_utc_day(self):
        day = 24 * 3600 * 1000
        c1 = [
            mk(0, 11.0, 11.5, 10.5, 11.0),
            mk(1, 11.0, 12.5, 10.2, 12.0),  # day-1 high 12.5 / low 10.2
            mk(2, 12.0, 12.2, 11.8, 12.0),
        ]
        c2 = [mk(288 + i, 12.0, 12.3, 11.9, 12.1) for i in range(3)]  # next UTC day
        pools = find_liquidity_pools(c1 + c2)
        pdh = [p for p in pools if p.kind == "PDH"]
        pdl = [p for p in pools if p.kind == "PDL"]
        assert len(pdh) == 1 and pdh[0].price == 12.5
        assert len(pdl) == 1 and pdl[0].price == 10.2

    def test_empty_and_tiny_inputs(self):
        assert find_liquidity_pools([]) == []
        assert find_liquidity_pools([mk(0, 1, 1.1, 0.9, 1.0)]) == []


class TestSweepDetection:
    def make_above_pool(self):
        return LiquidityPool(kind="EQUAL_HIGHS", price=12.0, side="ABOVE",
                             formed_at=TS_BASE, touches=2, strength=3.0)

    def make_below_pool(self):
        return LiquidityPool(kind="EQUAL_LOWS", price=10.0, side="BELOW",
                             formed_at=TS_BASE, touches=2, strength=3.0)

    def test_wick_through_close_back_is_sweep(self):
        c = mk(10, 11.8, 12.3, 11.7, 11.9)  # wick above 12.0, close below
        assert detect_sweep(c, self.make_above_pool()) is True

    def test_close_through_is_break_not_sweep(self):
        c = mk(10, 11.8, 12.3, 11.7, 12.1)  # closed above the level
        assert detect_sweep(c, self.make_above_pool()) is False

    def test_no_pierce_is_not_sweep(self):
        c = mk(10, 11.8, 11.99, 11.7, 11.9)
        assert detect_sweep(c, self.make_above_pool()) is False

    def test_below_pool_sweep(self):
        c = mk(10, 10.2, 10.3, 9.7, 10.1)  # wick below 10.0, close back above
        assert detect_sweep(c, self.make_below_pool()) is True

    def test_find_pool_sweep_returns_first_hit_in_window(self):
        pool = self.make_above_pool()
        candles = [
            mk(10, 11.8, 11.9, 11.7, 11.8),   # no sweep
            mk(11, 11.8, 12.3, 11.7, 11.9),   # sweep here
            mk(12, 11.9, 12.4, 11.8, 11.9),   # later sweep ignored
        ]
        ts = find_pool_sweep(candles, pool, from_ts=candles[0].timestamp)
        assert ts == candles[1].timestamp
        assert find_pool_sweep(candles, pool, from_ts=candles[2].timestamp) == candles[2].timestamp


class TestFreshSweep:
    def make_pools(self):
        return [
            LiquidityPool(kind="EQUAL_LOWS", price=10.0, side="BELOW", formed_at=TS_BASE, touches=2, strength=3.0),
            LiquidityPool(kind="EQUAL_HIGHS", price=12.0, side="ABOVE", formed_at=TS_BASE, touches=2, strength=3.0),
        ]

    def test_fresh_opposing_sweep_detected(self):
        pools = self.make_pools()
        to_ts = TS_BASE + 60 * FIVE_MIN
        sweep_candle = mk(60 - 1, 10.2, 10.3, 9.7, 10.1)  # 5m before to_ts, sweeps the 10.0 lows
        swept, pool, sweep_ts = has_fresh_sweep(
            [sweep_candle], pools, "Bullish", from_ts=TS_BASE, to_ts=to_ts, max_age_ms=2 * 3600 * 1000,
        )
        assert swept is True
        assert pool is not None and pool.side == "BELOW"
        assert sweep_ts == sweep_candle.timestamp

    def test_stale_sweep_outside_window(self):
        pools = self.make_pools()
        to_ts = TS_BASE + 60 * FIVE_MIN
        old_candle = mk(60 - 30, 10.2, 10.3, 9.7, 10.1)  # ~2.5h before to_ts
        swept, pool, _ = has_fresh_sweep(
            [old_candle], pools, "Bullish", from_ts=TS_BASE, to_ts=to_ts, max_age_ms=2 * 3600 * 1000,
        )
        assert swept is False and pool is None

    def test_same_side_pool_never_counts(self):
        pools = self.make_pools()
        to_ts = TS_BASE + 60 * FIVE_MIN
        # Bearish setup sweeps an ABOVE pool only if a candle wicks above 12 and
        # closes back under; here a bullish candle sweeps BELOW pools, which are
        # irrelevant for a Bearish direction query.
        sweep_candle = mk(60 - 1, 10.2, 10.3, 9.7, 10.1)
        swept, pool, _ = has_fresh_sweep(
            [sweep_candle], pools, "Bearish", from_ts=TS_BASE, to_ts=to_ts, max_age_ms=2 * 3600 * 1000,
        )
        assert swept is False and pool is None

    def test_pools_for_direction_filtering(self):
        pools = self.make_pools()
        assert all(p.side == "BELOW" for p in pools_for_direction(pools, "Bullish"))
        assert all(p.side == "ABOVE" for p in pools_for_direction(pools, "Bearish"))


class TestNearestOpposingPool:
    def test_picks_nearest_beyond_min_distance(self):
        pools = [
            LiquidityPool(kind="EQUAL_HIGHS", price=100.0, side="ABOVE", formed_at=0, touches=2),
            LiquidityPool(kind="PDH", price=105.0, side="ABOVE", formed_at=0, touches=1),
        ]
        got = nearest_opposing_pool(pools, "Bullish", entry_price=100.0, min_distance=2.0)
        assert got is not None and got.price == 105.0

    def test_swept_pools_excluded(self):
        pools = [
            LiquidityPool(kind="PDH", price=103.0, side="ABOVE", formed_at=0, touches=1, swept_at=123),
            LiquidityPool(kind="EQUAL_HIGHS", price=106.0, side="ABOVE", formed_at=0, touches=2),
        ]
        got = nearest_opposing_pool(pools, "Bullish", entry_price=100.0, min_distance=1.0)
        assert got is not None and got.price == 106.0

    def test_none_when_nothing_beyond_min_rr(self):
        pools = [LiquidityPool(kind="PDH", price=101.0, side="ABOVE", formed_at=0, touches=1)]
        assert nearest_opposing_pool(pools, "Bullish", entry_price=100.0, min_distance=2.0) is None


class TestLiquidityTakeProfit:
    def test_uses_pool_when_beyond_min_rr(self):
        pools = [LiquidityPool(kind="PDH", price=103.0, side="ABOVE", formed_at=0, touches=1)]
        tp, mode, pool = liquidity_take_profit(pools, "Bullish", entry_price=100.0, risk_r=1.0)
        assert mode == "LIQUIDITY"
        assert pool is not None and pool.price == 103.0
        assert tp < 103.0  # buffered in front of the level
        assert tp > 102.9  # buffer is small (0.02%)

    def test_falls_back_to_fixed_r_without_pool(self):
        tp, mode, pool = liquidity_take_profit([], "Bullish", entry_price=100.0, risk_r=1.0, fallback_r=2.0)
        assert mode == "FIXED_R" and pool is None and tp == 102.0

    def test_bearish_side(self):
        pools = [LiquidityPool(kind="PDL", price=97.0, side="BELOW", formed_at=0, touches=1)]
        tp, mode, pool = liquidity_take_profit(pools, "Bearish", entry_price=100.0, risk_r=1.0)
        assert mode == "LIQUIDITY"
        assert tp > 97.0 and tp < 97.1


class TestGuardsAndParsing:
    def test_parse_gap_band(self):
        assert parse_gap_band("0.10,0.20") == (0.10, 0.20)
        assert parse_gap_band(" 0.1 , 0.2 ") == (0.1, 0.2)
        assert parse_gap_band(None) is None
        assert parse_gap_band("") is None
        assert parse_gap_band("0.20,0.10") is None  # hi <= lo
        assert parse_gap_band("0.10") is None
        assert parse_gap_band("abc,0.2") is None

    def test_anchor_age_guard(self):
        assert anchor_age_guard_ok(10.0) is True
        assert anchor_age_guard_ok(50.0) is True
        assert anchor_age_guard_ok(24.0) is False   # dead-zone edge [lo, hi)
        assert anchor_age_guard_ok(36.0) is False
        assert anchor_age_guard_ok(48.0) is True
        assert anchor_age_guard_ok(25.0, dead_zone=(20.0, 30.0)) is False


def test_candle_type_matches_engine():
    """liquidity.Candle must be the engine Candle (identical field layout)."""
    c = mk(0, 1.0, 1.2, 0.8, 1.1)
    assert isinstance(c, EngineCandle)
    assert (c.timestamp, c.open, c.high, c.low, c.close) == (TS_BASE, 1.0, 1.2, 0.8, 1.1)
