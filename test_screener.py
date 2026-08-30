"""
Comprehensive test suite for Crypto 2-Stage FVG Screener & Backtester.
Validates core mechanics, multi-timeframe rules, and critical edge cases:
1. 4H Active FVG Cache, Retracement Requirement & Invalidation Rule.
2. LTF Post-Formation Strict Retracement Timing Separation (Formation != Entry).
3. Active Open Setup Backwards Search (Retrieving unclosed open trades when newer ones fail).
4. Full Invalidation & Stopped-Out Filtering (Excluding dead/closed setups from active screener).
5. Binary Trade Lifecycle & Fixed SL (No Breakeven, strict Target TP & SL exits, zero post-win SL alerts).
6. Single Active Position vs Concurrent Multi-Position Modes (Config-driven).
7. Take Profit & Stop Loss Points Math across Bullish and Bearish setups.
8. Dual Telegram Alert Formatting with points, IST timestamps, and stages.
9. TradingView-Style Candlestick & Projection Chart Rendering with dynamic titles & zero-overlap pending box.
10. Backtest Single vs Concurrent Simulation & Historical Metric Aggregation.
11. Commodity & Crypto Symbol Mapping (PAXG, GOLD, SILVER, WTIOIL, BTC, ETH).
12. FastAPI Dynamic Chart & Backtest Endpoints.
"""

import io
import time
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from backtest import _simulate_trade_forward, BacktestSummary, HistoricalTrade, run_historical_backtest
from chart_generator import generate_setup_chart
from hyperliquid_client import HyperliquidClient, hyperliquid_client
from strategy import (
    Candle,
    FVG,
    TPLevels,
    calculate_tp_levels,
    compute_all_active_4h_fvgs,
    compute_fvg,
    price_in_fvg,
    score_coin,
    is_4h_fvg_retraced_after_creation,
    phase2_check,
    Phase1Result,
    SetupResult,
)
from telegram_client import format_single_setup, format_alert_message, _format_price
from trade_tracker import TradeTracker, TrackedTrade

IST = timezone(timedelta(hours=5, minutes=30))


# ==============================================================================
# 1. 4H ACTIVE FVG CACHE, RETRACE REQUIREMENT & INVALIDATION TESTS
# ==============================================================================
def test_4h_fvg_active_cache_invalidation():
    """Validates that a 4H FVG stays active until a subsequent candle breaches its boundary."""
    c1 = Candle(timestamp=1000, open=90, high=100, low=85, close=98, volume=10)
    c2 = Candle(timestamp=2000, open=98, high=120, low=97, close=118, volume=50)
    c3 = Candle(timestamp=3000, open=118, high=125, low=110, close=122, volume=30)

    # Candle 4 stays above bottom=100 -> FVG remains active
    c4 = Candle(timestamp=4000, open=122, high=124, low=105, close=115, volume=20)
    active_fvgs = compute_all_active_4h_fvgs([c1, c2, c3, c4])
    assert len(active_fvgs) == 1
    assert active_fvgs[0].direction == "Bullish"
    assert active_fvgs[0].bottom == 100
    assert active_fvgs[0].top == 110

    # Candle 5 shoots past bottom (low 95 < 100) -> Invalidation occurs!
    c5 = Candle(timestamp=5000, open=115, high=116, low=95, close=96, volume=40)
    invalidated_fvgs = compute_all_active_4h_fvgs([c1, c2, c3, c4, c5])
    assert len(invalidated_fvgs) == 0


def test_4h_fvg_must_retrace_after_creation():
    """Validates that a 4H FVG remains untested until price pulls back strictly after formation."""
    c1 = Candle(timestamp=1000, open=90, high=100, low=85, close=98, volume=10)
    c2 = Candle(timestamp=2000, open=98, high=120, low=97, close=118, volume=50)
    c3 = Candle(timestamp=3000, open=118, high=125, low=110, close=122, volume=30)
    fvg = FVG(direction="Bullish", top=110, bottom=100, c1=c1, c2=c2, c3=c3, formed_at=3000)

    # Case A: Subsequent candles stay high above (115 to 125) without touching [100, 110]
    c4_high = Candle(timestamp=4000, open=122, high=126, low=115, close=124, volume=20)
    c5_high = Candle(timestamp=5000, open=124, high=128, low=118, close=125, volume=20)
    candles_untested = [c1, c2, c3, c4_high, c5_high]
    assert is_4h_fvg_retraced_after_creation(candles_untested, fvg, current_price=125.0) is False

    # Case B: Subsequent candle 6 dips down to 105 (inside [100, 110]) -> Retrace confirmed!
    c6_retrace = Candle(timestamp=6000, open=125, high=126, low=105, close=112, volume=30)
    candles_tested = [c1, c2, c3, c4_high, c5_high, c6_retrace]
    assert is_4h_fvg_retraced_after_creation(candles_tested, fvg, current_price=112.0) is True


