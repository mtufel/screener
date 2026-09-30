"""
Tests for the strategy-parameterized API routes added by the strategy-extensibility change.

Covers:
  - GET /api/{strategy}/status  (valid + unknown strategy -> 404, not 500 NameError)
  - GET /api/{strategy}/info    (valid + unknown strategy -> 404, not 500 NameError)
  - GET /api/{strategy}/backtest (success path serializes metadata + numerics, unknown -> 404)
  - GET /api/{strategy}/scan    (generic live-scan route: S3 setups, scoped active trade, unknown -> 404)
  - GET /api/{strategy}/backtest S3 knobs (require_sweep/gap_band_exclude/tp_mode passthrough)
"""

from types import SimpleNamespace

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


# -----------------------------------------------------------------------------
# GET /api/{strategy}/scan — generic live setup scan
# -----------------------------------------------------------------------------

def _fake_s3_setup() -> SimpleNamespace:
    """Attribute-access stand-in for an S3 adapter setup (post _normalize_setup)."""
    return SimpleNamespace(
        direction="Bullish", state="PENDING_RETRACE",
        entry_price=64000.0, stop_loss=63880.0, risk_r=120.0, risk_pct=0.19,
        tp_1r=64120.0, tp_2r=64240.0, tp_3r=64360.0, floating_r=0.0,
        entry_time_ist="01-Jan 10:00 IST", entry_timestamp=1700000000000,
        completion_target="2R", ltf_timeframe="5m",
        ltf_fvg=SimpleNamespace(direction="Bullish", bottom=63800.0, top=63920.0,
                                width=120.0, gap_pct=0.1879,
                                formed_time_ist="01-Jan 09:55 IST", formed_at=1699999900000),
        anchor=None,
    )


def test_api_strategy_scan_returns_setup_payloads():
    import api.extreme as extreme_api

    client = TestClient(app)
    strat = get_strategy("liquidity_sweep_fvg")

    fake_provider = MagicMock()
    fake_provider.get_all_mids = AsyncMock(return_value={"BTC": 65000.0})
    fake_provider.resolve_symbol.side_effect = lambda s: s
    fake_svc = MagicMock()
    fake_svc.get_market_data_provider.return_value = fake_provider

    find_setups_mock = AsyncMock(return_value=[_fake_s3_setup()])
    with patch("strategies.get_strategy", return_value=strat), \
         patch.object(extreme_api, "_svc", return_value=fake_svc), \
         patch.object(strat, "find_setups", new=find_setups_mock), \
         patch("extreme_trade_tracker.extreme_trade_tracker"
               ".get_active_trade_for_symbol_and_scope", return_value=None):
        resp = client.get("/api/liquidity_sweep_fvg/scan?symbols=BTC,ETH")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["strategy"] == "liquidity_sweep_fvg"
    assert data["count"] == 2  # the mocked setup is returned once per symbol
    first = data["setups"][0]
    assert first["symbol"] == "BTC"
    assert first["direction"] == "Bullish"
    assert first["state"] == "PENDING_RETRACE"
    assert first["entry_price"] == 64000.0
    assert first["completion_target"] == "2R"
    assert first["target_fvg"]["gap_pct"] == 0.188
    assert first["strategy"] == "liquidity_sweep_fvg"
    # find_setups called once per requested symbol with default params
    assert find_setups_mock.await_count == 2
    call_params = find_setups_mock.await_args_list[0].kwargs["params"]
    assert call_params["require_sweep"] is True
    assert call_params["entry_sessions"] == "NY_KZ"


