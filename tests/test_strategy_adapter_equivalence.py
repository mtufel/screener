"""
Tests that Strategy2Extreme adapter produces equivalent results to direct
engine calls (get_extreme_setup_for_symbol / run_extreme_backtest).

These tests verify that the adapter is a thin, non-invasive wrapper that
translates the uniform BaseStrategy interface to the existing engine without
changing behavior.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from strategies import get_strategy
from strategies.strategy2_extreme import Strategy2Extreme


def test_strategy2_extreme_has_correct_name_and_display():
    strat = get_strategy("extreme_fvg")
    assert strat.name == "extreme_fvg"
    assert strat.display_name == "Extreme LTF FVG"


def test_strategy2_extreme_default_params_match_current_production():
    strat = get_strategy("extreme_fvg")
    # These must match the current production defaults so resolving empty
    # overrides yields byte-identical params to what the daemon uses today.
    params = strat.resolve_params({})
    assert params["ltf_timeframe"] == "5m"
    assert params["completion_target"] == "2R"
    assert params["min_gap_pct"] == 0.05
    assert params["use_close_invalidation"] is False
    assert params["session_filter"] is False


@pytest.mark.asyncio
async def test_adapter_find_setups_calls_engine_with_correct_kwargs():
    """Verify the adapter passes engine kwargs correctly by mocking the engine."""
    strat = get_strategy("extreme_fvg")
    mock_provider = MagicMock()
    params = {
        "ltf_timeframe": "5m",
        "use_close_invalidation": False,
        "min_gap_pct": 0.05,
        "completion_target": "2R",
        "session_filter": False,
        "weekday_filter": False,
        "sessions": "ALL",
        "entry_session_filter": False,
        "entry_weekday_filter": False,
        "entry_sessions": "ALL",
    }

    with patch(
        "strategy_extreme_fvg.get_extreme_setup_for_symbol",
        new_callable=AsyncMock,
    ) as mock_engine:
        mock_engine.return_value = None
        await strat.find_setups("BTC", mock_provider, params)
        mock_engine.assert_called_once()
        call_kwargs = mock_engine.call_args.kwargs
        assert call_kwargs["symbol"] == "BTC"
        assert call_kwargs["ltf_timeframe"] == "5m"
        assert call_kwargs["min_gap_pct"] == 0.05
        assert call_kwargs["completion_target"] == "2R"


@pytest.mark.asyncio
async def test_adapter_find_setups_returns_list():
    """When the engine returns a setup, adapter returns it in a list."""
    strat = get_strategy("extreme_fvg")
    mock_provider = MagicMock()
    mock_setup = MagicMock()
    mock_setup.entry_price = 60000.0
    params = {
        "ltf_timeframe": "5m",
        "use_close_invalidation": False,
        "min_gap_pct": 0.05,
        "completion_target": "2R",
        "session_filter": False,
        "weekday_filter": False,
        "sessions": "ALL",
        "entry_session_filter": False,
        "entry_weekday_filter": False,
        "entry_sessions": "ALL",
    }

    with patch(
        "strategy_extreme_fvg.get_extreme_setup_for_symbol",
        new_callable=AsyncMock,
    ) as mock_engine:
        mock_engine.return_value = mock_setup
        result = await strat.find_setups("ETH", mock_provider, params)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] is mock_setup


@pytest.mark.asyncio
async def test_adapter_find_setups_returns_empty_when_engine_returns_none():
    """When engine returns None, adapter returns []. (Daemon iterates gracefully.)"""
    strat = get_strategy("extreme_fvg")
    mock_provider = MagicMock()
    params = {
        "ltf_timeframe": "5m",
        "use_close_invalidation": False,
        "min_gap_pct": 0.05,
        "completion_target": "2R",
        "session_filter": False,
        "weekday_filter": False,
        "sessions": "ALL",
        "entry_session_filter": False,
        "entry_weekday_filter": False,
        "entry_sessions": "ALL",
    }

    with patch(
        "strategy_extreme_fvg.get_extreme_setup_for_symbol",
        new_callable=AsyncMock,
    ) as mock_engine:
        mock_engine.return_value = None
        result = await strat.find_setups("SOL", mock_provider, params)
        assert result == []


@pytest.mark.asyncio
async def test_adapter_backtest_calls_engine_with_correct_kwargs():
    """Verify the adapter's backtest passes engine kwargs correctly."""
    strat = get_strategy("extreme_fvg")
    mock_provider = MagicMock()
    params = {
        "ltf_timeframe": "5m",
        "use_close_invalidation": False,
        "min_gap_pct": 0.05,
        "session_filter": False,
        "weekday_filter": False,
        "entry_session_filter": False,
        "entry_weekday_filter": False,
        "sessions": "ALL",
        "entry_sessions": "ALL",
    }

    mock_report = MagicMock()
    mock_report.symbol = "BTC"

    with patch(
        "backtest_extreme_fvg.run_extreme_backtest",
        new_callable=AsyncMock,
    ) as mock_engine:
        mock_engine.return_value = mock_report
        await strat.backtest("BTC", 14, mock_provider, params)
        mock_engine.assert_called_once()
        call_kwargs = mock_engine.call_args.kwargs
        assert call_kwargs["symbol"] == "BTC"
        assert call_kwargs["days"] == 14
        assert call_kwargs["ltf_timeframe"] == "5m"
        assert call_kwargs["min_gap_pct"] == 0.05
        assert call_kwargs["session_filter"] is False