def test_4h_bearish_fvg_invalidation():
    """Validates bearish 4H FVG formation and invalidation on upper breach."""
    c1 = Candle(timestamp=1000, open=95, high=98, low=90, close=92, volume=10)
    c2 = Candle(timestamp=2000, open=92, high=92, low=75, close=78, volume=50)
    c3 = Candle(timestamp=3000, open=78, high=80, low=70, close=75, volume=30)

    active = compute_all_active_4h_fvgs([c1, c2, c3])
    assert len(active) == 1
    assert active[0].direction == "Bearish"
    assert active[0].bottom == 80
    assert active[0].top == 90

    # Candle 4 shoots past top (high 95 > 90) -> Invalidation
    c4 = Candle(timestamp=4000, open=75, high=95, low=74, close=92, volume=20)
    invalidated = compute_all_active_4h_fvgs([c1, c2, c3, c4])
    assert len(invalidated) == 0


# ==============================================================================
# 2. TAKE PROFIT (1.0R - 3.0R) & STOP LOSS POINTS MATH
# ==============================================================================
def test_tp_levels_bullish_and_points():
    tp = calculate_tp_levels(direction="Bullish", entry_price=100.0, sl_price=90.0)
    assert tp.risk_points == 10.0
    assert tp.sl_points == 10.0
    assert tp.risk_pct == 10.0
    assert tp.r1 == 110.0
    assert tp.r1_points == 10.0
    assert tp.r1_5 == 115.0
    assert tp.r1_5_points == 15.0
    assert tp.r2 == 120.0
    assert tp.r2_points == 20.0
    assert tp.r3 == 130.0
    assert tp.r3_points == 30.0


def test_tp_levels_bearish_and_points():
    tp = calculate_tp_levels(direction="Bearish", entry_price=100.0, sl_price=110.0)
    assert tp.risk_points == 10.0
    assert tp.sl_points == 10.0
    assert tp.risk_pct == 10.0
    assert tp.r1 == 90.0
    assert tp.r1_points == 10.0
    assert tp.r1_5 == 85.0
    assert tp.r1_5_points == 15.0
    assert tp.r2 == 80.0
    assert tp.r2_points == 20.0
    assert tp.r3 == 70.0
    assert tp.r3_points == 30.0


# ==============================================================================
# 3. LTF POST-FORMATION RETRACE TIMING & CANDIDATE SCAN TESTS
# ==============================================================================
@pytest.mark.asyncio
async def test_ltf_strict_retrace_timing_separation():
    """Validates that LTF FVG formation timestamp is strictly earlier than Entry timestamp."""
    base_ts = 1725000000000  # realistic ms timestamp
    c1 = Candle(base_ts, 100, 105, 95, 104, 10)
    c2 = Candle(base_ts + 300000, 104, 120, 102, 118, 50)
    c3 = Candle(base_ts + 600000, 118, 125, 110, 122, 30)  # FVG Formed at +600s: top=110, bottom=105
    c4_fly = Candle(base_ts + 900000, 122, 128, 115, 126, 20)  # No retrace (+900s)
    c5_retrace = Candle(base_ts + 1200000, 126, 127, 108, 112, 25)  # Retraces into [105, 110] at +1200s
    c6_live = Candle(base_ts + 1500000, 112, 114, 110, 112, 10)  # Currently open unfinished candle

    candles = [c1, c2, c3, c4_fly, c5_retrace, c6_live]
    htf = FVG("Bullish", 130, 100, c1, c2, c3, base_ts + 600000)
    p1 = Phase1Result("BTC", "Bullish", htf, 112.0, [htf])

    class DummyClient:
        async def get_last_n_candles(self, *args, **kwargs):
            return [{"t": c.timestamp, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume} for c in candles]

    setup = await phase2_check(p1, client=DummyClient(), ltf_timeframe="5m")
    assert setup is not None
    assert setup.stage == "ACTIVATED"
    assert setup.fvg_formation_time_ist != setup.entry_time_ist
    assert setup.entry_price == 110.0


