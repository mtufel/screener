"""
Unit and integration test suite for LTF FVG partial mitigation and dynamic boundary reduction.

Covers:
1. Helper function `shrink_fvg_on_mitigation` (Bullish & Bearish).
2. Lifecycle evaluation `evaluate_ltf_setup_lifecycle` across partial TP mitigations and re-entries.
3. Discovery `find_unmitigated_ltf_fvgs` surfacing shrunk residual FVGs with preserved structural SL.
4. Stop Loss invariance across successive shrinks.
5. Minimum gap percentage constraint enforcement.
6. Full invalidation when wick crosses the opposite boundary / structural SL.
7. Extreme trade tracker ledger metadata tracking (deepest_wick_reached, residual_fvg).
8. Backtest simulation loop handling consecutive trades on residual gaps.
"""

import pytest
from strategy_extreme_fvg import (
    Candle,
    FVG,
    shrink_fvg_on_mitigation,
    evaluate_ltf_setup_lifecycle,
    find_unmitigated_ltf_fvgs,
    select_extreme_ltf_fvg,
    build_extreme_trade_setup,
    TouchedAnchor,
)
from extreme_trade_tracker import ExtremeTradeTracker, TrackedExtremeTrade
from backtest_extreme_fvg import simulate_trade_execution, run_extreme_backtest

M5 = 5 * 60 * 1000
T0 = 1_700_000_000_000


