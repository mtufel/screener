"""
Tests for custom session time ranges and UI bindings.
"""

from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from session_filter import SessionFilterConfig
from main import app, state


def make_ts(year=2026, month=9, day=15, hour=14, minute=0) -> int:
    """Helper creating millisecond UTC timestamp (Sep 15, 2026 is Tuesday)."""
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def test_custom_session_time_single_range():
    cfg = SessionFilterConfig(fvg_sessions="13:30-20:00")
    # 13:29 UTC -> False
    assert cfg.is_fvg_valid(make_ts(hour=13, minute=29)) is False
    # 13:30 UTC -> True
    assert cfg.is_fvg_valid(make_ts(hour=13, minute=30)) is True
    # 17:00 UTC -> True
    assert cfg.is_fvg_valid(make_ts(hour=17, minute=0)) is True
    # 20:00 UTC -> False (exclusive end)
    assert cfg.is_fvg_valid(make_ts(hour=20, minute=0)) is False


def test_custom_session_time_multiple_ranges():
    cfg = SessionFilterConfig(fvg_sessions="08:00-12:00,13:00-17:00")
    # 07:59 -> False
    assert cfg.is_fvg_valid(make_ts(hour=7, minute=59)) is False
    # 09:30 -> True
    assert cfg.is_fvg_valid(make_ts(hour=9, minute=30)) is True
    # 12:30 -> False (gap between 12 and 13)
    assert cfg.is_fvg_valid(make_ts(hour=12, minute=30)) is False
    # 15:00 -> True
    assert cfg.is_fvg_valid(make_ts(hour=15, minute=0)) is True
    # 17:01 -> False
    assert cfg.is_fvg_valid(make_ts(hour=17, minute=1)) is False


def test_custom_session_time_cross_midnight():
    cfg = SessionFilterConfig(entry_sessions="22:00-04:00")
    # 21:59 -> False
    assert cfg.is_entry_valid(make_ts(hour=21, minute=59)) is False
    # 23:00 -> True
    assert cfg.is_entry_valid(make_ts(hour=23, minute=0)) is True
    # 02:00 -> True
    assert cfg.is_entry_valid(make_ts(hour=2, minute=0)) is True
    # 04:01 -> False
    assert cfg.is_entry_valid(make_ts(hour=4, minute=1)) is False


def test_template_contains_custom_session_controls():
    html_path = Path(__file__).resolve().parent.parent / "templates" / "index.html"
    assert html_path.exists(), "templates/index.html must exist"
    content = html_path.read_text(encoding="utf-8")

    # Check custom options and input fields in Extreme Screener Control Bar
    assert 'id="extremeSessions"' in content
    assert 'id="extremeSessionsCustom"' in content
    assert 'id="extremeEntrySessions"' in content
    assert 'id="extremeEntrySessionsCustom"' in content

    # Check custom options and input fields in Extreme Backtest form
    assert 'id="extremeBtSession"' in content
    assert 'id="extremeBtSessionCustom"' in content
    assert 'id="extremeBtEntrySession"' in content
    assert 'id="extremeBtEntrySessionCustom"' in content

    # Check JavaScript handlers
    assert "handleExtremeSessionSelect" in content
    assert "handleBtSessionSelect" in content
    assert "KNOWN_SESSION_PRESETS" in content
    assert "Custom (UTC)..." in content


def test_api_extreme_config_custom_sessions():
    client = TestClient(app)
    resp = client.post("/api/extreme/config?sessions=13:30-20:00&entry_sessions=08:00-11:30")
    assert resp.status_code == 200
    assert state.get("extreme_sessions") == "13:30-20:00"
    assert state.get("extreme_entry_sessions") == "08:00-11:30"
    assert state.get("extreme_session_filter") is True
    assert state.get("extreme_entry_session_filter") is True

    # Check status endpoint returns the custom sessions
    status_resp = client.get("/api/extreme/status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["sessions"] == "13:30-20:00"
    assert data["entry_sessions"] == "08:00-11:30"


def test_api_extreme_backtest_session_parameters_forwarding():
    from unittest.mock import AsyncMock, patch
    from backtest_extreme_fvg import ExtremeBacktestReport

    fake_report = ExtremeBacktestReport(
        symbol="BTC",
        days=30,
        ltf_timeframe="5m",
        invalidation_mode="close",
        min_gap_pct=0.05,
        session_filter_enabled=True,
        weekday_filter_enabled=False,
        entry_session_filter_enabled=True,
        entry_weekday_filter_enabled=False,
        fvg_sessions="LONDON",
        entry_sessions="NY",
        total_trades=10,
        wins_1r=7,
        wins_2r=5,
        wins_3r=3,
        losses=3,
        win_rate_1r=70.0,
        win_rate_2r=50.0,
        win_rate_3r=30.0,
        net_pnl_1r=4.0,
        net_pnl_2r=7.0,
        net_pnl_3r=6.0,
        profit_factor_1r=2.33,
        profit_factor_2r=3.33,
        profit_factor_3r=3.0,
        max_drawdown_r=2.0,
        avg_trade_duration_min=45.0,
        avg_mfe_r=2.5,
        trades=[],
    )

    client = TestClient(app)
    with patch("backtest_extreme_fvg.run_extreme_backtest", new_callable=AsyncMock) as mock_backtest:
        mock_backtest.return_value = fake_report

        resp = client.get("/api/extreme/backtest?symbol=BTC&days=30&sessions=LONDON&entry_sessions=NY")
        assert resp.status_code == 200
        mock_backtest.assert_called_once()
        kwargs = mock_backtest.call_args.kwargs

        # Verify session_config passed to backtest engine
        session_cfg = kwargs.get("session_config")
        assert session_cfg is not None
        assert session_cfg.fvg_sessions == "LONDON"
        assert session_cfg.entry_sessions == "NY"
        assert kwargs.get("session_filter") is True
        assert kwargs.get("entry_session_filter") is True

