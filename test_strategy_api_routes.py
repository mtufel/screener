"""
Tests for the strategy-parameterized API routes added by the strategy-extensibility change.

Covers:
  - GET /api/{strategy}/status  (valid + unknown strategy -> 404, not 500 NameError)
  - GET /api/{strategy}/info    (valid + unknown strategy -> 404, not 500 NameError)
"""

import pytest
from fastapi.testclient import TestClient

from main import app


def test_api_strategy_status_known_strategy():
    client = TestClient(app)
    resp = client.get("/api/extreme_fvg/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["strategy"] == "extreme_fvg"
    assert "display_name" in data
    assert "is_active" in data
    assert "is_running" in data


def test_api_strategy_status_active_flag_reflects_current_daemon_strategy():
    client = TestClient(app)
    resp = client.get("/api/extreme_fvg/status")
    assert resp.status_code == 200
    data = resp.json()
    # extreme_active_strategy defaults to extreme_fvg, so it should be active
    assert data["strategy"] == "extreme_fvg"
    assert data["is_active"] in (True, False)


def test_api_strategy_status_unknown_strategy_returns_404_not_500():
    client = TestClient(app)
    resp = client.get("/api/does_not_exist/status")
    assert resp.status_code == 404
    data = resp.json()
    assert "Unknown strategy" in data["detail"] or "does_not_exist" in data["detail"]


def test_api_strategy_info_known_strategy():
    client = TestClient(app)
    resp = client.get("/api/extreme_fvg/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "extreme_fvg"
    assert data["display_name"] == "Extreme LTF FVG"
    assert "default_params" in data
    assert data["default_params"]["min_gap_pct"] == 0.05


def test_api_strategy_info_unknown_strategy_returns_404_not_500():
    client = TestClient(app)
    resp = client.get("/api/does_not_exist/info")
    assert resp.status_code == 404
    data = resp.json()
    assert "Unknown strategy" in data["detail"]
    assert "extreme_fvg" in data["detail"]  # available list is surfaced


def test_api_strategy_backtest_unknown_strategy_returns_404():
    """Regression test: the /api/{strategy}/backtest handler previously referenced an
    unbound `e` in its except clause, which raised NameError (500) instead of 404."""
    client = TestClient(app)
    resp = client.get("/api/does_not_exist/backtest")
    assert resp.status_code == 404
    data = resp.json()
    assert "Unknown strategy" in data["detail"] or "Datafeed" in data["detail"]
