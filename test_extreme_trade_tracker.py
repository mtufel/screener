"""
Unit & Integration Tests for Extreme Live Trade Tracker & History Logging.
"""

import pytest
from fastapi.testclient import TestClient
from main import app
from extreme_trade_tracker import ExtremeTradeTracker, TrackedExtremeTrade


@pytest.fixture
def client():
    return TestClient(app)


def test_tracker_lifecycle_and_resolution(tmp_path):
    storage_file = tmp_path / "test_trades.json"
    tracker = ExtremeTradeTracker(storage_path=str(storage_file))

    # 1. Simulate new setup discovery
    mock_setup = {
        "symbol": "BTC",
        "direction": "Bullish",
        "state": "PENDING_RETRACE",
        "entry_price": 60000.0,
        "stop_loss": 59000.0,
        "risk_r": 1000.0,
        "risk_pct": 1.67,
        "tp_1r": 61000.0,
        "tp_2r": 62000.0,
        "tp_3r": 63000.0,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "15m",
        "anchor": {"bottom": 59500, "top": 60500, "formed_time_ist": "03-Sep 09:00 AM IST"},
        "target_fvg": {"bottom": 59800, "top": 60000, "formed_at": 1788000000, "gap_pct": 0.33},
    }

    mids = {"BTC": 60500.0}
    events = tracker.process_live_setups([mock_setup], mids)
    assert len(events) == 1
    assert events[0][0] == "NEW_SETUP"
    assert len(tracker.active_trades) == 1

    # 2. Simulate entry filled
    mock_setup["state"] = "TRADE_ACTIVE"
    mock_setup["entry_time_ist"] = "03-Sep 09:15 AM IST"
    events = tracker.process_live_setups([mock_setup], {"BTC": 60000.0})
    assert any(e[0] == "ENTRY_FILLED" for e in events)

    trade = list(tracker.active_trades.values())[0]
    assert trade.state == "TRADE_ACTIVE"
    assert trade.entry_filled_at_ist == "03-Sep 09:15 AM IST"

    # 3. Simulate price rally hitting 2R TP (62000)
    events = tracker.process_live_setups([mock_setup], {"BTC": 62100.0})
    assert any(e[0] == "TP_HIT" for e in events)
    assert len(tracker.active_trades) == 0
    assert len(tracker.history) == 1

    closed = tracker.history[0]
    assert closed.state == "COMPLETED_TP"
    assert closed.realized_r == 2.0
    assert closed.mfe_r >= 2.0

    # 4. Verify summary metrics
    summary = tracker.get_summary()
    assert summary["total_closed_trades"] == 1
    assert summary["wins"] == 1
    assert summary["win_rate_pct"] == 100.0
    assert summary["net_realized_r"] == 2.0


def test_api_live_history_endpoints(client):
    res = client.get("/api/extreme/live-history")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "summary" in data
    assert "active_trades" in data
    assert "history" in data

    res_clear = client.post("/api/extreme/clear-live-history")
    assert res_clear.status_code == 200
    assert res_clear.json()["status"] == "success"


def test_pending_setup_invalidation_when_sl_breached(tmp_path):
    storage_file = tmp_path / "test_trades_inval.json"
    tracker = ExtremeTradeTracker(storage_path=str(storage_file))

    mock_setup = {
        "symbol": "BTC",
        "direction": "Bullish",
        "state": "PENDING_RETRACE",
        "entry_price": 60000.0,
        "stop_loss": 59000.0,
        "risk_r": 1000.0,
        "risk_pct": 1.67,
        "tp_1r": 61000.0,
        "tp_2r": 62000.0,
        "tp_3r": 63000.0,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "15m",
        "anchor": {"bottom": 59000, "top": 60500, "formed_time_ist": "03-Sep 09:00 AM IST"},
        "target_fvg": {"bottom": 59800, "top": 60000, "formed_at": 1788000000, "gap_pct": 0.33},
    }

    # Setup created as PENDING_RETRACE
    events = tracker.process_live_setups([mock_setup], {"BTC": 60500.0})
    assert len(tracker.active_trades) == 1

    # Price crashes below stop loss (58500 < 59000) without filling entry
    events = tracker.process_live_setups([], {"BTC": 58500.0})
    assert any(e[0] == "SETUP_INVALIDATED" for e in events)
    assert len(tracker.active_trades) == 0
    assert len(tracker.history) == 1
    assert tracker.history[0].state == "INVALIDATED"


