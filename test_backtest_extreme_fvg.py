"""
Unit Tests for Extreme LTF FVG Backtesting Engine.
Tests simulation logic, 1R/2R/3R multi-target resolution, SL handling, and performance metrics.
"""

import pytest
from backtest_extreme_fvg import (
    ExtremeHistoricalTrade,
    ExtremeBacktestReport,
    simulate_trade_execution,
)
from strategy_extreme_fvg import (
    Candle,
    FVG,
    TouchedAnchor,
)


def make_candle(timestamp: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
    )


def test_simulate_trade_hitting_all_tps():
    # Bullish FVG: Entry=100, SL=90, Risk=10 -> TP1=110, TP2=120, TP3=130
    c1 = make_candle(0, 85, 95, 80, 92)
    c2 = make_candle(1000, 92, 115, 91, 114)
    c3 = make_candle(2000, 114, 120, 100, 118)
    ltf_fvg = FVG("Bullish", 100, 95, c1, c2, c3, formed_at=2000, timeframe="15m")
    anchor = TouchedAnchor(ltf_fvg, first_touch_timestamp=1000, most_recent_touch_timestamp=1000)

    # Subsequent candles:
    # Bar 1: reaches 112 (hits TP1 110)
    sub1 = make_candle(3000, 100, 112, 98, 111)
    # Bar 2: reaches 122 (hits TP2 120)
    sub2 = make_candle(4000, 111, 122, 109, 121)
    # Bar 3: reaches 132 (hits TP3 130)
    sub3 = make_candle(5000, 121, 132, 120, 131)

    trade = simulate_trade_execution(
        symbol="BTC",
        direction="Bullish",
        entry_price=100.0,
        stop_loss=90.0,
        entry_timestamp=3000,
        subsequent_candles=[sub1, sub2, sub3],
        anchor=anchor,
        ltf_fvg=ltf_fvg,
    )

    assert trade.hit_1r is True
    assert trade.hit_2r is True
    assert trade.hit_3r is True
    assert trade.realized_r_1r == 1.0
    assert trade.realized_r_2r == 2.0
    assert trade.realized_r_3r == 3.0
    assert trade.exit_reason == "TP_3R"
    assert trade.mfe_r == (132.0 - 100.0) / 10.0  # +3.2R


def test_simulate_trade_hitting_1r_then_stopped_out():
    # Bullish FVG: Entry=100, SL=90, Risk=10 -> TP1=110, TP2=120
    c1 = make_candle(0, 85, 95, 80, 92)
    c2 = make_candle(1000, 92, 115, 91, 114)
    c3 = make_candle(2000, 114, 120, 100, 118)
    ltf_fvg = FVG("Bullish", 100, 95, c1, c2, c3, formed_at=2000, timeframe="15m")
    anchor = TouchedAnchor(ltf_fvg, first_touch_timestamp=1000, most_recent_touch_timestamp=1000)

    # Bar 1 reaches 112 (hits 1R 110)
    sub1 = make_candle(3000, 100, 112, 98, 109)
    # Bar 2 dumps to 88 (hits SL 90 before reaching 2R 120)
    sub2 = make_candle(4000, 109, 110, 88, 89)

    trade = simulate_trade_execution(
        symbol="ETH",
        direction="Bullish",
        entry_price=100.0,
        stop_loss=90.0,
        entry_timestamp=3000,
        subsequent_candles=[sub1, sub2],
        anchor=anchor,
        ltf_fvg=ltf_fvg,
    )

    assert trade.hit_1r is True
    assert trade.hit_2r is False
    assert trade.hit_3r is False
    assert trade.realized_r_1r == 1.0   # Won if target was 1R
    assert trade.realized_r_2r == -1.0  # Lost if target was 2R
    assert trade.realized_r_3r == -1.0  # Lost if target was 3R
    assert trade.exit_reason == "STOPPED_OUT"


def test_simulate_trade_hitting_sl_directly_bearish():
    # Bearish FVG: Entry=100, SL=110, Risk=10 -> TP1=90, TP2=80, TP3=70
    c1 = make_candle(0, 115, 120, 105, 108)
    c2 = make_candle(1000, 108, 109, 85, 87)
    c3 = make_candle(2000, 87, 95, 84, 86)
    ltf_fvg = FVG("Bearish", 105, 100, c1, c2, c3, formed_at=2000, timeframe="15m")
    anchor = TouchedAnchor(ltf_fvg, first_touch_timestamp=1000, most_recent_touch_timestamp=1000)

    # Bar 1 rallies straight to 112 (hits SL 110 directly)
    sub1 = make_candle(3000, 100, 112, 99, 111)

    trade = simulate_trade_execution(
        symbol="SOL",
        direction="Bearish",
        entry_price=100.0,
        stop_loss=110.0,
        entry_timestamp=3000,
        subsequent_candles=[sub1],
        anchor=anchor,
        ltf_fvg=ltf_fvg,
    )

    assert trade.hit_1r is False
    assert trade.hit_2r is False
    assert trade.hit_3r is False
    assert trade.realized_r_1r == -1.0
    assert trade.realized_r_2r == -1.0
    assert trade.realized_r_3r == -1.0
    assert trade.exit_reason == "STOPPED_OUT"


def test_trade_timestamps_distinct_and_chronological():
    # Verify that FVG Formed time < Entry time < Exit time
    c1 = make_candle(0, 85, 95, 80, 92)
    c2 = make_candle(900000, 92, 115, 91, 114)
    c3 = make_candle(1800000, 114, 120, 100, 118)  # FVG closes at 1800000 + 900000 = 2700000 ms
    ltf_fvg = FVG("Bullish", 100, 95, c1, c2, c3, formed_at=1800000, timeframe="15m")
    anchor = TouchedAnchor(ltf_fvg, first_touch_timestamp=900000, most_recent_touch_timestamp=900000)

    # Subsequent bar 1: 2700000 - 3600000 (price stays above 100)
    # Subsequent bar 2: 3600000 - 4500000 (price retraces to 98 and fills entry at 100)
    # Subsequent bar 3: 4500000 - 5400000 (price rallies to 125, hitting 2R TP)
    sub1 = make_candle(4500000, 100, 125, 99, 124)

    trade = simulate_trade_execution(
        symbol="BTC",
        direction="Bullish",
        entry_price=100.0,
        stop_loss=90.0,
        entry_timestamp=3600000,
        subsequent_candles=[sub1],
        anchor=anchor,
        ltf_fvg=ltf_fvg,
    )

    assert trade.fvg_formation_timestamp == 2700000  # C3 close timestamp (09:45 AM)
    assert trade.entry_timestamp == 3600000          # Retrace fill timestamp (10:00 AM)
    assert trade.exit_timestamp == 5400000           # Exit bar close timestamp (10:30 AM)
    assert trade.fvg_formation_timestamp < trade.entry_timestamp < trade.exit_timestamp

