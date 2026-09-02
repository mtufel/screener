"""
Unit tests for strategy_extreme_fvg.py.
Covers:
1. Closed candle enforcement (open/unfinished candles rejected from forming FVGs).
2. 4H Bullish and Bearish FVG detection.
3. Invalidation logic (wick vs. close breach).
4. HTFFVGCache bootstrap and delta update mechanisms.
"""

import pytest
from strategy_extreme_fvg import (
    Candle,
    FVG,
    HTFFVGCache,
    TouchedAnchor,
    compute_all_active_4h_fvgs,
    filter_closed_candles,
    get_4h_fvg_most_recent_touch,
    get_most_recent_touched_4h_fvg,
    HTF_CANDLE_DURATION_MS,
)

H4 = HTF_CANDLE_DURATION_MS  # 4 hours in ms


def make_candle(ts: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=100.0)


# ==============================================================================
# 1. Closed Candle Filter Tests
# ==============================================================================
def test_filter_closed_candles_excludes_unfinished_bar():
    # C1 starts at 0, closes at 4h
    c1 = make_candle(0, 100, 105, 95, 102)
    # C2 starts at 4h, closes at 8h
    c2 = make_candle(H4, 102, 110, 101, 108)
    # C3 starts at 8h, closes at 12h (unfinished if now = 10h)
    c3_live = make_candle(2 * H4, 108, 120, 107, 118)

    # Current time = 10h (during C3)
    now_ms = int(2.5 * H4)
    closed = filter_closed_candles([c1, c2, c3_live], current_time_ms=now_ms)

    assert len(closed) == 2
    assert closed == [c1, c2]


def test_filter_closed_candles_includes_finished_bar():
    c1 = make_candle(0, 100, 105, 95, 102)
    c2 = make_candle(H4, 102, 110, 101, 108)
    c3 = make_candle(2 * H4, 108, 120, 107, 118)

    # Current time = 12h (C3 closed exactly at 12h)
    now_ms = 3 * H4
    closed = filter_closed_candles([c1, c2, c3], current_time_ms=now_ms)

    assert len(closed) == 3
    assert closed == [c1, c2, c3]


# ==============================================================================
# 2. 4H FVG Detection on Fully Closed Candles
# ==============================================================================
def test_bullish_4h_fvg_detection_and_formation_close_time():
    # Bullish FVG: c3.low > c1.high
    c1 = make_candle(0, 100, 110, 95, 105)        # High = 110
    c2 = make_candle(H4, 105, 130, 104, 128)      # Large impulse
    c3 = make_candle(2 * H4, 128, 135, 115, 132)  # Low = 115 > c1.high (110)

    now_ms = 3 * H4  # c3 is closed
    fvgs = compute_all_active_4h_fvgs([c1, c2, c3], current_time_ms=now_ms)

    assert len(fvgs) == 1
    f = fvgs[0]
    assert f.direction == "Bullish"
    assert f.bottom == 110.0
    assert f.top == 115.0
    assert f.formed_at == 2 * H4
    assert f.close_timestamp == 3 * H4  # Formed on c3 CLOSE


def test_bearish_4h_fvg_detection():
    # Bearish FVG: c3.high < c1.low
    c1 = make_candle(0, 150, 155, 140, 142)       # Low = 140
    c2 = make_candle(H4, 142, 143, 120, 122)      # Impulse down
    c3 = make_candle(2 * H4, 122, 135, 118, 120)  # High = 135 < c1.low (140)

    now_ms = 3 * H4
    fvgs = compute_all_active_4h_fvgs([c1, c2, c3], current_time_ms=now_ms)

    assert len(fvgs) == 1
    f = fvgs[0]
    assert f.direction == "Bearish"
    assert f.bottom == 135.0
    assert f.top == 140.0
    assert f.close_timestamp == 3 * H4


def test_unfinished_candle_cannot_form_fvg():
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    # c3 is live / unfinished
    c3_live = make_candle(2 * H4, 128, 135, 115, 132)

    now_ms = int(2.5 * H4)  # inside c3, not closed
    fvgs = compute_all_active_4h_fvgs([c1, c2, c3_live], current_time_ms=now_ms)

    assert len(fvgs) == 0  # c3 was not closed!


