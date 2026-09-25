"""
Strategy-based TDD tests for Strategy 2 Step 3: LTF FVG discovery.

Rules under test (STRATEGIES.md "Extreme FVG Strategy" + openspec/specs/strategy-2-extreme):
- FVG formation: Bullish c3.low > c1.high -> [bottom=c1.high, top=c3.low];
  Bearish c3.high < c1.low -> [bottom=c3.high, top=c1.low] (strict inequality).
- The candle that formed the FVG (c3) can NEVER count as its own entry touch:
  lifecycle is evaluated on candles i+3 onwards only.
- Discovery starts strictly at the 4H anchor's first touch (no lookahead):
  LTF FVGs are only considered when c3 closes at/after the first touch timestamp.
- Pre-entry SL breach invalidates the candidate; resolved states are discarded.
"""

import pytest

from strategy_extreme_fvg import (
    Candle,
    FVG,
    compute_all_active_4h_fvgs,
    evaluate_ltf_setup_lifecycle,
    find_unmitigated_ltf_fvgs,
    get_4h_fvg_first_touch_ts,
)

FIVE_MIN_MS = 5 * 60 * 1000
FOUR_H_MS = 4 * 3600 * 1000


def mk_c(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


# Base epoch aligned to a 5m grid; current_time_ms is always passed explicitly
# so tests never depend on the wall clock.
T0 = 1_700_000_000_000 - (1_700_000_000_000 % FIVE_MIN_MS)


def _run(candles, direction="Bullish", after=0, now=None, min_gap_pct=0.05, current_price=0.0):
    return find_unmitigated_ltf_fvgs(
        candles_ltf=candles,
        after_timestamp=after,
        direction=direction,
        current_price=current_price,
        current_time_ms=now if now is not None else (candles[-1].timestamp + FIVE_MIN_MS if candles else T0),
        ltf_timeframe="5m",
        min_gap_pct=min_gap_pct,
    )


# ==============================================================================
# 1. FVG math correctness (zone boundaries, equality edge, overlap, min-gap)
# ==============================================================================

def test_bullish_fvg_zone_boundaries_exact():
    """Bullish zone must be exactly [c1.high, c3.low] with gap_pct vs midpoint."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    fvgs = _run([c1, c2, c3], now=T0 + 3 * FIVE_MIN_MS)

    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.direction == "Bullish"
    assert fvg.bottom == pytest.approx(101.0)   # c1.high
    assert fvg.top == pytest.approx(103.0)      # c3.low
    assert fvg.gap_pct == pytest.approx((2.0 / 102.0) * 100.0)


def test_bearish_fvg_zone_boundaries_exact():
    """Bearish zone must be exactly [c3.high, c1.low]."""
    c1 = mk_c(T0, 100.0, 100.8, 99.0, 99.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 99.5, 100.0, 94.0, 94.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 94.5, 95.0, 93.0, 93.5)
    fvgs = _run([c1, c2, c3], direction="Bearish", now=T0 + 3 * FIVE_MIN_MS)

    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.direction == "Bearish"
    assert fvg.top == pytest.approx(99.0)       # c1.low
    assert fvg.bottom == pytest.approx(95.0)    # c3.high


def test_equal_boundary_candle_produces_no_fvg():
    """Strict inequality: c3.low == c1.high (bullish) is NOT a gap; bearish mirror too."""
    c1 = mk_c(T0, 100.0, 101.0, 99.0, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 104.0, 100.0, 103.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 103.5, 105.0, 101.0, 104.5)  # c3.low == c1.high
    assert _run([c1, c2, c3], now=T0 + 3 * FIVE_MIN_MS) == []

    c1b = mk_c(T0, 100.0, 101.0, 99.0, 100.5)
    c2b = mk_c(T0 + FIVE_MIN_MS, 100.5, 100.8, 96.0, 96.5)
    c3b = mk_c(T0 + 2 * FIVE_MIN_MS, 96.5, 99.0, 95.0, 95.5)  # c3.high == c1.low
    assert _run([c1b, c2b, c3b], direction="Bearish", now=T0 + 3 * FIVE_MIN_MS) == []


def test_overlapping_candles_produce_no_fvg():
    """c3 range intersecting c1 range in either direction yields no FVG."""
    c1 = mk_c(T0, 100.0, 101.0, 99.0, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 103.0, 100.0, 102.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 102.5, 104.0, 99.5, 103.5)  # low 99.5 < c1.high 101.0
    assert _run([c1, c2, c3], now=T0 + 3 * FIVE_MIN_MS) == []

    c3b = mk_c(T0 + 2 * FIVE_MIN_MS, 102.5, 104.0, 100.0, 103.5)
    c2b = mk_c(T0 + FIVE_MIN_MS, 100.5, 103.0, 98.0, 102.0)
    assert _run([c1, c2b, c3b], direction="Bearish", now=T0 + 3 * FIVE_MIN_MS) == []


def test_min_gap_filter_boundary():
    """Gaps below 0.05% of midpoint are filtered; just above the threshold pass."""
    mid = 100.0
    ref = mid * 0.05 / 100.0  # width of an exactly-0.05% zone at mid

    w_low = ref * 0.98  # BELOW threshold
    c1 = mk_c(T0, 99.0, mid - w_low / 2, 98.0, 99.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, mid - w_low / 2, mid + 6, mid - w_low, mid + 5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, mid + 5, mid + 7, mid + w_low / 2, mid + 6)
    assert _run([c1, c2, c3], now=T0 + 3 * FIVE_MIN_MS) == []

    w_high = ref * 1.02  # ABOVE threshold
    c1b = mk_c(T0, 99.0, mid - w_high / 2, 98.0, 99.5)
    c2b = mk_c(T0 + FIVE_MIN_MS, mid - w_high / 2, mid + 6, mid - w_high, mid + 5)
    c3b = mk_c(T0 + 2 * FIVE_MIN_MS, mid + 5, mid + 7, mid + w_high / 2, mid + 6)
    fvgs = _run([c1b, c2b, c3b], now=T0 + 3 * FIVE_MIN_MS)
    assert len(fvgs) == 1
    assert fvgs[0].gap_pct == pytest.approx((w_high / mid) * 100.0)


def test_gap_pct_and_geometry_properties():
    """FVG properties width/midpoint/gap_pct are internally consistent."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    fvg = _run([c1, c2, c3], now=T0 + 3 * FIVE_MIN_MS)[0]

    assert fvg.width == pytest.approx(2.0)
    assert fvg.midpoint == pytest.approx(102.0)
    assert fvg.gap_pct == pytest.approx(fvg.width / fvg.midpoint * 100.0)


# ==============================================================================
# 2. No lookahead: discovery anchored to the 4H first touch
# ==============================================================================

def test_fvg_formed_before_first_touch_is_excluded():
    """c3 closing strictly BEFORE the anchor first-touch timestamp is not discovered."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    c3_close = T0 + 3 * FIVE_MIN_MS

    first_touch = c3_close + 1  # anchor touch happens AFTER this FVG completed
    assert _run([c1, c2, c3], after=first_touch, now=c3_close + 10 * FIVE_MIN_MS) == []


def test_fvg_formed_exactly_at_first_touch_is_included():
    """Boundary: c3 close exactly AT the first-touch timestamp qualifies (>=)."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    c3_close = T0 + 3 * FIVE_MIN_MS

    fvgs = _run([c1, c2, c3], after=c3_close, now=c3_close + 10 * FIVE_MIN_MS)
    assert len(fvgs) == 1


def test_4h_fvg_zone_math_and_touch_detection_compose():
    """4H FVG zone math + touch detection: c3 never counts as its own touch."""
    h1 = mk_c(T0, 2400.0, 2402.0, 2390.0, 2395.0)
    h2 = mk_c(T0 + FOUR_H_MS, 2395.0, 2450.0, 2394.0, 2445.0)
    h3 = mk_c(T0 + 2 * FOUR_H_MS, 2445.0, 2460.0, 2415.0, 2455.0)
    h4 = mk_c(T0 + 3 * FOUR_H_MS, 2455.0, 2470.0, 2410.0, 2465.0)  # low 2410 <= zone top
    htf = compute_all_active_4h_fvgs([h1, h2, h3, h4], current_time_ms=T0 + 5 * FOUR_H_MS)

    assert len(htf) == 1
    anchor_fvg = htf[0]
    assert anchor_fvg.bottom == pytest.approx(2402.0)  # h1.high
    assert anchor_fvg.top == pytest.approx(2415.0)     # h3.low

    # Formation time = c3 close (open + 4H)
    assert anchor_fvg.close_timestamp == h3.timestamp + FOUR_H_MS

    # h3 (formation candle) cannot be its own touch -> first touch must be h4
    touch = get_4h_fvg_first_touch_ts(candles_4h=[h1, h2, h3, h4], fvg=anchor_fvg, current_price=0.0)
    assert touch is not None
    first_ts, tf = touch
    assert first_ts == h4.timestamp
    assert tf == "4h"


def test_ltf_fvgs_compose_into_4h_anchor_flow():
    """A post-touch 5m gap lands above the 4H anchor zone and stays discoverable."""
    h1, h2, h3 = mk_c(T0, 2400.0, 2402.0, 2390.0, 2395.0), \
        mk_c(T0 + FOUR_H_MS, 2395.0, 2450.0, 2394.0, 2445.0), \
        mk_c(T0 + 2 * FOUR_H_MS, 2445.0, 2460.0, 2415.0, 2455.0)
    anchor_fvg = compute_all_active_4h_fvgs([h1, h2, h3], current_time_ms=T0 + 3 * FOUR_H_MS)[0]

    first_touch = anchor_fvg.close_timestamp
    base = first_touch - (first_touch % FIVE_MIN_MS)
    l1 = mk_c(base, 2412.0, 2418.0, 2414.0, 2416.0)             # touches zone [2402..2415]
    l2 = mk_c(base + FIVE_MIN_MS, 2416.0, 2424.0, 2415.0, 2423.0)
    l3 = mk_c(base + 2 * FIVE_MIN_MS, 2423.0, 2426.0, 2425.0, 2425.5)  # gap [2418..2425]

    assert l1.low <= anchor_fvg.top and l1.high >= anchor_fvg.bottom  # anchor touch
    fvgs = _run([l1, l2, l3], after=first_touch, now=base + 3 * FIVE_MIN_MS)
    assert len(fvgs) == 1
    assert fvgs[0].direction == "Bullish"
    assert fvgs[0].lifecycle_state == "PENDING_RETRACE"


# ==============================================================================
# 3. Formation-candle exclusion & lifecycle wiring
# ==============================================================================

def test_formation_candle_cannot_be_own_entry_pending_state():
    """Without post-formation candles the FVG stays PENDING_RETRACE with no entry ts."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    fvgs = _run([c1, c2, c3], now=T0 + 3 * FIVE_MIN_MS)

    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.lifecycle_state == "PENDING_RETRACE"
    assert fvg.entry_timestamp is None
    assert fvg.floating_r == 0.0


def test_formation_candle_touching_zone_is_not_entry():
    """c3's low equals the zone top (would be entry if counted) but c4+ is the entry scan."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)  # c3.low == zone top
    fvgs = _run([c1, c2, c3], now=T0 + 3 * FIVE_MIN_MS)

    assert len(fvgs) == 1
    assert fvgs[0].lifecycle_state == "PENDING_RETRACE"
    assert fvgs[0].entry_timestamp is None


def test_entry_on_first_post_formation_touch_candle():
    """First post-formation candle touching the outer boundary activates with its open ts."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    c4 = mk_c(T0 + 3 * FIVE_MIN_MS, 106.0, 106.5, 102.9, 105.0)  # low <= entry (103.0)
    fvgs = _run([c1, c2, c3, c4], now=T0 + 4 * FIVE_MIN_MS)

    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.lifecycle_state == "TRADE_ACTIVE"
    assert fvg.entry_timestamp == c4.timestamp


def test_pre_entry_sl_breach_invalidates_candidate():
    """Post-formation candle breaching SL without touching entry -> discarded."""
    c1 = mk_c(T0, 100.0, 101.0, 97.0, 100.5)   # SL will be 97.0
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    c4 = mk_c(T0 + 3 * FIVE_MIN_MS, 105.0, 105.5, 96.5, 97.5)  # low <= SL, high < entry
    assert _run([c1, c2, c3, c4], now=T0 + 4 * FIVE_MIN_MS) == []


def test_resolved_states_are_discarded_from_discovery():
    """STOPPED_OUT / COMPLETED candidates never surface from discovery."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    # Fill then stop: entry 103.0, SL 99.5
    c4 = mk_c(T0 + 3 * FIVE_MIN_MS, 106.0, 106.5, 102.5, 103.0)
    c5 = mk_c(T0 + 4 * FIVE_MIN_MS, 103.0, 103.5, 99.0, 99.5)
    assert _run([c1, c2, c3, c4, c5], now=T0 + 5 * FIVE_MIN_MS) == []

    # Fill then complete (2R: 103.0 + 2 * 3.5 = 110.0)
    c5b = mk_c(T0 + 4 * FIVE_MIN_MS, 103.0, 110.5, 102.0, 109.0)
    assert _run([c1, c2, c3, c4, c5b], now=T0 + 5 * FIVE_MIN_MS) == []


def test_direction_filter_only_matching_fvgs_discovered():
    """A bullish scan must not return bearish-shaped gaps and vice versa."""
    bull_c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    bull_c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    bull_c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    assert _run([bull_c1, bull_c2, bull_c3], direction="Bearish", now=T0 + 3 * FIVE_MIN_MS) == []
    assert len(_run([bull_c1, bull_c2, bull_c3], direction="Bullish", now=T0 + 3 * FIVE_MIN_MS)) == 1


# ==============================================================================
# 4. evaluate_ltf_setup_lifecycle: formation-exclusion rules directly
# ==============================================================================

def test_lifecycle_pending_with_no_subsequent_candles():
    """Empty subsequent list -> PENDING_RETRACE, no entry, zero floating R."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    fvg = FVG(direction="Bullish", top=103.0, bottom=101.0, c1=c1, c2=c2, c3=c3,
              formed_at=c3.timestamp, timeframe="5m")

    state, entry_ts, floating = evaluate_ltf_setup_lifecycle(fvg, subsequent_candles=[], current_price=0.0)
    assert state == "PENDING_RETRACE"
    assert entry_ts is None
    assert floating == 0.0


def test_lifecycle_sl_breach_vs_touch_breach_same_candle():
    """Bullish: low<=SL AND high<entry -> INVALIDATED; touch+breach same candle -> STOPPED_OUT."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    fvg = FVG(direction="Bullish", top=103.0, bottom=101.0, c1=c1, c2=c2, c3=c3,
              formed_at=c3.timestamp, timeframe="5m")

    breach = mk_c(T0 + 3 * FIVE_MIN_MS, 102.0, 102.5, 99.0, 99.5)  # no entry touch
    state, entry_ts, _ = evaluate_ltf_setup_lifecycle(fvg, [breach], current_price=0.0)
    assert state == "INVALIDATED"
    assert entry_ts is None

    touch_breach = mk_c(T0 + 3 * FIVE_MIN_MS, 104.0, 104.5, 99.0, 99.5)  # touches entry first
    state2, entry_ts2, floating2 = evaluate_ltf_setup_lifecycle(fvg, [touch_breach], current_price=0.0)
    assert state2 == "STOPPED_OUT"
    assert entry_ts2 == touch_breach.timestamp
    assert floating2 == -1.0


def test_lifecycle_live_price_branches():
    """Live price fills via the wall-clock branch; beyond SL invalidates."""
    c1 = mk_c(T0, 100.0, 101.0, 99.5, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    fvg = FVG(direction="Bullish", top=103.0, bottom=101.0, c1=c1, c2=c2, c3=c3,
              formed_at=c3.timestamp, timeframe="5m")

    state, entry_ts, floating = evaluate_ltf_setup_lifecycle(fvg, [], current_price=102.0)
    assert state == "TRADE_ACTIVE"
    assert entry_ts is not None and entry_ts > 0
    # floating_r is wick-risk based: (current - entry) / (entry - SL) = -1.0 / 3.5
    assert floating == pytest.approx((102.0 - 103.0) / 3.5)

    state2, _, _ = evaluate_ltf_setup_lifecycle(fvg, [], current_price=99.0)
    assert state2 == "INVALIDATED"
