"""
Unit and Integration Tests for FastAPI Extreme Continuous Scanner Daemon.
"""

import pytest
from fastapi.testclient import TestClient
from main import app, state, execute_extreme_screener_cycle


def test_api_extreme_status_endpoint():
    client = TestClient(app)
    resp = client.get("/api/extreme/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "is_running" in data
    assert "interval_seconds" in data
    assert "completion_target" in data
    assert data["completion_target"] == "2R"
    assert "setups" in data


def test_api_extreme_toggle_daemon():
    client = TestClient(app)
    # Pause daemon
    resp = client.post("/api/extreme/toggle-daemon?enable=false")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_running"] is False
    assert state["extreme_is_running"] is False

    # Resume daemon
    resp = client.post("/api/extreme/toggle-daemon?enable=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_running"] is True
    assert state["extreme_is_running"] is True


def test_api_extreme_config_endpoint():
    client = TestClient(app)
    resp = client.post("/api/extreme/config?interval_seconds=45&ltf=5m&target=3R&min_gap_pct=0.10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["config"]["interval_seconds"] == 45
    assert data["config"]["ltf_timeframe"] == "5m"
    assert data["config"]["completion_target"] == "3R"
    assert data["config"]["min_gap_pct"] == 0.10
    assert state["extreme_interval_seconds"] == 45