@pytest.mark.asyncio
async def test_active_open_setup_backwards_search():
    """
    Validates that when the latest FVG got stopped out, phase2_check searches
    backwards and retrieves earlier valid open setups (e.g. 10:30 AM Gold trade).
    """
    # 1. Earlier Valid FVG formed at 1000-3000 (SL = 95, Entry = 105, TP2 = 125)
    c1 = Candle(1000, 95, 100, 95, 99, 10)
    c2 = Candle(2000, 99, 115, 98, 112, 50)
    c3 = Candle(3000, 112, 120, 105, 118, 30)  # FVG 1: [100, 105], SL=95
    c4 = Candle(4000, 118, 119, 104, 108, 20)  # Retraced at 4000 -> Active open!

    # 2. Later FVG formed at 5000-7000 (SL = 110)
    c5 = Candle(5000, 108, 112, 106, 110, 20)
    c6 = Candle(6000, 110, 115, 109, 114, 50)  # High stays at 115 (< TP2 125)
    c7 = Candle(7000, 114, 122, 112, 120, 30)  # FVG 2: [112, 120], SL=109

    # 3. Crash candle at 8000 drops to 102 (Breaches FVG 2 SL=109, but DOES NOT breach FVG 1 SL=95!)
    c8_crash = Candle(8000, 120, 121, 102, 104, 80)

    candles = [c1, c2, c3, c4, c5, c6, c7, c8_crash]
    htf = FVG("Bullish", 140, 90, c1, c2, c3, 3000)
    p1 = Phase1Result("PAXG", "Bullish", htf, 104.0, [htf])

    class DummyClient:
        async def get_last_n_candles(self, *args, **kwargs):
            return [{"t": c.timestamp, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume} for c in candles]

    setup = await phase2_check(p1, client=DummyClient(), ltf_timeframe="5m")
    assert setup is not None
    assert setup.stage == "ACTIVATED"
    # Should skip the dead FVG 2 (SL 109) and retrieve the active open FVG 1 (SL 95)
    assert setup.sl_ref == 95.0
    assert setup.entry_price == 105.0


@pytest.mark.asyncio
async def test_all_candidate_fvgs_stopped_out_returns_none():
    """Validates that if all candidate FVGs are stopped out, phase2_check returns None."""
    c1 = Candle(1000, 100, 105, 95, 104, 10)
    c2 = Candle(2000, 104, 120, 102, 118, 50)
    c3 = Candle(3000, 118, 125, 110, 122, 30)  # SL = 95
    c4 = Candle(4000, 122, 124, 108, 115, 20)  # Retrace
    c5_sl_hit = Candle(5000, 115, 116, 90, 92, 60)  # Drops to 90 (< SL 95)

    candles = [c1, c2, c3, c4, c5_sl_hit]
    htf = FVG("Bullish", 130, 90, c1, c2, c3, 3000)
    p1 = Phase1Result("BTC", "Bullish", htf, 92.0, [htf])

    class DummyClient:
        async def get_last_n_candles(self, *args, **kwargs):
            return [{"t": c.timestamp, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume} for c in candles]

    setup = await phase2_check(p1, client=DummyClient(), ltf_timeframe="5m")
    assert setup is None


