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
    compute_all_active_4h_fvgs,
    filter_closed_candles,
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
