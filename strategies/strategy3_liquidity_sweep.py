"""
Strategy 3 adapter — Liquidity-Sweep FVG behind ``BaseStrategy``.

Registry name: ``liquidity_sweep_fvg``. Implements the video model
(4H FVG bias → liquidity sweep → LTF FVG entry → liquidity target) on top of
the shared 4H machinery, with data-proven gates. Strategy 2 files are untouched.
"""

from typing import Any, Dict, List

from strategies.base import BaseStrategy
from strategies.registry import register


@register
class Strategy3LiquiditySweepFVG(BaseStrategy):
    """Adapter for ``strategy_liquidity_sweep_fvg`` engine functions."""

    name = "liquidity_sweep_fvg"
    display_name = "Liquidity-Sweep FVG"

    default_params = {
        # Core scanning params (mirror S2 defaults)
        "ltf_timeframe": "5m",
        "min_gap_pct": 0.05,
        "completion_target": "2R",
        "use_close_invalidation": False,
        # Gates
        "require_sweep": True,
        "sweep_max_age_h": 2.0,
        "gap_band_exclude": "0.10,0.20",
        "anchor_age_guard": True,
        # Entry-session gate: NY killzone 13:00-16:00 UTC carried all profit.
        "entry_session_filter": True,
        "entry_sessions": "NY_KZ",
        # Take-profit
        "tp_mode": "LIQUIDITY",
        "min_rr_for_liquidity": 1.5,
        "fallback_target_r": 2.0,
    }

    async def find_setups(self, symbol: str, provider: Any, params: Dict[str, Any]) -> List[Any]:
        from session_filter import SessionFilterConfig
        from strategy_liquidity_sweep_fvg import get_liquidity_sweep_setup_for_symbol

        session_config = SessionFilterConfig.from_legacy(
            entry_session_filter=params.get("entry_session_filter", False),
            entry_sessions=params.get("entry_sessions", "ALL"),
        )

        setup = await get_liquidity_sweep_setup_for_symbol(
            symbol=symbol,
            ltf_timeframe=params.get("ltf_timeframe", "5m"),
            client=provider,
            use_close_invalidation=params.get("use_close_invalidation", False),
            min_gap_pct=params.get("min_gap_pct", 0.05),
            completion_target=params.get("completion_target", "2R"),
            require_sweep=params.get("require_sweep", True),
            sweep_max_age_h=params.get("sweep_max_age_h", 2.0),
            gap_band_exclude=params.get("gap_band_exclude", "0.10,0.20"),
            anchor_age_guard=params.get("anchor_age_guard", True),
            tp_mode=params.get("tp_mode", "LIQUIDITY"),
            min_rr_for_liquidity=params.get("min_rr_for_liquidity", 1.5),
            fallback_target_r=params.get("fallback_target_r", 2.0),
            session_config=session_config,
        )
        return [setup] if setup is not None else []

    async def backtest(self, symbol: str, days: int, provider: Any, params: Dict[str, Any]) -> Any:
        from backtest_liquidity_sweep_fvg import run_liquidity_sweep_backtest

        return await run_liquidity_sweep_backtest(
            symbol=symbol,
            days=days,
            ltf_timeframe=params.get("ltf_timeframe", "5m"),
            use_close_invalidation=params.get("use_close_invalidation", False),
            min_gap_pct=params.get("min_gap_pct", 0.05),
            require_sweep=params.get("require_sweep", True),
            sweep_max_age_h=params.get("sweep_max_age_h", 2.0),
            gap_band_exclude=params.get("gap_band_exclude", "0.10,0.20"),
            anchor_age_guard=params.get("anchor_age_guard", True),
            tp_mode=params.get("tp_mode", "LIQUIDITY"),
            min_rr_for_liquidity=params.get("min_rr_for_liquidity", 1.5),
            fallback_target_r=params.get("fallback_target_r", 2.0),
            entry_sessions=params.get("entry_sessions", "NY_KZ"),
            entry_weekday_only=params.get("entry_weekday_only", True),
            client=provider,
        )
