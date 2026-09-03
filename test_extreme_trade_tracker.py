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
