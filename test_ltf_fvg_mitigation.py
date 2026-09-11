"""
Unit and Integration tests for the Modular Object-Oriented Residual FVG Engine.
Tests cover:
1. Pure OOP factory `ResidualFVGEngine.create_residual` & `fvg.derive_residual` (Bullish & Bearish shrinkage, SL invariance, immutability, full breach, min_gap_pct filter).
2. OOP State Machine `TradeLifecycleEvaluator` with exact active-to-close adverse wick tracking.
3. Live Trade Tracker deduplication and in-place pending update.
4. Backtester sequential residual re-entry execution (multi-trade vs classic single-trade).
"""

import pytest
from strategy_extreme_fvg import (
    Candle,
    FVG,
    create_residual_fvg,
    evaluate_ltf_setup_lifecycle,
)
from residual_fvg_engine import ResidualFVGEngine, TradeLifecycleEvaluator
from extreme_trade_tracker import ExtremeTradeTracker
from backtest_extreme_fvg import run_extreme_backtest

FIVE_MIN_MS = 5 * 60 * 1000
T0 = 1_700_000_000_000


def mk_c(ts, o, h, l, c, vol=100.0):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=vol)


# ==============================================================================
# 1. Pure Factory `ResidualFVGEngine` & `fvg.derive_residual` Tests
# ==============================================================================

def test_bullish_residual_fvg_shrinkage_and_sl_invariance():
    """Bullish: top shrinks down to deepest adverse wick, bottom & structural SL remain invariant."""
    c1 = mk_c(T0, 100.0, 102.0, 99.0, 101.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 101.5, 110.0, 101.0, 109.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 109.5, 112.0, 108.0, 111.0)
    
    # Original Bullish FVG: [bottom=102.0, top=108.0]
    # Structural SL is min(c1.l, c2.l, c3.l) == 99.0
    orig_fvg = FVG(
        direction="Bullish",
        top=108.0,
        bottom=102.0,
        c1=c1,
        c2=c2,
        c3=c3,
        formed_at=T0 + 2 * FIVE_MIN_MS,
        timeframe="5m",
        original_top=108.0,
        original_bottom=102.0,
        mitigation_count=0,
    )
    
    # Deepest wick penetrated down to 105.0 during trade
    residual = ResidualFVGEngine.create_residual(orig_fvg, deepest_wick=105.0, min_gap_pct=0.01)
    
    assert residual is not None
    assert residual.direction == "Bullish"
    assert residual.top == 105.0  # Shrunk to deepest wick
    assert residual.bottom == 102.0  # Unchanged
    # Structural SL invariant: min(c1.l, c2.l, c3.l) == 99.0
    assert min(residual.c1.low, residual.c2.low, residual.c3.low) == 99.0
    assert residual.original_top == 108.0
    assert residual.original_bottom == 102.0
    assert residual.mitigation_count == 1
    assert residual.deepest_wick_penetration == 105.0
    assert residual.formed_at == orig_fvg.formed_at
    
    # Test instance method fvg.derive_residual()
    method_residual = orig_fvg.derive_residual(deepest_wick=105.0, min_gap_pct=0.01)
    assert method_residual is not None
    assert method_residual.top == 105.0
    
    # Verify original FVG was NOT mutated (pure functional requirement)
    assert orig_fvg.top == 108.0
    assert orig_fvg.mitigation_count == 0


def test_bearish_residual_fvg_shrinkage_and_sl_invariance():
    """Bearish: bottom rises up to deepest adverse wick, top & structural SL remain invariant."""
    c1 = mk_c(T0, 110.0, 111.0, 108.0, 108.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 108.5, 109.0, 100.0, 100.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 100.5, 102.0, 99.0, 99.5)
    
    # Original Bearish FVG: [bottom=102.0, top=108.0]
    # Structural SL is max(c1.h, c2.h, c3.h) == 111.0
    orig_fvg = FVG(
        direction="Bearish",
        top=108.0,
        bottom=102.0,
        c1=c1,
        c2=c2,
        c3=c3,
        formed_at=T0 + 2 * FIVE_MIN_MS,
        timeframe="5m",
        original_top=108.0,
        original_bottom=102.0,
        mitigation_count=0,
    )
    
    # Deepest wick penetrated up to 106.0 during trade
    residual = ResidualFVGEngine.create_residual(orig_fvg, deepest_wick=106.0, min_gap_pct=0.01)
    
    assert residual is not None
    assert residual.direction == "Bearish"
    assert residual.top == 108.0  # Unchanged
    assert residual.bottom == 106.0  # Risen to deepest wick
    # Structural SL invariant: max(c1.h, c2.h, c3.h) == 111.0
    assert max(residual.c1.high, residual.c2.high, residual.c3.high) == 111.0
    assert residual.original_top == 108.0
    assert residual.original_bottom == 102.0
    assert residual.mitigation_count == 1
    assert residual.deepest_wick_penetration == 106.0
    assert residual.formed_at == orig_fvg.formed_at


