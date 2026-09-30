"""
Tests for the strategy registry (strategies/registry.py).
Verifies: enumerate, resolve, unknown-name error with available list,
and resolve_params precedence.
"""

import pytest

from strategies import get_strategy, list_strategy_names, registry_snapshot
from strategies.base import BaseStrategy
from strategies.registry import register


def test_list_strategy_names_contains_extreme_fvg():
    names = list_strategy_names()
    assert "extreme_fvg" in names
    assert isinstance(names, list)
    assert names == sorted(names)  # must be sorted


def test_registry_snapshot_has_extreme_fvg_display_name():
    snap = registry_snapshot()
    assert "extreme_fvg" in snap
    assert snap["extreme_fvg"] == "Extreme LTF FVG"


def test_get_strategy_returns_strategy_instance():
    strat = get_strategy("extreme_fvg")
    assert strat is not None
    assert strat.name == "extreme_fvg"
    assert strat.display_name == "Extreme LTF FVG"


def test_get_strategy_unknown_raises_keyerror_with_available():
    with pytest.raises(KeyError) as exc_info:
        get_strategy("strategy99")
    msg = str(exc_info.value)
    assert "strategy99" in msg
    assert "extreme_fvg" in msg


def test_get_strategy_returns_new_instance_each_call():
    s1 = get_strategy("extreme_fvg")
    s2 = get_strategy("extreme_fvg")
    assert s1 is not s2  # instances not shared


def test_resolve_params_returns_defaults_when_empty():
    strat = get_strategy("extreme_fvg")
    params = strat.resolve_params({})
    assert params["ltf_timeframe"] == "5m"
    assert params["completion_target"] == "2R"
    assert params["min_gap_pct"] == 0.05
    assert params["session_filter"] is False


def test_resolve_params_runtime_overrides_win():
    strat = get_strategy("extreme_fvg")
    params = strat.resolve_params({
        "session_filter": True,
        "ltf_timeframe": "15m",
    })
    assert params["session_filter"] is True
    assert params["ltf_timeframe"] == "15m"
    # Unset keys still come from defaults
    assert params["completion_target"] == "2R"


def test_resolve_params_partial_override_only_affects_override_keys():
    strat = get_strategy("extreme_fvg")
    base = strat.resolve_params({})
    partial = strat.resolve_params({"min_gap_pct": 0.1})
    assert partial["min_gap_pct"] == 0.1
    assert partial["session_filter"] == base["session_filter"]


def test_register_rejects_empty_name():
    with pytest.raises(ValueError) as exc_info:
        @register
        class NoNameStrategy(BaseStrategy):
            name = ""
            display_name = "No Name"
            async def find_setups(self, symbol, provider, params):
                return []
            async def backtest(self, symbol, days, provider, params):
                return {}
    assert "non-empty" in str(exc_info.value).lower()


def test_register_rejects_duplicate_name():
    # "extreme_fvg" is already registered; attempting to register another class
    # with the same name should raise.
    with pytest.raises(ValueError) as exc_info:
        @register
        class DuplicateExtreme(BaseStrategy):
            name = "extreme_fvg"
            display_name = "Duplicate"
            async def find_setups(self, symbol, provider, params):
                return []
            async def backtest(self, symbol, days, provider, params):
                return {}
    assert "extreme_fvg" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Strategy 3 (liquidity_sweep_fvg) registration & defaults
# ---------------------------------------------------------------------------

def test_list_strategy_names_contains_liquidity_sweep_fvg():
    names = list_strategy_names()
    assert "liquidity_sweep_fvg" in names


def test_registry_snapshot_has_liquidity_sweep_display_name():
    snap = registry_snapshot()
    assert snap.get("liquidity_sweep_fvg") == "Liquidity-Sweep FVG"


def test_get_strategy_returns_strategy3_instance():
    strat = get_strategy("liquidity_sweep_fvg")
    assert strat is not None
    assert strat.name == "liquidity_sweep_fvg"
    assert strat.interface_version == 1


def test_strategy3_defaults_match_validated_config():
    strat = get_strategy("liquidity_sweep_fvg")
    params = strat.resolve_params({})
    # Gates ON per strategy3_validation_report.html
    assert params["require_sweep"] is True
    assert params["sweep_max_age_h"] == 2.0
    assert params["anchor_age_guard"] is True
    assert params["gap_band_exclude"] == "0.10,0.20"
    assert params["entry_sessions"] == "NY_KZ"
    assert params["tp_mode"] == "LIQUIDITY"
    assert params["min_rr_for_liquidity"] == 1.5
    assert params["fallback_target_r"] == 2.0


def test_strategy3_runtime_overrides_win():
    strat = get_strategy("liquidity_sweep_fvg")
    params = strat.resolve_params({"tp_mode": "FIXED_R", "require_sweep": False})
    assert params["tp_mode"] == "FIXED_R"
    assert params["require_sweep"] is False
    # Untouched defaults survive
    assert params["sweep_max_age_h"] == 2.0


# ---------------------------------------------------------------------------
# Strategy 4 (video_fvg) registration & defaults
# ---------------------------------------------------------------------------

def test_list_strategy_names_contains_video_fvg():
    names = list_strategy_names()
    assert "video_fvg" in names


def test_registry_snapshot_has_video_fvg_display_name():
    snap = registry_snapshot()
    assert snap.get("video_fvg") == "Video FVG (4H Anchor)"


def test_get_strategy_returns_strategy4_instance():
    strat = get_strategy("video_fvg")
    assert strat is not None
    assert strat.name == "video_fvg"
    assert strat.interface_version == 1