# ==============================================================================
# 3. Invalidation Rules
# ==============================================================================
def test_bullish_fvg_invalidated_when_price_breaches_bottom():
    c1 = make_candle(0, 100, 110, 95, 105)        # bottom = 110
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)  # top = 115
    # c4 wicks down to 108 (< 110 bottom) -> Invalidation!
    c4 = make_candle(3 * H4, 132, 133, 108, 120)

    now_ms = 4 * H4
    fvgs = compute_all_active_4h_fvgs([c1, c2, c3, c4], current_time_ms=now_ms)
    assert len(fvgs) == 0


def test_bearish_fvg_invalidated_when_price_breaches_top():
    c1 = make_candle(0, 150, 155, 140, 142)       # top = 140
    c2 = make_candle(H4, 142, 143, 120, 122)
    c3 = make_candle(2 * H4, 122, 135, 118, 120)  # bottom = 135
    # c4 wicks up to 142 (> 140 top) -> Invalidation!
    c4 = make_candle(3 * H4, 120, 142, 119, 138)

    now_ms = 4 * H4
    fvgs = compute_all_active_4h_fvgs([c1, c2, c3, c4], current_time_ms=now_ms)
    assert len(fvgs) == 0


def test_fvg_survives_when_price_respects_boundary():
    c1 = make_candle(0, 100, 110, 95, 105)        # bottom = 110
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)  # top = 115
    # c4 dips to 112 (inside [110, 115]), does NOT breach bottom (110)
    c4 = make_candle(3 * H4, 132, 133, 112, 125)

    now_ms = 4 * H4
    fvgs = compute_all_active_4h_fvgs([c1, c2, c3, c4], current_time_ms=now_ms)
    assert len(fvgs) == 1
    assert fvgs[0].bottom == 110.0


# ==============================================================================
# 4. HTFFVGCache Bootstrap & Delta Scan Tests
# ==============================================================================
def test_htf_fvg_cache_bootstrap():
    cache = HTFFVGCache()
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)

    now_ms = 3 * H4
    active = cache.bootstrap("BTC", [c1, c2, c3], current_time_ms=now_ms)

    assert cache.is_bootstrapped("BTC")
    assert len(active) == 1
    assert active[0].bottom == 110.0
    assert cache.last_processed_candle_ts["BTC"] == 2 * H4
    assert len(cache.last_closed_candles["BTC"]) == 2


def test_htf_fvg_cache_delta_detects_new_fvg():
    cache = HTFFVGCache()
    c1 = make_candle(0, 100, 105, 95, 102)
    c2 = make_candle(H4, 102, 106, 100, 104)
    c3 = make_candle(2 * H4, 104, 108, 101, 106)  # No FVG initially

    cache.bootstrap("ETH", [c1, c2, c3], current_time_ms=3 * H4)
    assert len(cache.get_active_fvgs("ETH")) == 0

    # Next 2 candles form an FVG with c3:
    # c3 (2*H4): high = 108
    # c4 (3*H4): impulse
    # c5 (4*H4): low = 112 > c3.high (108) -> New Bullish FVG [108 - 112]!
    c4 = make_candle(3 * H4, 106, 125, 105, 124)
    c5 = make_candle(4 * H4, 124, 130, 112, 128)

    now_ms = 5 * H4
    updated = cache.update_delta(
        "ETH",
        recent_candles_4h=[c4, c5],
        current_price=128.0,
        current_time_ms=now_ms,
    )

    assert len(updated) == 1
    assert updated[0].direction == "Bullish"
    assert updated[0].bottom == 108.0
    assert updated[0].top == 112.0
    assert cache.last_processed_candle_ts["ETH"] == 4 * H4