def test_api_strategy_scan_prefers_active_trade_in_strategy_scope():
    import api.extreme as extreme_api

    client = TestClient(app)
    strat = get_strategy("liquidity_sweep_fvg")

    active_trade = SimpleNamespace(
        direction="Bearish", entry_price=64000.0, stop_loss=64120.0,
        risk_r=120.0, risk_pct=0.19, tp_1r=63880.0, tp_2r=63760.0, tp_3r=63640.0,
        completion_target="2R", ltf_timeframe="5m", strategy="liquidity_sweep_fvg",
        strategy_params={"tp_mode": "LIQUIDITY"}, entry_filled_at_ist="01-Jan 10:00 IST",
        entry_timestamp=1700000000000, htf_anchor={}, ltf_fvg={},
    )

    fake_provider = MagicMock()
    fake_provider.get_all_mids = AsyncMock(return_value={"BTC": 63900.0})
    fake_provider.resolve_symbol.side_effect = lambda s: s
    fake_svc = MagicMock()
    fake_svc.get_market_data_provider.return_value = fake_provider

    find_setups_mock = AsyncMock(return_value=[])
    with patch("strategies.get_strategy", return_value=strat), \
         patch.object(extreme_api, "_svc", return_value=fake_svc), \
         patch.object(strat, "find_setups", new=find_setups_mock), \
         patch("extreme_trade_tracker.extreme_trade_tracker"
               ".get_active_trade_for_symbol_and_scope", return_value=active_trade):
        resp = client.get("/api/liquidity_sweep_fvg/scan?symbols=BTC")

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    setup = data["setups"][0]
    assert setup["state"] == "TRADE_ACTIVE"
    assert setup["strategy"] == "liquidity_sweep_fvg"
    # No new setup scan when an active trade already covers the symbol
    find_setups_mock.assert_not_awaited()


def test_api_strategy_scan_unknown_strategy_returns_404():
    client = TestClient(app)
    resp = client.get("/api/does_not_exist/scan")
    assert resp.status_code == 404
    assert "Unknown strategy" in resp.json()["detail"]
    assert "extreme_fvg" in resp.json()["detail"]  # available list surfaced


# -----------------------------------------------------------------------------
# GET /api/{strategy}/backtest — S3 knob passthrough via resolve_params
# -----------------------------------------------------------------------------

def test_api_strategy_backtest_s3_knobs_passthrough():
    """S3-specific query knobs must reach strat.backtest params (None-filtered,
    'none' gap band -> None), while unset knobs keep strategy defaults."""
    import api.extreme as extreme_api
    from backtest_liquidity_sweep_fvg import Strategy3BacktestReport

    client = TestClient(app)
    strat = get_strategy("liquidity_sweep_fvg")

    report = Strategy3BacktestReport(
        symbol="BTC", days=10, ltf_timeframe="5m", invalidation_mode="close",
        min_gap_pct=0.05, require_sweep=False, sweep_max_age_h=4.0,
        gap_band=None, anchor_age_guard=True, tp_mode="FIXED_R",
        total_trades=3, wins=2, losses=1, win_rate=66.7, net_r=1.2345,
        profit_factor=2.5, max_drawdown_r=1.0, avg_hold_min=45.0, avg_mfe_r=0.8,
        liquidity_tp_count=1, fixed_tp_count=2, gate_rejects={"GAP_BAND": 4},
        trades=[],
    )

    fake_svc = MagicMock()
    fake_svc.get_market_data_provider.return_value = MagicMock()

    with patch("strategies.get_strategy", return_value=strat), \
         patch.object(extreme_api, "_svc", return_value=fake_svc), \
         patch.object(strat, "backtest", new=AsyncMock(return_value=report)):
        resp = client.get(
            "/api/liquidity_sweep_fvg/backtest?symbol=BTC&days=10"
            "&require_sweep=false&gap_band_exclude=none&tp_mode=FIXED_R"
            "&min_rr_for_liquidity=2.0&fallback_target_r=3.0&sweep_max_age_h=4"
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["strategy"] == "liquidity_sweep_fvg"
        assert data["tp_mode"] == "FIXED_R"
        assert data["gap_band"] is None  # tuple serialized as JSON null
        assert data["net_r"] == 1.23     # rounded to 2dp
        assert data["gate_rejects"] == {"GAP_BAND": 4}

        # Knobs arrived in params exactly as requested ('none' passes through
        # as a string; the S3 engine's parse_gap_band('none') -> None disables it)
        params = strat.backtest.await_args.kwargs["params"]
        assert params["require_sweep"] is False
        assert params["gap_band_exclude"] == "none"
        assert params["tp_mode"] == "FIXED_R"
        assert params["min_rr_for_liquidity"] == 2.0
        assert params["fallback_target_r"] == 3.0
        assert params["sweep_max_age_h"] == 4.0
        # Unset knobs keep strategy defaults
        assert params["anchor_age_guard"] is True
        assert params["entry_sessions"] == "NY_KZ"