# ==============================================================================
# 4. TRADE TRACKER: LIFECYCLE, NO BREAKEVEN, & TARGET TP CLOSURE
# ==============================================================================
def test_trade_tracker_alert_deduplication():
    """Validates that repeat scans of the same setup do not spam duplicate alerts."""
    tracker = TradeTracker(single_active_position=True)
    c1 = Candle(1000, 100, 105, 95, 104, 10)
    c2 = Candle(2000, 105, 120, 104, 118, 50)
    c3 = Candle(3000, 118, 125, 110, 122, 30)
    candles = [c1, c2, c3]
    htf_fvg = FVG("Bullish", top=125, bottom=110, c1=c1, c2=c2, c3=c3, formed_at=3000)
    ltf_fvg = FVG("Bullish", top=120, bottom=115, c1=c1, c2=c2, c3=c3, formed_at=3000)
    tp = calculate_tp_levels("Bullish", entry_price=118.0, sl_price=105.0)

    # 1. PENDING_RETRACE -> Alert 1 dispatched
    setup1 = SetupResult("BTC", "Bullish", "PENDING_RETRACE", htf_fvg, ltf_fvg, 122.0, 118.0, 105.0, tp, 0.9, "5m")
    should_alert1, text1, _ = tracker.register_or_update_setup(setup1, candles)
    assert should_alert1 is True
    assert "Setup Formed" in text1

    # 2. Duplicate PENDING_RETRACE -> Suppressed
    should_alert2, text2, _ = tracker.register_or_update_setup(setup1, candles)
    assert should_alert2 is False
    assert text2 is None

    # 3. Transition to ACTIVATED -> Alert 2 dispatched
    setup_act = SetupResult("BTC", "Bullish", "ACTIVATED", htf_fvg, ltf_fvg, 118.0, 118.0, 105.0, tp, 0.9, "5m")
    should_alert3, text3, _ = tracker.register_or_update_setup(setup_act, candles)
    assert should_alert3 is True
    assert "TRADE ACTIVATED" in text3

    # 4. Duplicate ACTIVATED -> Suppressed
    should_alert4, text4, _ = tracker.register_or_update_setup(setup_act, candles)
    assert should_alert4 is False


def test_trade_tracker_tp_sl_updates():
    """
    Validates binary exits:
    - 1.0R / 1.5R are progress milestones (SL is NOT moved to breakeven).
    - 2.0R Target TP closes the trade as WIN (CLOSED_TP).
    - Subsequent price crash below initial SL never fires a Stop Loss alert.
    """
    tracker = TradeTracker(single_active_position=True)
    c1 = Candle(1000, 100, 105, 95, 104, 10)
    c2 = Candle(2000, 105, 120, 104, 118, 50)
    c3 = Candle(3000, 118, 125, 110, 122, 30)
    htf_fvg = FVG("Bullish", top=125, bottom=110, c1=c1, c2=c2, c3=c3, formed_at=3000)
    ltf_fvg = FVG("Bullish", top=120, bottom=115, c1=c1, c2=c2, c3=c3, formed_at=3000)
    tp = calculate_tp_levels("Bullish", entry_price=100.0, sl_price=90.0)  # TP1=110, TP2=120, TP3=130

    # Register Activated Bullish trade
    setup = SetupResult("BTC", "Bullish", "ACTIVATED", htf_fvg, ltf_fvg, 100.0, 100.0, 90.0, tp, 0.9, "5m")
    tracker.register_or_update_setup(setup, [c1, c2, c3])

    # Price moves to 112 -> Hits 1.0R TP Milestone (Trade remains open, SL remains 90.0)
    updates1 = tracker.check_open_trades({"BTC": 112.0})
    assert len(updates1) == 1
    assert "1.0R TAKE PROFIT REACHED" in updates1[0]
    assert tracker.trades[tracker.get_setup_id(setup)].sl_price == 90.0

    # Price moves to 116 -> Hits 1.5R TP Milestone
    updates1_5 = tracker.check_open_trades({"BTC": 116.0})
    assert len(updates1_5) == 1
    assert "1.5R TAKE PROFIT REACHED" in updates1_5[0]

    # Price moves to 122 -> Hits 2.0R Target TP (Closes trade as WIN!)
    updates2 = tracker.check_open_trades({"BTC": 122.0})
    assert len(updates2) == 1
    assert "2.0R TARGET TAKE PROFIT HIT" in updates2[0]
    assert tracker.trades[tracker.get_setup_id(setup)].stage == "CLOSED_TP"

    # Price later drops down to 88 (below initial SL) -> Must NOT emit Stop Loss alert because trade already won 2R TP!
    updates_late_drop = tracker.check_open_trades({"BTC": 88.0})
    assert len(updates_late_drop) == 0, "Closed winning trade must never trigger Stop Loss!"