def test_htf_fvg_cache_delta_invalidates_existing_fvg():
    cache = HTFFVGCache()
    c1 = make_candle(0, 100, 110, 95, 105)        # bottom = 110
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)  # top = 115

    cache.bootstrap("SOL", [c1, c2, c3], current_time_ms=3 * H4)
    assert len(cache.get_active_fvgs("SOL")) == 1

    # New delta candle c4 wicks down to 105 (< 110 bottom)
    c4 = make_candle(3 * H4, 132, 133, 105, 120)

    now_ms = 4 * H4
    updated = cache.update_delta(
        "SOL",
        recent_candles_4h=[c4],
        current_price=120.0,
        current_time_ms=now_ms,
    )

    assert len(updated) == 0
    assert len(cache.get_active_fvgs("SOL")) == 0


def test_htf_fvg_cache_delta_invalidates_on_live_price():
    cache = HTFFVGCache()
    c1 = make_candle(0, 100, 110, 95, 105)        # bottom = 110
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)  # top = 115

    cache.bootstrap("PAXG", [c1, c2, c3], current_time_ms=3 * H4)
    assert len(cache.get_active_fvgs("PAXG")) == 1

    # No new closed candles, but live price drops below 110 (e.g. 109.0)
    updated = cache.update_delta(
        "PAXG",
        recent_candles_4h=[],
        current_price=109.0,
        current_time_ms=3 * H4,
    )

    assert len(updated) == 0
    assert len(cache.get_active_fvgs("PAXG")) == 0


def test_htf_fvg_cache_ignores_unfinished_candle_in_delta():
    cache = HTFFVGCache()
    c1 = make_candle(0, 100, 105, 95, 102)
    c2 = make_candle(H4, 102, 106, 100, 104)
    c3 = make_candle(2 * H4, 104, 108, 101, 106)

    cache.bootstrap("AVAX", [c1, c2, c3], current_time_ms=3 * H4)

    # c4 is still open/unfinished
    c4_live = make_candle(3 * H4, 106, 130, 115, 128)

    now_ms = int(3.5 * H4)  # halfway through c4
    updated = cache.update_delta(
        "AVAX",
        recent_candles_4h=[c4_live],
        current_price=128.0,
        current_time_ms=now_ms,
    )

    # c4 was open, so it should not be processed for new FVG formation
    assert cache.last_processed_candle_ts["AVAX"] == 2 * H4
    assert len(updated) == 0


# ==============================================================================
# 5. Step 2: Touch Detection & Most Recent Touched Anchor Tests
# ==============================================================================
def test_4h_touch_strictly_post_close():
    # Bullish FVG [110 - 115] formed by c1, c2, c3
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    # c3 reaches low of 112 during formation, but c3 CANNOT touch its own FVG!
    c3 = make_candle(2 * H4, 128, 135, 112, 132)

    fvg = FVG("Bullish", 115, 110, c1, c2, c3, formed_at=2 * H4, timeframe="4h")

    # With only c1, c2, c3, touch must be None
    touch = get_4h_fvg_most_recent_touch([c1, c2, c3], fvg, current_price=132.0)
    assert touch is None

    # When c4 (strictly post-close) dips into [110, 115] with low=113
    c4 = make_candle(3 * H4, 132, 133, 113, 125)
    touch_c4 = get_4h_fvg_most_recent_touch([c1, c2, c3, c4], fvg, current_price=125.0)
    assert touch_c4 is not None
    assert touch_c4.touch_timestamp == 3 * H4
    assert touch_c4.touch_timeframe == "4h"


def test_most_recent_touched_fvg_selection():
    # Create two Bullish 4H FVGs:
    # FVG A formed earlier, touched at 3 * H4
    c1_a = make_candle(0, 100, 110, 95, 105)
    c2_a = make_candle(H4, 105, 130, 104, 128)
    c3_a = make_candle(2 * H4, 128, 135, 115, 132)
    fvg_a = FVG("Bullish", 115, 110, c1_a, c2_a, c3_a, formed_at=2 * H4, timeframe="4h")

    # FVG B formed later, touched at 5 * H4
    c1_b = make_candle(3 * H4, 130, 140, 125, 138)
    c2_b = make_candle(4 * H4, 138, 160, 136, 158)
    c3_b = make_candle(5 * H4, 158, 165, 145, 162)
    fvg_b = FVG("Bullish", 145, 140, c1_b, c2_b, c3_b, formed_at=5 * H4, timeframe="4h")

    # Subsequent candles:
    # c4 dips into FVG A [110 - 115] at 3 * H4
    c4 = make_candle(3 * H4, 132, 133, 112, 128)
    # c6 dips into FVG B [140 - 145] at 6 * H4
    c6 = make_candle(6 * H4, 162, 163, 142, 155)

    all_candles = [c1_a, c2_a, c3_a, c4, c1_b, c2_b, c3_b, c6]

    # get_most_recent_touched_4h_fvg must select FVG B because its touch (6 * H4) is newer than A's touch (3 * H4)
    anchor = get_most_recent_touched_4h_fvg(
        candles_4h=all_candles,
        active_fvgs=[fvg_a, fvg_b],
        current_price=155.0,
    )

    assert anchor is not None
    assert anchor.fvg.bottom == 140.0
    assert anchor.fvg.top == 145.0
    assert anchor.latest_touch_timestamp == 6 * H4


