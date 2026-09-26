"""
Additive tests for the extreme-fvg-bias-filters OpenSpec change.

Covers the four research-backed bias filters added to Strategy 2 (Extreme LTF FVG):
  1. Momentum impulse-candle  (_is_strong_momentum, c2 body >= 50% of range, in-trade direction)
  2. Distance-from-4H-zone confluence (reject LTF FVGs far from the active 4H anchor)
  3. Gap ceiling (reject oversized gaps above max_gap_pct)
  4. Age ceiling (reject LTF FVGs formed more than max_ltf_fvg_age_candles after the 4H touch)

These tests are strictly ADDITIVE -- the existing test suite is Source of Truth (SOT)
and is intentionally left untouched.
"""

import pytest

from strategy_extreme_fvg import (
    Candle,
    FVG,
    _is_strong_momentum,
    find_unmitigated_ltf_fvgs,
)

FIVE_MIN_MS = 5 * 60 * 1000


def mk_c(ts, o, h, l, c):
    """Synthetic LTF candle on the 5m grid."""
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


# Base epoch aligned to a 5m grid; current_time_ms is always passed explicitly
# so tests never depend on the wall clock.
T0 = 1_700_000_000_000 - (1_700_000_000_000 % FIVE_MIN_MS)


def _run(candles, direction="Bullish", after=0, now=None, current_price=0.0, **bias):
    """Thin wrapper over find_unmitigated_ltf_fvgs allowing bias kwargs to be passed."""
    return find_unmitigated_ltf_fvgs(
        candles_ltf=candles,
        after_timestamp=after,
        direction=direction,
        current_price=current_price,
        current_time_ms=now if now is not None else (candles[-1].timestamp + FIVE_MIN_MS if candles else T0),
        ltf_timeframe="5m",
        min_gap_pct=0.05,
        **bias,
    )


# ==============================================================================
# 1. Momentum impulse-candle helper
# ==============================================================================

def test_is_strong_momentum_accepts_strong_bullish():
    c = mk_c(T0, o=100.0, h=107.0, l=99.5, c=106.0)  # body 6 / range 7.5 = 0.8
    assert _is_strong_momentum(c, "Bullish") is True


def test_is_strong_momentum_accepts_strong_bearish():
    c = mk_c(T0, o=106.0, h=107.0, l=99.5, c=100.0)  # body 6 / range 7.5 = 0.8
    assert _is_strong_momentum(c, "Bearish") is True


def test_is_strong_momentum_rejects_weak_body():
    c = mk_c(T0, o=100.0, h=114.0, l=100.0, c=103.0)  # body 3 / range 14 < 0.5
    assert _is_strong_momentum(c, "Bullish") is False


def test_is_strong_momentum_rejects_direction_mismatch():
    # Strong body, but in the opposite direction of the FVG.
    c = mk_c(T0, o=100.0, h=107.0, l=99.5, c=106.0)  # strong bullish candle
    assert _is_strong_momentum(c, "Bearish") is False
    d = mk_c(T0, o=106.0, h=107.0, l=99.5, c=100.0)  # strong bearish candle
    assert _is_strong_momentum(d, "Bullish") is False


def test_is_strong_momentum_rejects_doji():
    c = mk_c(T0, o=101.0, h=107.0, l=99.5, c=101.0)  # body 0
    assert _is_strong_momentum(c, "Bullish") is False


# ==============================================================================
# Shared fixture: a clean bullish FVG that remains PENDING_RETRACE
# ==============================================================================

