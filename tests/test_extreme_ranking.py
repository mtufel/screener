"""
Strategy-based TDD tests for Strategy 2 Step 4: extreme anchor ranking.

Rules under test (STRATEGIES.md):
- The extreme anchor is a 4H FVG built from the LAST 3 candles of the extreme move
  (bullish: deepest bottom; bearish: highest top).
- A touch means a post-formation candle's range overlaps the 4H zone (bounds inclusive);
  the formation candle (c3) can never be its own touch.
- first_touch_timestamp = EARLIEST qualifying touch (LTF scan starts from it).
- Anchor selection prefers the currently-inside zone, then the most recent touch.
- Formation time is c3's close (open + 4H), rendered in IST.
"""

from datetime import datetime

import pytest

from strategy_extreme_fvg import (
    Candle,
    FVG,
    TouchedAnchor,
    compute_all_active_4h_fvgs,
    get_4h_fvg_first_touch_ts,
    get_4h_fvg_most_recent_touch_ts,
    get_most_recent_touched_4h_fvg,
)

FOUR_H_MS = 4 * 3600 * 1000


def mk_c(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


T0 = 1_700_000_000_000 - (1_700_000_000_000 % FOUR_H_MS)
NOW = T0 + 20 * FOUR_H_MS  # fixed wall clock for the live-price branches


def _bullish_extreme_candles():
    """Last-3 bullish extreme: h1 [2390..2402], h2 up, h3 [2415..2460] -> zone [2402, 2415]."""
    h1 = mk_c(T0, 2400.0, 2402.0, 2390.0, 2395.0)
    h2 = mk_c(T0 + FOUR_H_MS, 2395.0, 2450.0, 2394.0, 2445.0)
    h3 = mk_c(T0 + 2 * FOUR_H_MS, 2445.0, 2460.0, 2415.0, 2455.0)
    return h1, h2, h3


def _bearish_extreme_candles():
    """Last-3 bearish extreme: h1 [2498..2510], h2 down, h3 [2450..2485] -> zone [2485, 2498]."""
    h1 = mk_c(T0, 2500.0, 2510.0, 2498.0, 2505.0)
    h2 = mk_c(T0 + FOUR_H_MS, 2505.0, 2506.0, 2450.0, 2455.0)
    h3 = mk_c(T0 + 2 * FOUR_H_MS, 2455.0, 2485.0, 2440.0, 2470.0)
    return h1, h2, h3


def _fvg_of(c1, c2, c3):
    return FVG(direction="Bullish", top=c3.low, bottom=c1.high, c1=c1, c2=c2, c3=c3,
               formed_at=c3.timestamp, timeframe="4h")


def _bearish_fvg_of(c1, c2, c3):
    return FVG(direction="Bearish", top=c1.low, bottom=c3.high, c1=c1, c2=c2, c3=c3,
               formed_at=c3.timestamp, timeframe="4h")


def _touched(fvg, first_ts, recent_ts=None, inside=False):
    return TouchedAnchor(fvg=fvg, first_touch_timestamp=first_ts,
                         most_recent_touch_timestamp=recent_ts or first_ts,
                         is_currently_inside=inside, touch_timeframe="4h")


# ==============================================================================
# 4H FVG formation from the last-3 extreme window
# ==============================================================================

def test_last3_bullish_extreme_forms_zone():
    """Bullish zone bounds come strictly from the last-3 candles: [h1.high, h3.low]."""
    h1, h2, h3 = _bullish_extreme_candles()
    fvgs = compute_all_active_4h_fvgs([h1, h2, h3], current_time_ms=T0 + 3 * FOUR_H_MS)

    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.direction == "Bullish"
    assert fvg.bottom == pytest.approx(2402.0)
    assert fvg.top == pytest.approx(2415.0)
    assert fvg.formed_at == h3.timestamp


def test_last3_bearish_extreme_forms_zone():
    """Bearish zone bounds come strictly from the last-3 candles: [h3.high, h1.low]."""
    h1, h2, h3 = _bearish_extreme_candles()
    fvgs = compute_all_active_4h_fvgs([h1, h2, h3], current_time_ms=T0 + 3 * FOUR_H_MS)

    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.direction == "Bearish"
    assert fvg.top == pytest.approx(2498.0)
    assert fvg.bottom == pytest.approx(2485.0)


def test_unclosed_4h_candle_is_excluded_from_formation():
    """A still-open 4H candle never participates in FVG formation (closed filter)."""
    h1, h2, h3 = _bullish_extreme_candles()
    h4 = mk_c(T0 + 3 * FOUR_H_MS, 2455.0, 2490.0, 2450.0, 2480.0)  # not yet closed
    # now = h4 open -> h4 must be filtered out
    fvgs = compute_all_active_4h_fvgs([h1, h2, h3, h4], current_time_ms=T0 + 3 * FOUR_H_MS)
    assert len(fvgs) == 1
    assert fvgs[0].formed_at == h3.timestamp


# ==============================================================================
# Touch semantics: post-close, inclusive bounds, formation candle excluded
# ==============================================================================

def test_formation_candle_cannot_be_own_touch():
    """h3's range overlaps its own zone, but only h4+ may count as touch."""
    h1, h2, h3 = _bullish_extreme_candles()
    fvg = _fvg_of(h1, h2, h3)
    # h3 low (2415) equals zone top -> touches its own zone; must be ignored.
    assert h3.low <= fvg.top and h3.high >= fvg.bottom

    touch = get_4h_fvg_first_touch_ts(candles_4h=[h1, h2, h3], fvg=fvg, current_price=0.0)
    assert touch is None  # no post-close candle present


def test_first_touch_is_earliest_post_close_candle():
    """first_touch = earliest qualifying candle, not the latest."""
    h1, h2, h3 = _bullish_extreme_candles()
    fvg = _fvg_of(h1, h2, h3)
    h4 = mk_c(T0 + 3 * FOUR_H_MS, 2455.0, 2470.0, 2410.0, 2465.0)  # touches zone
    h5 = mk_c(T0 + 4 * FOUR_H_MS, 2465.0, 2480.0, 2412.0, 2475.0)  # touches zone too

    touch = get_4h_fvg_first_touch_ts(candles_4h=[h1, h2, h3, h4, h5], fvg=fvg, current_price=0.0)
    assert touch is not None and touch[0] == h4.timestamp and touch[1] == "4h"


def test_touch_bounds_inclusive_and_miss_rejected():
    """Exact-boundary contact counts as touch; a full miss does not."""
    h1, h2, h3 = _bullish_extreme_candles()
    fvg = _fvg_of(h1, h2, h3)

    # Exact touch: candle low == zone top
    h4_exact = mk_c(T0 + 3 * FOUR_H_MS, 2420.0, 2425.0, 2415.0, 2422.0)
    touch = get_4h_fvg_first_touch_ts(candles_4h=[h1, h2, h3, h4_exact], fvg=fvg, current_price=0.0)
    assert touch is not None and touch[0] == h4_exact.timestamp

    # Miss: candle low above zone top
    h4_miss = mk_c(T0 + 3 * FOUR_H_MS, 2420.0, 2425.0, 2415.5, 2422.0)
    assert get_4h_fvg_first_touch_ts(candles_4h=[h1, h2, h3, h4_miss], fvg=fvg, current_price=0.0) is None


def test_most_recent_touch_prefers_latest_and_inside():
    """most_recent_touch = latest touch; currently-inside beats older touches."""
    h1, h2, h3 = _bullish_extreme_candles()
    fvg = _fvg_of(h1, h2, h3)
    h4 = mk_c(T0 + 3 * FOUR_H_MS, 2455.0, 2470.0, 2410.0, 2465.0)
    h5 = mk_c(T0 + 4 * FOUR_H_MS, 2465.0, 2480.0, 2412.0, 2475.0)

    rec = get_4h_fvg_most_recent_touch_ts(candles_4h=[h1, h2, h3, h4, h5], fvg=fvg,
                                          current_price=0.0, candles_ltf=None)
    assert rec is not None
    rec_ts, inside, tf = rec
    assert rec_ts == h5.timestamp and inside is False and tf == "4h"

    # Live price inside zone -> is_currently_inside wins
    rec2 = get_4h_fvg_most_recent_touch_ts(candles_4h=[h1, h2, h3, h4, h5], fvg=fvg,
                                           current_price=2410.0, candles_ltf=None)
    assert rec2 is not None and rec2[1] is True


# ==============================================================================
# Anchor selection: deepest bottom / highest top / inside-first
# ==============================================================================

def test_select_deepest_bottom_bullish_anchor():
    """Bullish: the deepest-bottom touched FVG is selected as the single anchor."""
    h1, h2, h3 = _bullish_extreme_candles()
    deep = _fvg_of(h1, h2, h3)                       # bottom 2402
    shallow_c1 = mk_c(T0, 2430.0, 2432.0, 2420.0, 2425.0)
    shallow_c2 = mk_c(T0 + FOUR_H_MS, 2425.0, 2460.0, 2424.0, 2455.0)
    shallow_c3 = mk_c(T0 + 2 * FOUR_H_MS, 2455.0, 2462.0, 2440.0, 2460.0)
    shallow = _fvg_of(shallow_c1, shallow_c2, shallow_c3)  # bottom 2432

    a1 = _touched(deep, first_ts=T0 + 3 * FOUR_H_MS, recent_ts=T0 + 3 * FOUR_H_MS)
    a2 = _touched(shallow, first_ts=T0 + 3 * FOUR_H_MS, recent_ts=T0 + 4 * FOUR_H_MS)

    chosen = get_most_recent_touched_4h_fvg(candles_4h=[], active_fvgs=[deep, shallow],
                                            current_price=0.0, candles_ltf=None)
    # Only via pre-built anchors: exercise ranking through TouchedAnchor inputs is not
    # possible directly (the function computes touches itself), so drive through the
    # real touch pipeline with candles that touch BOTH zones.
    h4 = mk_c(T0 + 3 * FOUR_H_MS, 2440.0, 2445.0, 2400.0, 2430.0)  # touches both zones
    h5 = mk_c(T0 + 4 * FOUR_H_MS, 2430.0, 2436.0, 2396.0, 2405.0)  # touches only deep
    chosen = get_most_recent_touched_4h_fvg(candles_4h=[h1, h2, h3, shallow_c1, shallow_c2,
                                                        shallow_c3, h4, h5],
                                            active_fvgs=[deep, shallow],
                                            current_price=0.0, candles_ltf=None)
    assert chosen is not None
    # h5 (most recent) touches only the deep zone -> deep must win the anchor slot
    assert chosen.fvg.bottom == pytest.approx(2402.0)


def test_select_highest_top_bearish_anchor():
    """Bearish: the highest-top touched FVG is selected as the single anchor."""
    bh1, bh2, bh3 = _bearish_extreme_candles()
    higher = _bearish_fvg_of(bh1, bh2, bh3)          # top 2498
    lc1 = mk_c(T0, 2470.0, 2475.0, 2460.0, 2465.0)
    lc2 = mk_c(T0 + FOUR_H_MS, 2465.0, 2466.0, 2430.0, 2435.0)
    lc3 = mk_c(T0 + 2 * FOUR_H_MS, 2435.0, 2455.0, 2425.0, 2450.0)
    lower = _bearish_fvg_of(lc1, lc2, lc3)           # top 2475

    # Candle touching only the higher zone most recently
    h4 = mk_c(T0 + 3 * FOUR_H_MS, 2490.0, 2499.0, 2480.0, 2495.0)  # inside [2485..2498] only
    chosen = get_most_recent_touched_4h_fvg(
        candles_4h=[bh1, bh2, bh3, lc1, lc2, lc3, h4],
        active_fvgs=[higher, lower], current_price=0.0, candles_ltf=None)
    assert chosen is not None
    assert chosen.fvg.top == pytest.approx(2498.0)


def test_no_touched_fvg_returns_none():
    """With no post-close touch anywhere, no anchor is produced."""
    h1, h2, h3 = _bullish_extreme_candles()
    fvg = _fvg_of(h1, h2, h3)
    h4 = mk_c(T0 + 3 * FOUR_H_MS, 2460.0, 2470.0, 2450.0, 2465.0)  # never dips to zone
    chosen = get_most_recent_touched_4h_fvg(candles_4h=[h1, h2, h3, h4],
                                            active_fvgs=[fvg], current_price=0.0,
                                            candles_ltf=None)
    assert chosen is None


def test_touched_anchor_carries_ist_first_touch_string():
    """IST formatting: first_touch_time_ist renders the touch open-time window."""
    h1, h2, h3 = _bullish_extreme_candles()
    fvg = _fvg_of(h1, h2, h3)
    h4 = mk_c(T0 + 3 * FOUR_H_MS, 2455.0, 2470.0, 2410.0, 2465.0)
    chosen = get_most_recent_touched_4h_fvg(candles_4h=[h1, h2, h3, h4],
                                            active_fvgs=[fvg], current_price=0.0,
                                            candles_ltf=None)
    assert chosen is not None
    assert chosen.first_touch_time_ist.endswith("IST")
    assert chosen.first_touch_timestamp == h4.timestamp


def test_currently_inside_anchor_preferred_over_deeper_untouched():
    """An anchor the live price sits inside wins even if a deeper FVG was touched earlier."""
    h1, h2, h3 = _bullish_extreme_candles()
    deep = _fvg_of(h1, h2, h3)                          # zone [2402..2415]
    sc1 = mk_c(T0, 2430.0, 2432.0, 2420.0, 2425.0)
    sc2 = mk_c(T0 + FOUR_H_MS, 2425.0, 2460.0, 2424.0, 2455.0)
    sc3 = mk_c(T0 + 2 * FOUR_H_MS, 2455.0, 2462.0, 2440.0, 2460.0)
    shallow = _fvg_of(sc1, sc2, sc3)                    # zone [2432..2440]

    # Old 4H touch on the deep zone only; live price sits inside the shallow zone
    h4 = mk_c(T0 + 3 * FOUR_H_MS, 2425.0, 2428.0, 2405.0, 2420.0)
    h5 = mk_c(T0 + 4 * FOUR_H_MS, 2420.0, 2426.0, 2416.0, 2424.0)  # touches deep again

    chosen = get_most_recent_touched_4h_fvg(
        candles_4h=[h1, h2, h3, sc1, sc2, sc3, h4, h5],
        active_fvgs=[deep, shallow],
        current_price=2436.0,  # inside shallow [2432..2440], above deep zone
        candles_ltf=None)

    assert chosen is not None
    assert chosen.fvg is shallow
    assert chosen.is_currently_inside is True