def test_currently_inside_takes_priority_over_older_touch():
    # FVG A: touched 12 hours ago, price moved out
    c1_a = make_candle(0, 100, 110, 95, 105)
    c2_a = make_candle(H4, 105, 130, 104, 128)
    c3_a = make_candle(2 * H4, 128, 135, 115, 132)
    fvg_a = FVG("Bullish", 115, 110, c1_a, c2_a, c3_a, formed_at=2 * H4, timeframe="4h")
    c4_a = make_candle(3 * H4, 132, 133, 112, 128)

    # FVG B: formed on 27-Aug, price is CURRENTLY INSIDE at 98.0
    c1_b = make_candle(4 * H4, 90, 96, 85, 95)
    c2_b = make_candle(5 * H4, 95, 105, 94, 103)
    c3_b = make_candle(6 * H4, 103, 106, 99, 104)
    fvg_b = FVG("Bullish", 99, 96, c1_b, c2_b, c3_b, formed_at=6 * H4, timeframe="4h")

    all_candles = [c1_a, c2_a, c3_a, c4_a, c1_b, c2_b, c3_b]

    # Live price is 98.0 -> inside FVG B [96 - 99]!
    anchor = get_most_recent_touched_4h_fvg(
        candles_4h=all_candles,
        active_fvgs=[fvg_a, fvg_b],
        current_price=98.0,
    )

    assert anchor is not None
    assert anchor.fvg == fvg_b
    assert anchor.is_currently_inside is True


def test_untouched_fvgs_return_none():
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)
    fvg = FVG("Bullish", 115, 110, c1, c2, c3, formed_at=2 * H4, timeframe="4h")

    # c4 stays high (120 - 130), never enters [110 - 115]
    c4 = make_candle(3 * H4, 132, 134, 120, 130)

    anchor = get_most_recent_touched_4h_fvg(
        candles_4h=[c1, c2, c3, c4],
        active_fvgs=[fvg],
        current_price=130.0,
    )
    assert anchor is None


def test_exact_ltf_touch_refinement():
    # 4H FVG formed at 2 * H4, closed at 3 * H4
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)
    fvg = FVG("Bullish", 115, 110, c1, c2, c3, formed_at=2 * H4, timeframe="4h")

    # 4H candle c4 starts at 3 * H4
    c4_4h = make_candle(3 * H4, 132, 133, 112, 125)

    # 15m candles during c4:
    # Bar 1 (3 * H4): High=133, Low=128 (no touch)
    ltf_1 = make_candle(3 * H4, 132, 133, 128, 130)
    # Bar 2 (3 * H4 + 15m): Dips to 112 (EXACT TOUCH!)
    m15 = 15 * 60 * 1000
    ltf_2 = make_candle(3 * H4 + m15, 130, 131, 112, 118)

    anchor = get_most_recent_touched_4h_fvg(
        candles_4h=[c1, c2, c3, c4_4h],
        active_fvgs=[fvg],
        current_price=118.0,
        candles_ltf=[ltf_1, ltf_2],
        ltf_timeframe="15m",
    )

    assert anchor is not None
    assert anchor.touch_timestamp == 3 * H4 + m15  # Exactly Bar 2!
    assert anchor.touch_timeframe == "15m"

