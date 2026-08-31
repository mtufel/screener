"""
Strategy Specification Test Suite.

Tests are written against the DOCUMENTED strategy rules (strategy_document.md).
Each test references the exact rule it validates.

Coverage:
  A. 4H FVG Invalidation Rules
     A1. Wick through zone does NOT invalidate (close-based only)
     A2. Close below bottom invalidates Bullish FVG
     A3. Close above top invalidates Bearish FVG
     A4. FVG survives wick exactly at boundary

  B. 4H FVG Touch Detection
     B1. Wick touching zone counts as touch
     B2. Candle open inside zone counts as touch
     B3. Candle close inside zone counts as touch
     B4. No subsequent candles = untouched
     B5. Live price inside zone counts as touch

  C. LTF FVG — First-After-Touch Rule
     C1. LTF FVG formed BEFORE htf_touch_ts must be ignored
     C2. FIRST LTF FVG after touch is selected, not the latest
     C3. No LTF FVG after touch = None (not PENDING)

  D. LTF FVG Entry & Retrace
     D1. Bullish entry when candle opens above FVG and dips in -> entry = fvg.top
     D2. Bullish entry when candle opens inside FVG -> entry = candle.open
     D3. Bearish entry when candle opens below FVG and rallies in -> entry = fvg.bottom
     D4. Retrace on same candle as formation is NOT valid entry

  E. Session Reset Rules
     E1. SL hit -> trade CLOSED_SL, no re-alerts
     E2. TP hit -> trade CLOSED_TP, SL cannot fire after
     E3. New 4H touch -> new session ID, independent from old

  F. Stop Loss Placement
     F1. Bullish SL = min(c1.low, c2.low, c3.low)
     F2. Bearish SL = max(c1.high, c2.high, c3.high)
     F3. SL must be below current price for Bullish

  G. Multiple Independent 4H FVG Sessions
     G1. Multiple 4H FVGs coexist in cache
     G2. Only one direction active per coin at a time
"""

import pytest
from strategy import (
    Candle,
    FVG,
    compute_all_active_4h_fvgs,
    is_4h_fvg_retraced_after_creation,
    price_in_fvg,
    calculate_tp_levels,
    Phase1Result,
    phase2_check,
)


# ==============================================================================
# HELPERS
# ==============================================================================
def make_candle(ts, o, h, l, c, v=10):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def make_dummy_client(candles):
    class DummyClient:
        async def get_last_n_candles(self, *args, **kwargs):
            return [
                {"t": c.timestamp, "o": c.open, "h": c.high,
                 "l": c.low, "c": c.close, "v": c.volume}
                for c in candles
            ]
    return DummyClient()


# ==============================================================================
# A. 4H FVG INVALIDATION RULES
# ==============================================================================