def test_residual_fvg_full_mitigation_breach_returns_none():
    """When deepest wick reaches or breaches opposite boundary, residual is None."""
    c1 = mk_c(T0, 100.0, 102.0, 99.0, 101.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 101.5, 110.0, 101.0, 109.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 109.5, 112.0, 108.0, 111.0)
    
    bullish_fvg = FVG(
        direction="Bullish",
        top=108.0,
        bottom=102.0,
        c1=c1, c2=c2, c3=c3,
        formed_at=T0 + 2 * FIVE_MIN_MS,
        timeframe="5m",
    )
    
    # Wick fully touched bottom (102.0) or breached below (101.0)
    assert ResidualFVGEngine.create_residual(bullish_fvg, deepest_wick=102.0) is None
    assert ResidualFVGEngine.create_residual(bullish_fvg, deepest_wick=101.0) is None
    
    bearish_fvg = FVG(
        direction="Bearish",
        top=108.0,
        bottom=102.0,
        c1=c1, c2=c2, c3=c3,
        formed_at=T0 + 2 * FIVE_MIN_MS,
        timeframe="5m",
    )
    
    # Wick fully touched top (108.0) or breached above (109.0)
    assert ResidualFVGEngine.create_residual(bearish_fvg, deepest_wick=108.0) is None
    assert ResidualFVGEngine.create_residual(bearish_fvg, deepest_wick=109.0) is None


def test_residual_fvg_min_gap_pct_filter():
    """When residual width as a percentage of midpoint is below min_gap_pct, return None."""
    c1 = mk_c(T0, 100.0, 100.0, 99.0, 100.0)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.0, 110.0, 100.0, 110.0)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 110.0, 112.0, 101.0, 111.0)
    
    # Bullish FVG: [bottom=100.0, top=101.0] -> 1.0 width on 100.5 midpoint ~ 0.995%
    bullish_fvg = FVG(
        direction="Bullish",
        top=101.0,
        bottom=100.0,
        c1=c1, c2=c2, c3=c3,
        formed_at=T0 + 2 * FIVE_MIN_MS,
        timeframe="5m",
    )
    
    # Deepest wick = 100.02 -> residual width = 0.02 on ~100.01 -> ~0.02%
    assert ResidualFVGEngine.create_residual(bullish_fvg, deepest_wick=100.02, min_gap_pct=0.05) is None
    assert ResidualFVGEngine.create_residual(bullish_fvg, deepest_wick=100.02, min_gap_pct=0.01) is not None


# ==============================================================================
# 2. TradeLifecycleEvaluator: Active to Close Adverse Wick Tracking & Modularity
# ==============================================================================

def test_lifecycle_evaluator_tracks_adverse_wick_from_active_to_close():
    """
    Evaluator must accurately track adverse wick across all candles from trade fill to TP exit.
    """
    c1 = mk_c(T0, 100.0, 102.0, 99.0, 101.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 101.5, 110.0, 101.0, 109.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 109.5, 112.0, 108.0, 111.0)
    
    # Bullish FVG: [102, 108], SL=99, Risk=9, 2R TP=126
    fvg = FVG(direction="Bullish", top=108.0, bottom=102.0, c1=c1, c2=c2, c3=c3, formed_at=T0 + 2 * FIVE_MIN_MS, timeframe="5m")
    
    # Candle 4: Entry filled at 108, low dips to 106
    c4 = mk_c(T0 + 3 * FIVE_MIN_MS, 110.0, 115.0, 106.0, 112.0)
    # Candle 5: While active, dips deeper to 104.5
    c5 = mk_c(T0 + 4 * FIVE_MIN_MS, 112.0, 118.0, 104.5, 117.0)
    # Candle 6: Closes trade at TP (high 128 >= 126), with low at 105.0
    c6 = mk_c(T0 + 5 * FIVE_MIN_MS, 117.0, 128.0, 105.0, 126.0)
    
    evaluator = TradeLifecycleEvaluator(fvg=fvg, completion_target="2R", min_gap_pct=0.01, partial_mitigation=True)
    state, entry_ts, floating_r, active_fvg = evaluator.evaluate([c4, c5, c6], current_price=126.0)
    
    assert state == "PENDING_RETRACE"
    assert active_fvg is not None
    # Shrunk top must be the lowest adverse wick across c4, c5, c6 (which was 104.5 on c5)
    assert active_fvg.top == 104.5
    assert active_fvg.bottom == 102.0
    assert active_fvg.mitigation_count == 1


