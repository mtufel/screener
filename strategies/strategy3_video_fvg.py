"""
Strategy 3 adapter — wraps the Video FVG engine behind ``BaseStrategy``.

Thin, non-invasive adapter mirroring ``Strategy2Extreme``: it translates between
the uniform ``BaseStrategy`` interface and the standalone ``strategy_video_fvg``
engine (and ``backtest_video_fvg``). Engine modules are imported at call time (not
import time) so this module stays import-safe for unit tests that patch the engine.

Registry name: ``video_fvg`` — the strategy-parameterized API routes
(``/api/video_fvg/backtest``, ``/api/video_fvg/status``, ``/api/video_fvg/info``)
and daemon orchestration resolve it automatically with no core changes.

Usage::

    from strategies import get_strategy
    s = get_strategy("video_fvg")
    setups = await s.find_setups("BTC", provider, s.resolve_params({}))
    report = await s.backtest("BTC", 30, provider, s.resolve_params({}))
"""

from typing import Any, Dict, List

from strategies.base import BaseStrategy
from strategies.registry import register


def _build_session_config(params: Dict[str, Any]) -> "SessionFilterConfig":
    """Reconstruct a SessionFilterConfig from flat params (matches engine's from_legacy)."""
    from session_filter import SessionFilterConfig

    return SessionFilterConfig.from_legacy(
        session_filter=params.get("session_filter", False),
        weekday_filter=params.get("weekday_filter", False),
        sessions=params.get("sessions", "ALL"),
    )


@register
class Strategy3VideoFVG(BaseStrategy):
    """Adapter for the ``get_video_setup_for_symbol`` engine.

    Registry name: ``video_fvg``
    Display name:   ``Video FVG (4H Anchor)``
    """

    name = "video_fvg"
    display_name = "Video FVG (4H Anchor)"

    # Declarative defaults — mirror the Video FVG production defaults in the
    # design doc: 5m LTF, 0.03% min gap, 50% HTF confirmation body, 3R target.
    default_params = {
        # Core scanning params
        "ltf_timeframe": "5m",
        "min_gap_pct": 0.03,
        "completion_target": "3R",
        "htf_confirm_body_pct": 0.5,
        # Session filter (applied at LTF FVG formation time)
        "session_filter": False,
        "weekday_filter": False,
        "sessions": "ALL",
    }

    async def find_setups(
        self,
        symbol: str,
        provider: Any,
        params: Dict[str, Any],
    ) -> List[Any]:
        """Call ``get_video_setup_for_symbol`` and wrap the result in a list.

        Returns at most one ``VideoFVGSetup`` per symbol (the single qualifying
        anchor -> first-LTF-FVG setup); the uniform interface wraps it in a list.
        """
        # Import at call time — keeps module import-time clean.
        from strategy_video_fvg import get_video_setup_for_symbol

        session_config = _build_session_config(params)

        setup = await get_video_setup_for_symbol(
            symbol=symbol,
            ltf_timeframe=params.get("ltf_timeframe", "5m"),
            client=provider,
            min_gap_pct=params.get("min_gap_pct", 0.03),
            htf_confirm_body_pct=params.get("htf_confirm_body_pct", 0.5),
            completion_target=params.get("completion_target", "3R"),
            session_filter=params.get("session_filter", False),
            weekday_filter=params.get("weekday_filter", False),
            sessions=params.get("sessions"),
            session_config=session_config,
        )
        return [setup] if setup is not None else []

    async def backtest(
        self,
        symbol: str,
        days: int,
        provider: Any,
        params: Dict[str, Any],
    ) -> Any:
        """Run ``run_video_fvg_backtest`` with resolved params."""
        # Import at call time.
        from backtest_video_fvg import run_video_fvg_backtest

        session_config = _build_session_config(params)

        return await run_video_fvg_backtest(
            symbol=symbol,
            days=days,
            ltf_timeframe=params.get("ltf_timeframe", "5m"),
            min_gap_pct=params.get("min_gap_pct", 0.03),
            htf_confirm_body_pct=params.get("htf_confirm_body_pct", 0.5),
            completion_target=params.get("completion_target", "3R"),
            session_filter=params.get("session_filter", False),
            weekday_filter=params.get("weekday_filter", False),
            sessions=params.get("sessions"),
            session_config=session_config,
            client=provider,
        )