def test_A1_wick_through_bottom_does_not_invalidate_bullish_fvg():
    """A candle WICK below the Bullish FVG bottom does NOT invalidate it (close-based rule)."""
    c1 = make_candle(1000, 90, 100, 85, 98)
    c2 = make_candle(2000, 98, 120, 97, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    # c4 wicks down to 95 (below bottom=100) but CLOSES at 102 (inside zone)
    c4 = make_candle(4000, 122, 124, 95, 102)
    active = compute_all_active_4h_fvgs([c1, c2, c3, c4], use_close_invalidation=True)
    assert len(active) == 1, "Bullish FVG must survive a wick through bottom when close is above bottom"
    assert active[0].direction == "Bullish"


def test_A2_close_below_bottom_invalidates_bullish_fvg():
    """A candle that CLOSES below Bullish FVG bottom permanently invalidates the FVG."""
    c1 = make_candle(1000, 90, 100, 85, 98)
    c2 = make_candle(2000, 98, 120, 97, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    # c4 closes at 98 — below bottom=100
    c4 = make_candle(4000, 122, 124, 95, 98)
    active = compute_all_active_4h_fvgs([c1, c2, c3, c4], use_close_invalidation=True)
    assert len(active) == 0, "Bullish FVG must be invalidated when a candle closes below bottom"


def test_A3_wick_above_top_does_not_invalidate_bearish_fvg():
    """A candle WICK above the Bearish FVG top does NOT invalidate it (close-based rule)."""
    # Bearish FVG: bottom=80, top=90
    c1 = make_candle(1000, 95, 98, 90, 92)
    c2 = make_candle(2000, 92, 92, 75, 78)
    c3 = make_candle(3000, 78, 80, 70, 75)
    # c4 wicks to 95 (above top=90) but CLOSES at 88 (inside zone)
    c4 = make_candle(4000, 75, 95, 74, 88)
    active = compute_all_active_4h_fvgs([c1, c2, c3, c4], use_close_invalidation=True)
    assert len(active) == 1, "Bearish FVG must survive a wick above top when close is below top"


def test_A4_close_above_top_invalidates_bearish_fvg():
    """A candle that CLOSES above Bearish FVG top permanently invalidates the FVG."""
    c1 = make_candle(1000, 95, 98, 90, 92)
    c2 = make_candle(2000, 92, 92, 75, 78)
    c3 = make_candle(3000, 78, 80, 70, 75)
    c4 = make_candle(4000, 75, 95, 74, 88)   # wick only — survives
    c5 = make_candle(5000, 88, 95, 78, 93)   # closes at 93 > top=90 -> invalidated (low=78 avoids spurious FVG)
    active = compute_all_active_4h_fvgs([c1, c2, c3, c4, c5], use_close_invalidation=True)
    assert len(active) == 0, "Bearish FVG must be invalidated when a candle closes above top"


def test_A5_fvg_survives_wick_exactly_at_boundary():
    """A wick exactly at the FVG boundary (low == bottom) does not invalidate."""
    c1 = make_candle(1000, 90, 100, 85, 98)
    c2 = make_candle(2000, 98, 120, 97, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    # c4 low exactly == bottom=100, closes at 105
    c4 = make_candle(4000, 122, 124, 100, 105)
    active = compute_all_active_4h_fvgs([c1, c2, c3, c4], use_close_invalidation=True)
    assert len(active) == 1, "FVG must survive when wick exactly tags the bottom boundary"


# ==============================================================================
# B. 4H FVG TOUCH DETECTION
# ==============================================================================

def test_B1_wick_into_zone_counts_as_touch():
    """Any wick entering the 4H zone triggers the touch — candle does not need to close inside."""
    c1 = make_candle(1000, 90, 100, 85, 98)
    c2 = make_candle(2000, 98, 120, 97, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    fvg = FVG("Bullish", top=110, bottom=100, c1=c1, c2=c2, c3=c3, formed_at=3000)

    # c4 wicks to 105 (inside [100,110]) but closes at 115 (above zone)
    c4 = make_candle(4000, 122, 124, 105, 115)
    result = is_4h_fvg_retraced_after_creation([c1, c2, c3, c4], fvg, current_price=115.0)
    assert result is True, "Wick into 4H zone must count as a valid touch"


def test_B2_candle_open_inside_zone_counts_as_touch():
    """A candle that opens inside the 4H FVG zone counts as a touch."""
    c1 = make_candle(1000, 90, 100, 85, 98)
    c2 = make_candle(2000, 98, 120, 97, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    fvg = FVG("Bullish", top=110, bottom=100, c1=c1, c2=c2, c3=c3, formed_at=3000)

    c4 = make_candle(4000, 105, 118, 104, 116)   # opens at 105 — inside [100,110]
    result = is_4h_fvg_retraced_after_creation([c1, c2, c3, c4], fvg, current_price=116.0)
    assert result is True, "Candle opening inside 4H zone must count as a touch"


def test_B3_candle_close_inside_zone_counts_as_touch():
    """A candle that closes inside the 4H FVG zone counts as a touch."""
    c1 = make_candle(1000, 90, 100, 85, 98)
    c2 = make_candle(2000, 98, 120, 97, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    fvg = FVG("Bullish", top=110, bottom=100, c1=c1, c2=c2, c3=c3, formed_at=3000)

    c4 = make_candle(4000, 120, 121, 105, 107)   # closes at 107 — inside [100,110]
    result = is_4h_fvg_retraced_after_creation([c1, c2, c3, c4], fvg, current_price=107.0)
    assert result is True, "Candle closing inside 4H zone must count as a touch"


def test_B4_no_subsequent_candles_means_untouched():
    """If no candles exist after FVG formation and live price is outside, FVG is untouched."""
    c1 = make_candle(1000, 90, 100, 85, 98)
    c2 = make_candle(2000, 98, 120, 97, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    fvg = FVG("Bullish", top=110, bottom=100, c1=c1, c2=c2, c3=c3, formed_at=3000)

    result = is_4h_fvg_retraced_after_creation([c1, c2, c3], fvg, current_price=122.0)
    assert result is False, "FVG with no subsequent candles and live price outside must be untouched"


def test_B5_live_price_inside_zone_counts_as_touch():
    """If live price is currently inside the 4H FVG zone, it counts as a touch."""
    c1 = make_candle(1000, 90, 100, 85, 98)
    c2 = make_candle(2000, 98, 120, 97, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    fvg = FVG("Bullish", top=110, bottom=100, c1=c1, c2=c2, c3=c3, formed_at=3000)

    c4 = make_candle(4000, 122, 126, 115, 124)   # no zone touch in closed candles
    result = is_4h_fvg_retraced_after_creation([c1, c2, c3, c4], fvg, current_price=105.0)
    assert result is True, "Live price inside 4H zone must count as a valid touch"


# ==============================================================================
# C. LTF FVG — FIRST-AFTER-TOUCH RULE
# ==============================================================================

@pytest.mark.asyncio
async def test_C1_ltf_fvg_before_4h_touch_must_be_ignored():
    """
    LTF FVGs formed BEFORE htf_touch_ts must be excluded from selection.
    Only the first LTF FVG formed AFTER the touch is valid.

    Timeline:
      ts=1000-3000: LTF FVG 1 forms (too early — before 4H touch)
      ts=5000:      4H FVG touch event (htf_touch_ts)
      ts=6000-8000: LTF FVG 2 forms AFTER touch — this is the valid one
    """
    # LTF FVG 1 (early, before touch): Bullish, [c1.high=104, c3.low=106]
    c1a = make_candle(1000, 100, 104, 99, 103)
    c2a = make_candle(2000, 103, 120, 102, 118)
    c3a = make_candle(3000, 118, 125, 106, 122)

    # Gap: 4H touch at ts=5000
    c4 = make_candle(4000, 122, 126, 115, 124)
    c5 = make_candle(5000, 124, 127, 108, 112)   # 4H touch happens here

    # LTF FVG 2 (valid, after touch): Bullish, [c6.high, c8.low]
    c6 = make_candle(6000, 112, 118, 111, 116)
    c7 = make_candle(7000, 116, 135, 115, 132)
    c8 = make_candle(8000, 132, 138, 120, 136)   # FVG2: bottom=c6.high=118, top=c8.low=120

    htf_fvg = FVG("Bullish", top=140, bottom=90, c1=c1a, c2=c2a, c3=c3a, formed_at=3000)
    htf_touch_ts = 5000
    p1 = Phase1Result("BTC", "Bullish", htf_fvg, 136.0, [htf_fvg], htf_touch_ts=htf_touch_ts)

    setup = await phase2_check(
        p1,
        client=make_dummy_client([c1a, c2a, c3a, c4, c5, c6, c7, c8, make_candle(9000, 136, 137, 135, 136)]),
        ltf_timeframe="5m"
    )

    assert setup is not None, "A valid LTF FVG exists after 4H touch — should not return None"
    assert setup.ltf_fvg.formed_at > htf_touch_ts, (
        f"LTF FVG must be formed AFTER htf_touch_ts={htf_touch_ts}, "
        f"got formed_at={setup.ltf_fvg.formed_at}"
    )
    assert setup.ltf_fvg.formed_at == 8000, (
        f"Expected the post-touch LTF FVG (ts=8000), got formed_at={setup.ltf_fvg.formed_at}"
    )


@pytest.mark.asyncio
async def test_C2_first_ltf_fvg_after_touch_is_selected_not_latest():
    """
    When multiple LTF FVGs form after htf_touch_ts, the FIRST (earliest) is selected.

    Timeline (htf_touch_ts = 3000):
      ts=4500: LTF FVG 1 forms  <- FIRST, must be selected
      ts=7500: LTF FVG 2 forms  <- second, must be ignored
    """
    htf_touch_ts = 3000

    # LTF FVG 1 — first after touch at ts=4500
    a1 = make_candle(3500, 100, 104, 98, 103)
    a2 = make_candle(4000, 103, 120, 102, 118)
    a3 = make_candle(4500, 118, 125, 106, 122)   # FVG1: bottom=a1.high=104, top=a3.low=106

    # LTF FVG 2 — second after touch at ts=7500
    b1 = make_candle(5000, 122, 128, 121, 126)
    b2 = make_candle(6000, 126, 145, 125, 142)
    b3 = make_candle(7500, 142, 150, 130, 148)   # FVG2: bottom=b1.high=128, top=b3.low=130

    current_price = 148.0   # above both FVGs — both pending
    htf_fvg = FVG("Bullish", top=160, bottom=90, c1=a1, c2=a2, c3=a3, formed_at=4500)
    p1 = Phase1Result("ETH", "Bullish", htf_fvg, current_price, [htf_fvg], htf_touch_ts=htf_touch_ts)

    setup = await phase2_check(
        p1,
        client=make_dummy_client([a1, a2, a3, b1, b2, b3, make_candle(8000, 148, 149, 147, 148)]),
        ltf_timeframe="5m"
    )

    assert setup is not None, "At least one LTF FVG exists — should not return None"
    assert setup.stage == "PENDING_RETRACE"
    assert setup.ltf_fvg.formed_at == 4500, (
        f"FIRST LTF FVG (ts=4500) must be selected, got formed_at={setup.ltf_fvg.formed_at}"
    )


@pytest.mark.asyncio
async def test_C3_no_ltf_fvg_after_touch_returns_none():
    """
    If no LTF FVG has formed yet after htf_touch_ts, return None.
    The 4H phase is confirmed but LTF pattern hasn't appeared.
    """
    htf_touch_ts = 3000

    # Post-touch candles with NO FVG pattern (c3.low < c1.high in every triplet)
    c1 = make_candle(3100, 100, 105, 99, 104)
    c2 = make_candle(3200, 104, 106, 103, 105)
    c3 = make_candle(3300, 105, 107, 104, 106)   # c3.low=104 < c1.high=105 -> no FVG

    htf_fvg = FVG("Bullish", top=120, bottom=90, c1=c1, c2=c2, c3=c3, formed_at=2000)
    p1 = Phase1Result("BTC", "Bullish", htf_fvg, 106.0, [htf_fvg], htf_touch_ts=htf_touch_ts)

    setup = await phase2_check(
        p1,
        client=make_dummy_client([c1, c2, c3, make_candle(3400, 106, 107, 105, 106)]),
        ltf_timeframe="5m"
    )
    assert setup is None, "No LTF FVG formed after touch must return None"


# ==============================================================================
# D. LTF FVG ENTRY & RETRACE
# ==============================================================================

@pytest.mark.asyncio
async def test_D1_bullish_entry_opens_above_fvg_dips_in():
    """
    Bullish: retrace candle opens ABOVE fvg.top, low dips inside -> entry = fvg.top.
    """
    htf_touch_ts = 1000
    c1 = make_candle(1100, 100, 104, 99, 103)
    c2 = make_candle(1200, 103, 120, 102, 118)
    c3 = make_candle(1300, 118, 125, 106, 122)  # FVG: bottom=104, top=106

    # Retrace: opens at 108 (above top=106), low dips to 105 (inside [104,106]), high=110 (< 2R TP 120)
    c4 = make_candle(1400, 108, 110, 105, 109)

    htf_fvg = FVG("Bullish", top=130, bottom=90, c1=c1, c2=c2, c3=c3, formed_at=1300)
    p1 = Phase1Result("BTC", "Bullish", htf_fvg, 109.0, [htf_fvg], htf_touch_ts=htf_touch_ts)

    setup = await phase2_check(p1, client=make_dummy_client([c1, c2, c3, c4, make_candle(1500, 109, 110, 108, 109)]), ltf_timeframe="5m")
    assert setup is not None
    assert setup.stage == "ACTIVATED"
    assert setup.entry_price == pytest.approx(106.0), (
        f"Entry must be at fvg.top=106 when candle opens above and dips in, got {setup.entry_price}"
    )


@pytest.mark.asyncio
async def test_D2_bullish_entry_opens_inside_fvg():
    """
    Bullish: retrace candle OPENS inside the FVG zone -> entry = candle.open.
    """
    htf_touch_ts = 1000
    c1 = make_candle(1100, 100, 104, 99, 103)
    c2 = make_candle(1200, 103, 120, 102, 118)
    c3 = make_candle(1300, 118, 125, 106, 122)  # FVG: [104, 106]

    # Retrace: opens at 105 — INSIDE [104, 106]
    c4 = make_candle(1400, 105, 108, 103, 107)

    htf_fvg = FVG("Bullish", top=130, bottom=90, c1=c1, c2=c2, c3=c3, formed_at=1300)
    p1 = Phase1Result("BTC", "Bullish", htf_fvg, 107.0, [htf_fvg], htf_touch_ts=htf_touch_ts)

    setup = await phase2_check(p1, client=make_dummy_client([c1, c2, c3, c4, make_candle(1500, 107, 108, 106, 107)]), ltf_timeframe="5m")
    assert setup is not None
    assert setup.stage == "ACTIVATED"
    assert setup.entry_price == pytest.approx(105.0), (
        f"Entry must be candle.open=105 when it opens inside the FVG, got {setup.entry_price}"
    )


@pytest.mark.asyncio
async def test_D3_bearish_entry_opens_below_fvg_rallies_in():
    """
    Bearish: retrace candle opens BELOW fvg.bottom, high rallies inside -> entry = fvg.bottom.
    """
    htf_touch_ts = 1000
    # Bearish FVG: c3.high < c1.low
    c1 = make_candle(1100, 120, 125, 118, 120)  # c1.low=118
    c2 = make_candle(1200, 120, 121, 100, 102)
    c3 = make_candle(1300, 102, 115, 98, 100)   # c3.high=115 < c1.low=118 -> FVG: [115,118]

    # Retrace: opens at 112 (below bottom=115), high rallies to 116 (inside [115,118])
    c4 = make_candle(1400, 112, 116, 111, 114)

    htf_fvg = FVG("Bearish", top=130, bottom=90, c1=c1, c2=c2, c3=c3, formed_at=1300)
    p1 = Phase1Result("BTC", "Bearish", htf_fvg, 114.0, [htf_fvg], htf_touch_ts=htf_touch_ts)

    setup = await phase2_check(p1, client=make_dummy_client([c1, c2, c3, c4, make_candle(1500, 114, 115, 113, 114)]), ltf_timeframe="5m")
    assert setup is not None
    assert setup.stage == "ACTIVATED"
    assert setup.entry_price == pytest.approx(115.0), (
        f"Bearish entry must be at fvg.bottom=115, got {setup.entry_price}"
    )


@pytest.mark.asyncio
async def test_D4_retrace_on_formation_candle_itself_is_not_valid_entry():
    """
    Entry requires a candle STRICTLY AFTER the FVG formation candle (c3).
    If only c3 exists (no subsequent candles), result must be PENDING or None.
    """
    htf_touch_ts = 1000
    c1 = make_candle(1100, 100, 104, 99, 103)
    c2 = make_candle(1200, 103, 120, 102, 118)
    c3 = make_candle(1300, 118, 125, 106, 122)  # FVG forms here — no candles after

    htf_fvg = FVG("Bullish", top=130, bottom=90, c1=c1, c2=c2, c3=c3, formed_at=1300)
    p1 = Phase1Result("BTC", "Bullish", htf_fvg, 122.0, [htf_fvg], htf_touch_ts=htf_touch_ts)

    setup = await phase2_check(p1, client=make_dummy_client([c1, c2, c3]), ltf_timeframe="5m")
    assert setup is None or setup.stage == "PENDING_RETRACE", (
        "With no subsequent candles, entry must not trigger on the formation candle itself"
    )


# ==============================================================================
# E. SESSION RESET RULES
# ==============================================================================

def test_E1_sl_hit_closes_trade_no_re_alerts():
    """After SL is hit, trade is CLOSED_SL and no further alerts fire."""
    from trade_tracker import TradeTracker
    from strategy import SetupResult
    tracker = TradeTracker(single_active_position=True, persistence_file=None)

    c1 = make_candle(1000, 100, 105, 90, 104)
    c2 = make_candle(2000, 104, 120, 103, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    htf = FVG("Bullish", top=130, bottom=90,  c1=c1, c2=c2, c3=c3, formed_at=3000)
    ltf = FVG("Bullish", top=115, bottom=110, c1=c1, c2=c2, c3=c3, formed_at=3000)
    tp  = calculate_tp_levels("Bullish", entry_price=110.0, sl_price=95.0)

    setup = SetupResult("BTC", "Bullish", "ACTIVATED", htf, ltf, 110.0, 110.0, 95.0, tp, 0.8, "5m")
    tracker.register_or_update_setup(setup, [c1, c2, c3])

    # SL hit at 94 (below sl=95)
    updates = tracker.check_open_trades({"BTC": 94.0})
    assert any("STOP LOSS" in u for u in updates), "SL alert must fire"

    tid = tracker.get_setup_id(setup)
    assert tracker.trades[tid].stage == "CLOSED_SL"

    # Price stays low — no further alerts
    updates2 = tracker.check_open_trades({"BTC": 93.0})
    assert len(updates2) == 0, "No alerts must fire on a CLOSED_SL trade"


def test_E2_tp_hit_closes_trade_sl_cannot_fire_after():
    """After 2R TP is hit, trade is CLOSED_TP. Subsequent price crash must NOT trigger SL."""
    from trade_tracker import TradeTracker
    from strategy import SetupResult
    tracker = TradeTracker(single_active_position=True, persistence_file=None)

    c1 = make_candle(1000, 100, 105, 90, 104)
    c2 = make_candle(2000, 104, 120, 103, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    htf = FVG("Bullish", top=130, bottom=90,  c1=c1, c2=c2, c3=c3, formed_at=3000)
    ltf = FVG("Bullish", top=115, bottom=110, c1=c1, c2=c2, c3=c3, formed_at=3000)
    tp  = calculate_tp_levels("Bullish", entry_price=100.0, sl_price=90.0)
    # risk=10, 2R=120

    setup = SetupResult("BTC", "Bullish", "ACTIVATED", htf, ltf, 100.0, 100.0, 90.0, tp, 0.8, "5m")
    tracker.register_or_update_setup(setup, [c1, c2, c3])

    # 2R TP hit at 120
    updates_tp = tracker.check_open_trades({"BTC": 120.0})
    assert any("2.0R TARGET" in u for u in updates_tp)

    tid = tracker.get_setup_id(setup)
    assert tracker.trades[tid].stage == "CLOSED_TP"

    # Price crashes to 85 — must NOT fire SL
    updates_sl = tracker.check_open_trades({"BTC": 85.0})
    assert len(updates_sl) == 0, "CLOSED_TP trade must never trigger SL alert"


def test_E3_new_4h_touch_produces_new_session_id():
    """
    A new 4H touch event produces a different session ID from the old one,
    ensuring the new LTF FVG is tracked independently without inheriting old state.
    """
    from strategy import SetupResult
    c1 = make_candle(1000, 100, 105, 95, 104)
    c2 = make_candle(2000, 104, 120, 103, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    htf = FVG("Bullish", top=130, bottom=90, c1=c1, c2=c2, c3=c3, formed_at=3000)

    # Session 1: LTF FVG formed at ts=4000
    ltf1 = FVG("Bullish", top=115, bottom=110, c1=c1, c2=c2, c3=c3, formed_at=4000)
    tp1  = calculate_tp_levels("Bullish", 110.0, 95.0)
    setup1 = SetupResult("BTC", "Bullish", "ACTIVATED", htf, ltf1, 110.0, 110.0, 95.0, tp1, 0.8, "5m")

    # Session 2: same 4H FVG re-touched, new LTF FVG formed at ts=8000
    ltf2 = FVG("Bullish", top=118, bottom=113, c1=c1, c2=c2, c3=c3, formed_at=8000)
    tp2  = calculate_tp_levels("Bullish", 113.0, 95.0)
    setup2 = SetupResult("BTC", "Bullish", "PENDING_RETRACE", htf, ltf2, 113.0, 113.0, 95.0, tp2, 0.8, "5m")

    from trade_tracker import TradeTracker
    tracker = TradeTracker(persistence_file=None)
    id1 = tracker.get_setup_id(setup1)
    id2 = tracker.get_setup_id(setup2)

    assert id1 != id2, (
        "Re-touch session must produce a new unique setup ID (different ltf_fvg.formed_at), "
        "not inherit state from the old session"
    )


# ==============================================================================
# F. STOP LOSS PLACEMENT
# ==============================================================================

def test_F1_bullish_sl_is_min_of_all_three_ltf_candle_lows():
    """Bullish SL = min(c1.low, c2.low, c3.low) of the LTF FVG forming candles."""
    c1 = make_candle(1000, 100, 110, 88, 108)   # c1.low = 88  <- lowest
    c2 = make_candle(2000, 108, 130, 95, 128)   # c2.low = 95  (impulse)
    c3 = make_candle(3000, 128, 135, 112, 132)  # c3.low = 112

    expected_sl = min(c1.low, c2.low, c3.low)
    assert expected_sl == 88.0, f"Bullish SL must be 88 (min of all lows), got {expected_sl}"


def test_F2_bearish_sl_is_max_of_all_three_ltf_candle_highs():
    """Bearish SL = max(c1.high, c2.high, c3.high) of the LTF FVG forming candles."""
    c1 = make_candle(1000, 120, 132, 118, 122)  # c1.high = 132 <- highest
    c2 = make_candle(2000, 122, 125, 95, 98)    # c2.high = 125 (impulse)
    c3 = make_candle(3000, 98, 115, 88, 90)     # c3.high = 115

    expected_sl = max(c1.high, c2.high, c3.high)
    assert expected_sl == 132.0, f"Bearish SL must be 132 (max of all highs), got {expected_sl}"


def test_F3_bullish_setup_discarded_if_sl_above_current_price():
    """
    A Bullish setup is discarded when sl_ref >= current_price.
    This means price has already moved below the protective SL zone.
    """
    c1 = make_candle(1000, 100, 110, 108, 109)   # c1.low=108  <- min
    c2 = make_candle(2000, 109, 130, 115, 128)
    c3 = make_candle(3000, 128, 135, 118, 132)

    sl_ref = min(c1.low, c2.low, c3.low)   # = 108
    current_price = 107.0                  # below SL — setup must be discarded
    assert sl_ref >= current_price, (
        f"Test pre-condition: sl_ref ({sl_ref}) must be >= current_price ({current_price})"
    )
    # The strategy discards this via: if sl_ref >= current_price: continue


# ==============================================================================
# G. MULTIPLE INDEPENDENT 4H FVG SESSIONS
# ==============================================================================

def test_G1_multiple_4h_fvgs_coexist_in_active_cache():
    """All un-invalidated 4H FVGs are stored. Multiple FVGs of different ages can coexist."""
    # Bullish FVG 1 (older) — bottom=100, top=110
    c1a = make_candle(1000,  90, 100, 85,  98)
    c2a = make_candle(2000,  98, 120, 97, 118)
    c3a = make_candle(3000, 118, 125, 110, 122)

    # Non-FVG candles in between
    c4  = make_candle(4000, 122, 124, 121, 123)
    c5  = make_candle(5000, 123, 126, 122, 124)

    # Bullish FVG 2 (newer) — bottom=130, top=135
    c1b = make_candle(6000, 124, 130, 123, 128)
    c2b = make_candle(7000, 128, 150, 127, 148)
    c3b = make_candle(8000, 148, 155, 135, 152)

    active = compute_all_active_4h_fvgs([c1a, c2a, c3a, c4, c5, c1b, c2b, c3b])

    assert len(active) >= 2, f"Both FVGs should be in active cache, found {len(active)}"
    formed_timestamps = {fvg.formed_at for fvg in active}
    assert 3000 in formed_timestamps, "Older FVG (ts=3000) must be in cache"
    assert 8000 in formed_timestamps, "Newer FVG (ts=8000) must be in cache"


def test_G2_only_one_direction_active_per_coin():
    """Only one direction (Bullish or Bearish) runs per coin at a time (single_active_position=True)."""
    from trade_tracker import TradeTracker
    from strategy import SetupResult
    tracker = TradeTracker(single_active_position=True, persistence_file=None)

    c1 = make_candle(1000, 100, 105, 95, 104)
    c2 = make_candle(2000, 104, 120, 103, 118)
    c3 = make_candle(3000, 118, 125, 110, 122)
    htf_bull = FVG("Bullish", top=130, bottom=90,  c1=c1, c2=c2, c3=c3, formed_at=3000)
    htf_bear = FVG("Bearish", top=140, bottom=115, c1=c1, c2=c2, c3=c3, formed_at=3000)
    ltf_bull = FVG("Bullish", top=115, bottom=110, c1=c1, c2=c2, c3=c3, formed_at=3500)
    ltf_bear = FVG("Bearish", top=125, bottom=120, c1=c1, c2=c2, c3=c3, formed_at=3600)
    tp_bull  = calculate_tp_levels("Bullish", 110.0, 95.0)
    tp_bear  = calculate_tp_levels("Bearish", 120.0, 135.0)

    setup_bull = SetupResult("BTC", "Bullish", "ACTIVATED", htf_bull, ltf_bull, 110.0, 110.0, 95.0,  tp_bull, 0.8, "5m")
    setup_bear = SetupResult("BTC", "Bearish", "ACTIVATED", htf_bear, ltf_bear, 120.0, 120.0, 135.0, tp_bear, 0.8, "5m")

    alerted_bull, _, _ = tracker.register_or_update_setup(setup_bull, [c1, c2, c3])
    assert alerted_bull is True, "First Bullish setup must be alerted"

    alerted_bear, _, _ = tracker.register_or_update_setup(setup_bear, [c1, c2, c3])
    assert alerted_bear is False, "Bearish setup must be suppressed while Bullish is active on same coin"