def test_trade_tracker_single_position_suppression():
    """Validates that single_active_position=True suppresses new overlapping trades for the same asset."""
    tracker = TradeTracker(single_active_position=True)
    c1 = Candle(1000, 100, 105, 95, 104, 10)
    c2 = Candle(2000, 105, 120, 104, 118, 50)
    c3 = Candle(3000, 118, 125, 110, 122, 30)
    htf = FVG("Bullish", top=125, bottom=110, c1=c1, c2=c2, c3=c3, formed_at=3000)
    ltf1 = FVG("Bullish", top=120, bottom=115, c1=c1, c2=c2, c3=c3, formed_at=3000)
    tp1 = calculate_tp_levels("Bullish", 100.0, 90.0)

    # Trade 1 Active
    setup1 = SetupResult("PAXG", "Bullish", "ACTIVATED", htf, ltf1, 100.0, 100.0, 90.0, tp1, 0.9, "5m")
    should_alert1, _, _ = tracker.register_or_update_setup(setup1, [c1, c2, c3])
    assert should_alert1 is True

    # New overlapping setup formed at 4000 while Trade 1 is still active
    c4 = Candle(4000, 122, 126, 118, 124, 20)
    ltf2 = FVG("Bullish", top=124, bottom=120, c1=c2, c2=c3, c3=c4, formed_at=4000)
    tp2 = calculate_tp_levels("Bullish", 122.0, 118.0)
    setup2 = SetupResult("PAXG", "Bullish", "ACTIVATED", htf, ltf2, 122.0, 122.0, 118.0, tp2, 0.9, "5m")

    # In Single Position mode, setup2 MUST be suppressed
    should_alert2, _, _ = tracker.register_or_update_setup(setup2, [c1, c2, c3, c4])
    assert should_alert2 is False


# ==============================================================================
# 5. TELEGRAM ALERT FORMATTING TESTS
# ==============================================================================
def test_telegram_dual_alert_formatting_with_points():
    c1 = Candle(1000, 96000, 96200, 95800, 96100, 10)
    c2 = Candle(2000, 96100, 97500, 96000, 97200, 50)
    c3 = Candle(3000, 97200, 97800, 96800, 97500, 30)
    htf_fvg = FVG("Bullish", top=96800, bottom=96200, c1=c1, c2=c2, c3=c3, formed_at=3000)
    ltf_fvg = FVG("Bullish", top=96550, bottom=96350, c1=c1, c2=c2, c3=c3, formed_at=3000)
    tp = calculate_tp_levels("Bullish", entry_price=96400, sl_price=95800)

    # Alert 1: Pending Retrace
    setup_pending = SetupResult(
        symbol="BTC",
        direction="Bullish",
        stage="PENDING_RETRACE",
        htf_fvg=htf_fvg,
        ltf_fvg=ltf_fvg,
        current_price=96600.0,
        entry_price=96450.0,
        sl_ref=95800.0,
        tp_levels=tp,
        score=0.85,
        ltf_timeframe="5m",
    )
    alert_1 = format_single_setup(setup_pending)
    assert "🟡 <b>BTC-PERP — Bullish Setup Formed</b>" in alert_1
    assert "Waiting for price to retrace" in alert_1
    assert "Risk: 600.00 pts" in alert_1

    # Alert 2: Activated Trade
    setup_activated = SetupResult(
        symbol="BTC",
        direction="Bullish",
        stage="ACTIVATED",
        htf_fvg=htf_fvg,
        ltf_fvg=ltf_fvg,
        current_price=96400.0,
        entry_price=96400.0,
        sl_ref=95800.0,
        tp_levels=tp,
        score=0.88,
        ltf_timeframe="5m",
    )
    alert_2 = format_single_setup(setup_activated)
    assert "TRADE ACTIVATED!" in alert_2
    assert "Risk: 600.00 pts" in alert_2
    assert "🎯 <b>1.0R:</b> $97,000.00 (+600.00 pts)" in alert_2
    assert "🎯 <b>2.0R:</b> $97,600.00 (+1,200.00 pts)" in alert_2


# ==============================================================================
# 6. TRADINGVIEW-STYLE CHART IMAGE GENERATION TESTS
# ==============================================================================
def test_chart_generator_rendering_pending_vs_active():
    """Validates chart rendering for pending (no candle overlap) and active setups."""
    c1 = Candle(1000, 100, 105, 95, 104, 10)
    c2 = Candle(2000, 104, 120, 102, 118, 50)
    c3 = Candle(3000, 118, 125, 110, 122, 30)
    candles = [c1, c2, c3]
    htf_fvg = FVG("Bullish", top=125, bottom=110, c1=c1, c2=c2, c3=c3, formed_at=3000)
    ltf_fvg = FVG("Bullish", top=120, bottom=115, c1=c1, c2=c2, c3=c3, formed_at=3000)
    tp = calculate_tp_levels("Bullish", entry_price=118.0, sl_price=105.0)

    # 1. Pending Chart
    img_pending = generate_setup_chart(
        symbol="BTC",
        direction="Bullish",
        candles_ltf=candles,
        htf_fvg=htf_fvg,
        ltf_fvg=ltf_fvg,
        entry_price=118.0,
        sl_price=105.0,
        tp_levels=tp,
        stage="PENDING_RETRACE",
        ltf_timeframe="5m",
    )
    assert len(img_pending) > 1000
    assert img_pending.startswith(b"\x89PNG\r\n\x1a\n")

    # 2. Active Chart
    img_active = generate_setup_chart(
        symbol="BTC",
        direction="Bullish",
        candles_ltf=candles,
        htf_fvg=htf_fvg,
        ltf_fvg=ltf_fvg,
        entry_price=118.0,
        sl_price=105.0,
        tp_levels=tp,
        stage="ACTIVATED",
        ltf_timeframe="5m",
    )
    assert len(img_active) > 1000
    assert img_active.startswith(b"\x89PNG\r\n\x1a\n")