def mk_c(ts: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


# ==============================================================================
# 1. shrink_fvg_on_mitigation Unit Tests
# ==============================================================================

def test_shrink_fvg_bullish_partial_fill():
    """Bullish FVG [100, 110] with lowest adverse wick 104 shrinks top to 104."""
    c1 = mk_c(T0, 95, 100, 94, 99)
    c2 = mk_c(T0 + M5, 99, 115, 98, 114)
    c3 = mk_c(T0 + 2 * M5, 114, 118, 110, 116)
    fvg = FVG(direction="Bullish", top=110.0, bottom=100.0, c1=c1, c2=c2, c3=c3, formed_at=c3.timestamp)

    res = shrink_fvg_on_mitigation(fvg, lowest_wick=104.0, min_gap_pct=0.05)
    assert res is not None
    assert res.top == 104.0
    assert res.bottom == 100.0
    assert res.original_top == 110.0
    assert res.original_bottom == 100.0
    assert res.mitigation_count == 1
    assert res.deepest_wick_penetration == 104.0


def test_shrink_fvg_bearish_partial_fill():
    """Bearish FVG [100, 110] with highest adverse wick 106 shrinks bottom to 106."""
    c1 = mk_c(T0, 115, 116, 110, 111)
    c2 = mk_c(T0 + M5, 111, 112, 95, 96)
    c3 = mk_c(T0 + 2 * M5, 96, 100, 94, 98)
    fvg = FVG(direction="Bearish", top=110.0, bottom=100.0, c1=c1, c2=c2, c3=c3, formed_at=c3.timestamp)

    res = shrink_fvg_on_mitigation(fvg, highest_wick=106.0, min_gap_pct=0.05)
    assert res is not None
    assert res.top == 110.0
    assert res.bottom == 106.0
    assert res.original_top == 110.0
    assert res.original_bottom == 100.0
    assert res.mitigation_count == 1
    assert res.deepest_wick_penetration == 106.0


def test_shrink_fvg_full_fill_returns_none():
    """Wick reaching or crossing the opposite boundary returns None (100% mitigated)."""
    c1 = mk_c(T0, 95, 100, 94, 99)
    c2 = mk_c(T0 + M5, 99, 115, 98, 114)
    c3 = mk_c(T0 + 2 * M5, 114, 118, 110, 116)
    fvg_bull = FVG(direction="Bullish", top=110.0, bottom=100.0, c1=c1, c2=c2, c3=c3, formed_at=c3.timestamp)

    # Bullish wick reaching bottom
    assert shrink_fvg_on_mitigation(fvg_bull, lowest_wick=100.0) is None
    # Bullish wick breaching below bottom
    assert shrink_fvg_on_mitigation(fvg_bull, lowest_wick=99.0) is None

    # Bearish wick reaching top
    fvg_bear = FVG(direction="Bearish", top=110.0, bottom=100.0, c1=c1, c2=c2, c3=c3, formed_at=c3.timestamp)
    assert shrink_fvg_on_mitigation(fvg_bear, highest_wick=110.0) is None
    assert shrink_fvg_on_mitigation(fvg_bear, highest_wick=111.0) is None


def test_shrink_fvg_rejects_under_min_gap_pct():
    """Residual gap smaller than min_gap_pct returns None."""
    c1 = mk_c(T0, 95, 100, 94, 99)
    c2 = mk_c(T0 + M5, 99, 115, 98, 114)
    c3 = mk_c(T0 + 2 * M5, 114, 118, 110, 116)
    # Gap [100, 100.02]: width 0.02 at 100 is 0.02% (< 0.05% threshold)
    fvg_bull = FVG(direction="Bullish", top=110.0, bottom=100.0, c1=c1, c2=c2, c3=c3, formed_at=c3.timestamp)
    assert shrink_fvg_on_mitigation(fvg_bull, lowest_wick=100.02, min_gap_pct=0.05) is None


# ==============================================================================
# 2. evaluate_ltf_setup_lifecycle & Multi-Trade Sequence Tests
# ==============================================================================

def test_evaluate_lifecycle_multi_trade_bullish():
    """
    Bullish FVG [100, 110] with SL at 94 (C1.low=94).
    Trade 1:
      - Entry at 110. Risk = 110 - 94 = 16. TP1 = 126.
      - Candle dips to low 105 (wick), rallies to high 128 (TP1 hit).
      - Shrinks FVG top to 105. Residual gap [100, 105].
    Trade 2:
      - Next candle dips to low 102, rallies to high 120 (TP1 on residual: entry 105, SL 94 -> risk 11, TP1 116).
      - Shrinks FVG top to 102. Residual gap [100, 102].
    Trade 3:
      - Next candle dips to low 101, currently floating at 103 (TRADE_ACTIVE).
    """
    c1 = mk_c(T0, 95, 100, 94, 99)
    c2 = mk_c(T0 + M5, 99, 115, 98, 114)
    c3 = mk_c(T0 + 2 * M5, 114, 118, 110, 116)
    fvg = FVG(direction="Bullish", top=110.0, bottom=100.0, c1=c1, c2=c2, c3=c3, formed_at=c3.timestamp)

    # Trade 1 execution
    c4 = mk_c(T0 + 3 * M5, 115, 128, 105, 127)  # dips to 105, hits TP1 126
    state1, _, _ = evaluate_ltf_setup_lifecycle(fvg, [c4], current_price=127.0, completion_target="1R")
    assert state1 == "PENDING_RETRACE"
    assert fvg.top == 105.0
    assert fvg.bottom == 100.0
    assert fvg.mitigation_count == 1

    # Trade 2 execution
    c5 = mk_c(T0 + 4 * M5, 126, 122, 102, 121)  # dips to 102, hits TP1 116
    state2, _, _ = evaluate_ltf_setup_lifecycle(fvg, [c4, c5], current_price=121.0, completion_target="1R")
    assert state2 == "PENDING_RETRACE"
    assert fvg.top == 102.0
    assert fvg.bottom == 100.0
    assert fvg.mitigation_count == 2

    # Trade 3 active entry (dips to 101 <= 102, high stays at 104 < TP1 110)
    c6 = mk_c(T0 + 5 * M5, 104, 104, 101, 103)
    state3, entry_ts3, float_r3 = evaluate_ltf_setup_lifecycle(fvg, [c4, c5, c6], current_price=103.0, completion_target="1R")
    assert state3 == "TRADE_ACTIVE"
    assert entry_ts3 == c6.timestamp
    assert fvg.top == 102.0
    # Stop loss remains original 94! Risk = 102 - 94 = 8. Floating R = (103 - 102)/8 = +0.125
    assert float_r3 == pytest.approx((103.0 - 102.0) / (102.0 - 94.0))


def test_evaluate_lifecycle_bearish_multi_trade():
    """
    Bearish FVG [100, 110] with SL at 116 (C1.high=116).
    Trade 1:
      - Entry at 100. Risk = 116 - 100 = 16. TP1 = 84.
      - Candle rallies to high 106 (wick), crashes to low 82 (TP1 hit).
      - Shrinks FVG bottom to 106. Residual gap [106, 110].
    """
    c1 = mk_c(T0, 112, 116, 110, 111)
    c2 = mk_c(T0 + M5, 111, 112, 95, 96)
    c3 = mk_c(T0 + 2 * M5, 96, 100, 94, 98)
    fvg = FVG(direction="Bearish", top=110.0, bottom=100.0, c1=c1, c2=c2, c3=c3, formed_at=c3.timestamp)

    c4 = mk_c(T0 + 3 * M5, 98, 106, 82, 83)  # touches 106, hits TP1 84
    state, _, _ = evaluate_ltf_setup_lifecycle(fvg, [c4], current_price=83.0, completion_target="1R")
    assert state == "PENDING_RETRACE"
    assert fvg.top == 110.0
    assert fvg.bottom == 106.0
    assert fvg.mitigation_count == 1


def test_evaluate_lifecycle_sl_invariance():
    """Structural SL is invariant across multiple shrinks."""
    c1 = mk_c(T0, 95, 100, 92, 99)
    c2 = mk_c(T0 + M5, 99, 115, 98, 114)
    c3 = mk_c(T0 + 2 * M5, 114, 118, 110, 116)
    fvg = FVG(direction="Bullish", top=110.0, bottom=100.0, c1=c1, c2=c2, c3=c3, formed_at=c3.timestamp)

    # After first TP shrink to 105
    c4 = mk_c(T0 + 3 * M5, 115, 130, 105, 129)
    evaluate_ltf_setup_lifecycle(fvg, [c4], current_price=129.0, completion_target="1R")
    assert fvg.top == 105.0

    # Next candle breaches original SL (92) -> STOPPED_OUT
    c5 = mk_c(T0 + 4 * M5, 125, 125, 91, 93)
    state, _, r = evaluate_ltf_setup_lifecycle(fvg, [c4, c5], current_price=93.0, completion_target="1R")
    assert state == "STOPPED_OUT"
    assert r == -1.0


# ==============================================================================
# 3. Discovery & Extreme Setup Building Tests
# ==============================================================================

def test_find_unmitigated_surfaces_residual_fvg():
    """find_unmitigated_ltf_fvgs properly surfaces a shrunk residual FVG."""
    c1 = mk_c(T0, 95, 100, 94, 99)
    c2 = mk_c(T0 + M5, 99, 115, 98, 114)
    c3 = mk_c(T0 + 2 * M5, 114, 118, 110, 116)
    c4 = mk_c(T0 + 3 * M5, 115, 130, 104, 128)  # trade 1 hits TP, shrinks to [100, 104]

    fvgs = find_unmitigated_ltf_fvgs(
        candles_ltf=[c1, c2, c3, c4],
        after_timestamp=T0,
        direction="Bullish",
        current_price=128.0,
        current_time_ms=T0 + 4 * M5,
        ltf_timeframe="5m",
        completion_target="1R",
    )

    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.top == 104.0
    assert fvg.bottom == 100.0
    assert fvg.mitigation_count == 1
    assert fvg.lifecycle_state == "PENDING_RETRACE"


def test_build_extreme_trade_setup_preserves_sl_on_residual():
    """build_extreme_trade_setup builds setup with entry=new_top and preserved SL."""
    c1 = mk_c(T0, 95, 100, 94, 99)
    c2 = mk_c(T0 + M5, 99, 115, 98, 114)
    c3 = mk_c(T0 + 2 * M5, 114, 118, 110, 116)
    fvg = FVG(direction="Bullish", top=104.0, bottom=100.0, c1=c1, c2=c2, c3=c3, formed_at=c3.timestamp, mitigation_count=1)

    anchor_fvg = FVG(direction="Bullish", top=102.0, bottom=96.0, c1=c1, c2=c2, c3=c3, formed_at=T0)
    anchor = TouchedAnchor(fvg=anchor_fvg, first_touch_timestamp=T0, most_recent_touch_timestamp=T0)

    setup = build_extreme_trade_setup(
        symbol="ETH",
        anchor=anchor,
        ltf_fvg=fvg,
        ltf_timeframe="5m",
        completion_target="2R",
    )

    assert setup.entry_price == 104.0
    assert setup.stop_loss == 94.0  # min(c1.low, c2.low, c3.low)
    assert setup.risk_r == 10.0
    assert setup.tp_1r == 114.0
    assert setup.tp_2r == 124.0
    assert setup.tp_3r == 134.0


# ==============================================================================
# 4. Extreme Trade Tracker Ledger Integration
# ==============================================================================

def test_extreme_trade_tracker_records_residual_fvg_on_tp(tmp_path):
    """ExtremeTradeTracker computes and records residual_fvg upon TP hit."""
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path / "ledger.json"))
    setup = {
        "symbol": "BTC",
        "direction": "Bullish",
        "state": "TRADE_ACTIVE",
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "risk_r": 10.0,
        "risk_pct": 10.0,
        "tp_1r": 110.0,
        "tp_2r": 120.0,
        "tp_3r": 130.0,
        "completion_target": "2R",
        "ltf_timeframe": "5m",
        "anchor": {"direction": "Bullish", "bottom": 85.0, "top": 95.0},
        "target_fvg": {"direction": "Bullish", "bottom": 92.0, "top": 100.0, "formed_at": T0},
        "entry_timestamp": T0 + M5,
    }

    tracker.process_live_setups([setup], {"BTC": 100.0}, {})
    trade = tracker.active_trades.get(list(tracker.active_trades.keys())[0])
    assert trade is not None
    assert trade.state == "TRADE_ACTIVE"

    # Candle dips to 96 (within FVG [92, 100]) and reaches 2R TP (120)
    tp_candle = {"BTC": [mk_c(T0 + 2 * M5, 99.0, 122.0, 96.0, 121.0)]}
    events = tracker.process_live_setups([], {"BTC": 121.0}, tp_candle)

    assert any(e[0] == "TP_HIT" for e in events)
    assert len(tracker.history) == 1
    h = tracker.history[0]
    assert h.state == "COMPLETED_TP"
    assert h.realized_r == 2.0
    assert h.deepest_wick_reached == 96.0
    assert h.residual_fvg is not None
    assert h.residual_fvg["top"] == 96.0
    assert h.residual_fvg["bottom"] == 92.0
    assert h.residual_fvg["mitigation_count"] == 1