def test_lifecycle_evaluation_partial_mitigation_enabled_vs_disabled():
    """
    When partial_mitigation=True, hitting TP produces a residual FVG that remains active (PENDING_RETRACE).
    When partial_mitigation=False, hitting TP marks the trade COMPLETED and active_fvg is None.
    """
    c1 = mk_c(T0, 100.0, 102.0, 99.0, 101.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 101.5, 110.0, 101.0, 109.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 109.5, 112.0, 108.0, 111.0)
    
    # Candle 4: Touches entry at 108 (low=105), goes up
    c4 = mk_c(T0 + 3 * FIVE_MIN_MS, 111.0, 120.0, 105.0, 118.0)
    # Candle 5: Hits TP at 126 (high=128, low=117)
    c5 = mk_c(T0 + 4 * FIVE_MIN_MS, 118.0, 128.0, 117.0, 125.0)
    # Candle 6: Price floating above residual (115-120)
    c6 = mk_c(T0 + 5 * FIVE_MIN_MS, 125.0, 126.0, 115.0, 120.0)
    
    fvg = FVG(direction="Bullish", top=108.0, bottom=102.0, c1=c1, c2=c2, c3=c3, formed_at=T0 + 2 * FIVE_MIN_MS, timeframe="5m")
    subsequent_candles = [c4, c5, c6]
    
    # Case A: partial_mitigation=False
    state_no_part, entry_ts_no_part, floating_r_no_part, active_fvg_no_part = evaluate_ltf_setup_lifecycle(
        ltf_fvg=fvg, subsequent_candles=subsequent_candles, current_price=120.0, partial_mitigation=False
    )
    assert state_no_part == "COMPLETED"
    assert active_fvg_no_part is None
    
    # Case B: partial_mitigation=True
    state_part, entry_ts_part, floating_r_part, active_fvg_part = evaluate_ltf_setup_lifecycle(
        ltf_fvg=fvg, subsequent_candles=subsequent_candles, current_price=120.0, partial_mitigation=True
    )
    assert state_part == "PENDING_RETRACE"
    assert active_fvg_part is not None
    assert active_fvg_part.top == 105.0
    assert active_fvg_part.bottom == 102.0
    assert min(active_fvg_part.c1.low, active_fvg_part.c2.low, active_fvg_part.c3.low) == 99.0
    assert active_fvg_part.mitigation_count == 1


# ==============================================================================
# 3. Live Trade Tracker Deduplication & In-Place Refresh
# ==============================================================================

def test_tracker_boundary_shrinkage_refreshes_pending_in_place(tmp_path):
    """
    When extreme trade tracker processes an active residual FVG that has shrunk,
    it should update the existing pending record in-place rather than creating duplicates.
    """
    storage_file = tmp_path / "test_trades_mitigation.json"
    tracker = ExtremeTradeTracker(
        storage_path=str(storage_file),
        session_filter=False,
        weekday_filter=False,
        entry_session_filter=False,
        entry_weekday_filter=False,
    )
    formed_ts = T0 + 2 * FIVE_MIN_MS
    
    initial_setup = {
        "symbol": "BTC",
        "direction": "Bullish",
        "state": "PENDING_RETRACE",
        "entry_price": 108.0,
        "stop_loss": 99.0,
        "risk_r": 9.0,
        "risk_pct": 8.33,
        "tp_1r": 117.0,
        "tp_2r": 126.0,
        "tp_3r": 135.0,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "5m",
        "anchor": {"bottom": 95.0, "top": 110.0, "formed_time_ist": "01-Jan 12:00 AM IST"},
        "target_fvg": {
            "bottom": 102.0,
            "top": 108.0,
            "formed_at": formed_ts,
            "gap_pct": 5.71,
            "mitigation_count": 0,
            "deepest_wick_penetration": None,
        },
    }
    
    # Step 1: Track initial pending FVG
    tracker.process_live_setups([initial_setup], {"BTC": 112.0}, session_filter=False, weekday_filter=False)
    assert len(tracker.active_trades) == 1
    trade_key = list(tracker.active_trades.keys())[0]
    trade = tracker.active_trades[trade_key]
    assert trade.entry_price == 108.0
    assert trade.state == "PENDING_RETRACE"
    assert trade.mitigation_count == 0
    
    # Step 2: Shrunk residual FVG with identical formed_at
    shrunk_setup = dict(initial_setup)
    shrunk_setup["entry_price"] = 105.0
    shrunk_setup["risk_r"] = 6.0
    shrunk_setup["tp_2r"] = 117.0
    shrunk_setup["target_fvg"] = {
        "bottom": 102.0,
        "top": 105.0,
        "formed_at": formed_ts,
        "gap_pct": 2.89,
        "mitigation_count": 1,
        "deepest_wick_penetration": 105.0,
    }
    
    tracker.process_live_setups([shrunk_setup], {"BTC": 112.0}, session_filter=False, weekday_filter=False)
    assert len(tracker.active_trades) == 1  # No duplicate rows created!
    updated_trade = tracker.active_trades[list(tracker.active_trades.keys())[0]]
    assert updated_trade.entry_price == 105.0  # Refreshed entry price
    assert updated_trade.fvg_top == 105.0
    assert updated_trade.mitigation_count == 1