def test_pending_setup_fills_and_completes_same_candle(tmp_path):
    """Regression: a PENDING_RETRACE trade whose FVG fills entry and reaches the
    completion target within one closed candle must resolve to COMPLETED_TP even
    though the scanner classifies the FVG as COMPLETED and no longer emits the setup."""
    storage_file = tmp_path / "test_trades_pending_tp.json"
    tracker = ExtremeTradeTracker(storage_path=str(storage_file))

    mock_setup = {
        "symbol": "BTC",
        "direction": "Bullish",
        "state": "PENDING_RETRACE",
        "entry_price": 100.25,
        "stop_loss": 100.05,
        "risk_r": 0.20,
        "risk_pct": 0.199,
        "tp_1r": 100.45,
        "tp_2r": 100.65,
        "tp_3r": 100.85,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "15m",
        "anchor": {"bottom": 100.05, "top": 100.60, "formed_time_ist": "04-Sep 09:00 AM IST"},
        "target_fvg": {"bottom": 100.05, "top": 100.25, "formed_at": 1788000000, "gap_pct": 0.20},
    }

    # Cycle 1: setup registered as PENDING_RETRACE (price above entry, no touch yet)
    events = tracker.process_live_setups([mock_setup], {"BTC": 100.30})
    assert len(tracker.active_trades) == 1
    assert any(e[0] == "NEW_SETUP" for e in events)

    # Cycle 2: FVG classified COMPLETED by the scanner -> NO setup emitted for BTC.
    # One closed candle touched entry (low 100.20 <= 100.25) and reached 2R (high 100.80 >= 100.65).
    fill_candle = {"t": 1788001800, "o": 100.40, "h": 100.80, "l": 100.20, "c": 100.70, "v": 1.0}
    events = tracker.process_live_setups([], {"BTC": 100.70}, recent_candles_map={"BTC": [fill_candle]})

    assert any(e[0] == "TP_HIT" for e in events)
    assert len(tracker.active_trades) == 0
    assert len(tracker.history) == 1
    resolved = tracker.history[0]
    assert resolved.state == "COMPLETED_TP"
    assert resolved.realized_r == 2.0
    assert resolved.entry_filled_at_ist is not None
    assert resolved.closed_at_ist is not None


def test_symbol_alias_resolution_for_live_mids(tmp_path):
    storage_file = tmp_path / "test_trades_alias.json"
    tracker = ExtremeTradeTracker(storage_path=str(storage_file))

    mock_gold_setup = {
        "symbol": "GOLD",
        "direction": "Bullish",
        "state": "TRADE_ACTIVE",
        "entry_price": 2500.0,
        "stop_loss": 2480.0,
        "risk_r": 20.0,
        "risk_pct": 0.8,
        "tp_1r": 2520.0,
        "tp_2r": 2540.0,
        "tp_3r": 2560.0,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "15m",
        "anchor": {"bottom": 2470, "top": 2510, "formed_time_ist": "03-Sep 09:00 AM IST"},
        "target_fvg": {"bottom": 2490, "top": 2500, "formed_at": 1788000000, "gap_pct": 0.4},
        "entry_timestamp": 1788001000,
    }

    # Hyperliquid mids keyed by PAXG for GOLD
    mids = {"PAXG": 2530.0}
    events = tracker.process_live_setups([mock_gold_setup], mids)
    trade = list(tracker.active_trades.values())[0]

    # floating_r should resolve PAXG mid-price (+1.5R) instead of staying frozen at 0.00R
    assert trade.floating_r == 1.5
    assert trade.max_favorable_price == 2530.0
    assert trade.mfe_r == 1.5
