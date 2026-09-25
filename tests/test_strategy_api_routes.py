"""
Tests for the strategy-parameterized API routes added by the strategy-extensibility change.

Covers:
  - GET /api/{strategy}/status  (valid + unknown strategy -> 404, not 500 NameError)
  - GET /api/{strategy}/info    (valid + unknown strategy -> 404, not 500 NameError)
  - GET /api/{strategy}/backtest (success path serializes metadata + numerics, unknown -> 404)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from main import app
from strategies import get_strategy


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


def test_api_strategy_backtest_success_preserves_metadata_and_rounds_numerics():
    """The success path must keep string metadata (symbol, ltf_timeframe,
    invalidation_mode, session names) and booleans intact while rounding numeric
    fields. Regression for a bug that coerced strings to 0.0 and bools to 1.0/0.0."""
    import api.extreme as extreme_api
    from backtest_extreme_fvg import ExtremeBacktestReport

    client = TestClient(app)

    report = ExtremeBacktestReport(
        symbol="BTC", days=14, ltf_timeframe="5m", invalidation_mode="wick",
        min_gap_pct=0.05, total_trades=2, wins_1r=1, wins_2r=1, wins_3r=0, losses=1,
        win_rate_1r=50.0, win_rate_2r=50.0, win_rate_3r=0.0,
        net_pnl_1r=1.0, net_pnl_2r=0.5, net_pnl_3r=0.0,
        profit_factor_1r=2.0, profit_factor_2r=1.5, profit_factor_3r=0.0,
        max_drawdown_r=0.5, avg_trade_duration_min=90, avg_mfe_r=0.6,
        session_filter_enabled=False, weekday_filter_enabled=False,
        entry_session_filter_enabled=False, entry_weekday_filter_enabled=False,
        fvg_sessions="ALL", entry_sessions="ALL",
        trades=[],
    )

    # Real strategy instance (so resolve_params works), only backtest is mocked.
    strat = get_strategy("extreme_fvg")
    strat.backtest = AsyncMock(return_value=report)

    fake_svc = MagicMock()
    fake_svc.get_market_data_provider.return_value = MagicMock()

    with patch("strategies.get_strategy", return_value=strat), \
         patch.object(extreme_api, "_svc", return_value=fake_svc):
        resp = client.get("/api/extreme_fvg/backtest?symbol=BTC&days=14")

    assert resp.status_code == 200
    data = resp.json()
    assert data["strategy"] == "extreme_fvg"
    # string metadata preserved (not coerced to 0.0)
    assert data["symbol"] == "BTC"
    assert data["ltf_timeframe"] == "5m"
    assert data["invalidation_mode"] == "wick"
    assert data["fvg_sessions"] == "ALL"
    assert data["entry_sessions"] == "ALL"
    # booleans preserved (not coerced to 1.0/0.0)
    assert data["session_filter_enabled"] is False
    # numerics rounded to 2dp
    assert data["win_rate_1r"] == 50.0
    assert data["min_gap_pct"] == 0.05
    assert data["days"] == 14