# ==============================================================================
# 7. HISTORICAL BACKTEST SIMULATION & SINGLE POSITION MODE
# ==============================================================================
def test_historical_trade_simulation_and_metrics():
    candles = [
        Candle(1000, 100, 105, 98, 104, 10),
        Candle(2000, 105, 120, 102, 118, 50),
        Candle(3000, 118, 125, 110, 122, 30),
        Candle(4000, 108, 112, 106, 108, 20),
        Candle(5000, 108, 115, 107, 114, 25),
        Candle(6000, 114, 130, 113, 128, 40),
    ]
    fvg = FVG("Bullish", top=110, bottom=105, c1=candles[0], c2=candles[1], c3=candles[2], formed_at=3000)

    trade = _simulate_trade_forward(
        entry_idx=3,
        candles_ltf=candles,
        symbol="BTC",
        direction="Bullish",
        entry_price=108.0,
        sl_price=98.0,
        target_rr=2.0,
        htf_fvg=fvg,
        ltf_fvg=fvg,
        score=0.85,
        ltf_timeframe="5m",
    )

    assert trade.outcome == "WIN"
    assert trade.sl_points == 10.0
    assert trade.tp_points == 20.0
    assert trade.tp_price == 128.0
    assert trade.r_multiple == 2.0
    assert trade.exit_candle_idx == 5


# ==============================================================================
# 8. COMMODITY & CRYPTO SYMBOL MAPPING
# ==============================================================================
def test_commodity_and_crypto_symbol_mapping():
    client = HyperliquidClient()
    # Direct commodity aliases
    assert client.get_hl_symbol("GOLD") == "PAXG"
    assert client.get_hl_symbol("SILVER") == "SILVER"
    assert client.get_hl_symbol("WTIOIL") == "WTIOIL"
    assert client.get_hl_symbol("BTC-PERP") == "BTC"
    assert client.get_hl_symbol("ETH-PERP") == "ETH"


# ==============================================================================
# 9. FASTAPI ENDPOINTS TEST
# ==============================================================================
def test_fastapi_endpoints():
    from main import app
    with TestClient(app) as client:
        # JSON Root Request
        json_res = client.get("/", headers={"Accept": "application/json"})
        assert json_res.status_code == 200
        assert json_res.json() == {"status": "screener running"}

        # HTML Web Dashboard Request
        html_res = client.get("/", headers={"Accept": "text/html"})
        assert html_res.status_code == 200
        assert "FVG SCREENER" in html_res.text
        assert "ACTIVATED TRADES" in html_res.text
        assert "PENDING RETRACE" in html_res.text
        assert "SL RISK (PTS)" in html_res.text
        assert "TP REWARD (PTS)" in html_res.text

        # Status API
        status_res = client.get("/api/status")
        assert status_res.status_code == 200
        assert "htf_mode" in status_res.json()

        # Dynamic Setup Chart API
        chart_res = client.get("/api/chart?symbol=BTC&direction=Bullish&ltf=5m&stage=ACTIVATED&entry=96000&sl=95000")
        assert chart_res.status_code == 200
        assert chart_res.headers["content-type"] == "image/png"
        assert len(chart_res.content) > 1000

        # Historical Backtest Chart with timestamp
        bt_chart_res = client.get("/api/chart?symbol=PAXG&direction=Bullish&ltf=5m&stage=ACTIVATED&entry=4460&sl=4458&timestamp=1725000000000")
        assert bt_chart_res.status_code == 200
        assert bt_chart_res.headers["content-type"] == "image/png"