@pytest.fixture
def bullish_fvg_candles():
    """3-candle bullish FVG [101, 103]: pending lifecycle, gap_pct ~1.96%."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    return [c1, c2, c3]


# ==============================================================================
# 2. Distance-from-4H-zone confluence filter
# ==============================================================================

def test_distance_filter_accepts_near_zone(bullish_fvg_candles):
    # Bullish FVG bottom=101; anchor bottom=100 -> d = (101-100)/100*100 = 1.0%
    fvgs = _run(
        bullish_fvg_candles,
        anchor_bottom=100.0,
        anchor_top=104.0,
        max_dist_from_4h_pct=2.0,
    )
    assert len(fvgs) == 1


def test_distance_filter_rejects_far_from_zone(bullish_fvg_candles):
    # d = 1.0% but ceiling is 0.5% -> rejected.
    fvgs = _run(
        bullish_fvg_candles,
        anchor_bottom=100.0,
        anchor_top=104.0,
        max_dist_from_4h_pct=0.5,
    )
    assert len(fvgs) == 0


def test_distance_filter_disabled_by_zero(bullish_fvg_candles):
    # A far-from-zone FVG is still retained when the filter is off.
    # anchor bottom=95 -> d = (101-95)/95*100 ~= 6.3%
    fvgs = _run(
        bullish_fvg_candles,
        anchor_bottom=95.0,
        anchor_top=99.0,
        max_dist_from_4h_pct=0.0,  # disabled
    )
    assert len(fvgs) == 1


# ==============================================================================
# 3. Momentum filter
# ==============================================================================

def test_momentum_filter_accepts_strong_impulse(bullish_fvg_candles):
    # c2 (100.5 -> 105.5, body 5 / range 6) is a strong bullish impulse.
    fvgs = _run(bullish_fvg_candles, require_momentum=True)
    assert len(fvgs) == 1


def test_momentum_filter_rejects_weak_impulse():
    # Same geometry but c2 has a wicky body (body 1 / range 6 < 0.5).
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 100.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    candles = [c1, c2, c3]

    # Without the filter the weak-impulse FVG is retained...
    assert len(_run(candles)) == 1
    # ...with require_momentum it is rejected.
    assert len(_run(candles, require_momentum=True)) == 0


# ==============================================================================
# 4. Gap ceiling filter
# ==============================================================================

def test_gap_ceiling_accepts_under_limit(bullish_fvg_candles):
    # gap_pct ~= (2/102)*100 = 1.96% <= 3.0%
    fvgs = _run(bullish_fvg_candles, max_gap_pct=3.0)
    assert len(fvgs) == 1


def test_gap_ceiling_rejects_oversized(bullish_fvg_candles):
    # gap_pct ~= 1.96% > 1.5% ceiling -> rejected.
    fvgs = _run(bullish_fvg_candles, max_gap_pct=1.5)
    assert len(fvgs) == 0


# ==============================================================================
# 5. Age ceiling filter
# ==============================================================================

def test_age_ceiling_rejects_stale_fvg(bullish_fvg_candles):
    # FVG forms at candle index 0 -> age = (0 + 2) - touch_idx.
    # With first_touch_ts before any candle, _touch_idx=0 -> age=2.
    # Ceiling of 1 rejects it.
    fvgs = _run(
        bullish_fvg_candles,
        first_touch_ts=T0,
        max_ltf_fvg_age_candles=1,
    )
    assert len(fvgs) == 0


def test_age_ceiling_accepts_fresh_fvg(bullish_fvg_candles):
    # Same FVG, but a ceiling high enough (>= 2) retains it.
    fvgs = _run(
        bullish_fvg_candles,
        first_touch_ts=T0,
        max_ltf_fvg_age_candles=2,
    )
    assert len(fvgs) == 1


def test_age_ceiling_permissive_default_keeps_all(bullish_fvg_candles):
    # Default 9999 is permissive (runtime no-op).
    fvgs = _run(bullish_fvg_candles, first_touch_ts=T0)
    assert len(fvgs) == 1


# ==============================================================================
# 6. Integration: all four filters together
# ==============================================================================

def test_all_filters_together_accept_only_confluent_setup(bullish_fvg_candles):
    # All constraints satisfied: near-zone, momentum ON, gap under ceiling, fresh.
    fvgs = _run(
        bullish_fvg_candles,
        anchor_bottom=100.0,
        anchor_top=104.0,
        max_dist_from_4h_pct=2.0,
        require_momentum=True,
        max_gap_pct=3.0,
        max_ltf_fvg_age_candles=2,
        first_touch_ts=T0,
    )
    assert len(fvgs) == 1


def test_all_filters_together_reject_when_any_fails(bullish_fvg_candles):
    # Everything confluent except distance is exceeded (d=1.0% > 0.5% ceiling).
    fvgs = _run(
        bullish_fvg_candles,
        anchor_bottom=100.0,
        anchor_top=104.0,
        max_dist_from_4h_pct=0.5,
        require_momentum=True,
        max_gap_pct=3.0,
        max_ltf_fvg_age_candles=2,
        first_touch_ts=T0,
    )
    assert len(fvgs) == 0