# ==============================================================================
# 4. Backtester Sequential Residual Re-Entry Execution
# ==============================================================================

@pytest.mark.asyncio
async def test_backtest_sequential_residual_reentry():
    """
    Construct synthetic candles where:
    1. 4H Bullish anchor exists (c1: [85, 92], c2: [88, 105], c3: [104, 110, low=95] -> [92, 95] FVG).
    2. LTF Bullish FVG [93.0, 94.0] forms post-touch.
    3. Trade 1 triggers at 94.0 (low=93.5, deepest adverse wick=93.5) and hits TP at 99.0 on Candle 100.
    4. Residual FVG [93.0, 93.5] triggers Trade 2 at 93.5 on Candle 102 (hits TP on Candle 103).
    """
    DUR = 15 * 60 * 1000  # 15m candle duration
    H = 16 * DUR          # 4h candle duration

    def bar(ts, o, h, l, cl):
        return {"t": ts, "o": o, "h": h, "l": l, "c": cl, "v": 100.0}

    # 4H: Bullish FVG [bottom=90, top=95]
    raw_4h = [
        bar(0, 85, 92, 80, 88),
        bar(H, 88, 105, 87, 104),
        bar(2 * H, 104, 110, 95, 108),
        bar(3 * H, 108, 118, 106, 115),
        bar(4 * H, 115, 125, 112, 122),
    ]

    # LTF:
    # Candles 0..95: flat chop above 4H zone (low 96 > 95)
    raw_ltf = [bar(n * DUR, 96, 96.5, 95.5, 96) for n in range(96)]
    
    # Candle 96: Touches 4H zone (low 92 inside [90, 95])
    raw_ltf.append(bar(96 * DUR, 96, 97, 92, 95))
    
    # Candles 97..99: Form Bullish LTF FVG [bottom=93.0, top=94.0], SL=92.5
    raw_ltf.append(bar(97 * DUR, 93.0, 93.0, 92.5, 92.8))  # c1: high=93.0, low=92.5
    raw_ltf.append(bar(98 * DUR, 92.8, 98.0, 92.6, 97.5))  # c2: impulse
    raw_ltf.append(bar(99 * DUR, 97.5, 98.5, 94.0, 98.0))  # c3: low=94.0 > c1.high 93.0
    
    # Candle 100: Trade 1 entry at 94.0, lowest adverse wick = 93.5, high reaches 3R TP (94.0 + 3*1.5 = 98.5, high=99.0)
    raw_ltf.append(bar(100 * DUR, 95.0, 99.0, 93.5, 98.0))
    
    # Candle 101: Price hovers (low=96)
    raw_ltf.append(bar(101 * DUR, 97.0, 97.5, 96.0, 96.5))
    
    # Candle 102: Trade 2 entry touches residual [93.0, 93.5] at 93.5 (low=93.2)
    raw_ltf.append(bar(102 * DUR, 96.5, 96.5, 93.2, 95.0))
    # Candle 103: Trade 2 reaches 3R TP (93.5 + 3*1.0 = 96.5)
    raw_ltf.append(bar(103 * DUR, 95.0, 97.0, 94.5, 96.5))
    
    # Pad out remaining candles
    raw_ltf.extend(bar(n * DUR, 95.5, 96.0, 95.0, 95.5) for n in range(104, 110))

    class FakeClient:
        async def get_candle_snapshot(self, symbol, timeframe, start_ms, end_ms):
            return raw_4h if timeframe == "4h" else raw_ltf

    # Run with partial_mitigation=True
    report_enabled = await run_extreme_backtest(
        symbol="BTC",
        days=1,
        ltf_timeframe="15m",
        min_gap_pct=0.01,
        partial_mitigation=True,
        client=FakeClient(),
    )

    assert report_enabled.total_trades == 2, f"Expected 2 trades, got {report_enabled.total_trades}"
    assert report_enabled.trades[0].entry_price == 94.0
    assert report_enabled.trades[0].mitigation_count == 0
    assert report_enabled.trades[1].entry_price == 93.5
    assert report_enabled.trades[1].mitigation_count == 1

    # Run with partial_mitigation=False
    report_disabled = await run_extreme_backtest(
        symbol="BTC",
        days=1,
        ltf_timeframe="15m",
        min_gap_pct=0.01,
        partial_mitigation=False,
        client=FakeClient(),
    )

    assert report_disabled.total_trades == 1, f"Expected 1 trade, got {report_disabled.total_trades}"
    assert report_disabled.trades[0].entry_price == 94.0
    assert report_disabled.trades[0].mitigation_count == 0
