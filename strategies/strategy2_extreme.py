"""
Strategy 2 adapter — wraps the existing Strategy 2 engine behind ``BaseStrategy``.

This is a thin, non-invasive adapter: it translates between the uniform
``BaseStrategy`` interface and the existing engine functions. The ~1200-line
FVG engine and ~830-line backtest engine are NOT rewritten; they are wrapped
as-is. Strategy 2's declarative defaults match the current production defaults,
so resolving params from runtime cfg yields byte-identical behavior to what
the daemon uses today.

Usage::

    from strategies import get_strategy
    s = get_strategy("extreme_fvg")
    params = s.resolve_params({"ltf_timeframe": "5m", "session_filter": True})
    setups = await s.find_setups("BTC", provider, params)
    report = await s.backtest("BTC", 30, provider, params)

Note: this module imports engine modules at function-call time (not at import
time) to avoid creating a hard import-time dependency that would prevent the
daemon from running when the engine is unavailable during testing.
"""

from typing import Any, Dict, List

from strategies.base import BaseStrategy
from strategies.registry import register


def _build_session_config(params: Dict[str, Any]) -> "SessionFilterConfig":
    """Reconstruct a SessionFilterConfig from flat params (matches engine's from_legacy)."""
    # Import here to keep the module import-time clean (engine may be absent in unit tests).
    from session_filter import SessionFilterConfig

    return SessionFilterConfig.from_legacy(
        session_filter=params.get("session_filter", False),
        weekday_filter=params.get("weekday_filter", False),
        entry_session_filter=params.get("entry_session_filter", False),
        entry_weekday_filter=params.get("entry_weekday_filter", False),
        sessions=params.get("sessions", "ALL"),
        entry_sessions=params.get("entry_sessions", "ALL"),
    )


@register
class Strategy2Extreme(BaseStrategy):
    """Adapter for the existing ``get_extreme_setup_for_symbol`` engine.

    Registry name: ``extreme_fvg``
    Display name:   ``Extreme LTF FVG``
    """

    name = "extreme_fvg"
    display_name = "Extreme LTF FVG"

    # Declarative defaults — mirror the current production env defaults so
    # runtime reconciliation with an empty runtime_overrides dict yields exactly
    # the same parameter values the daemon uses today.
    default_params = {
        # Core scanning params
        "ltf_timeframe": "5m",
        "min_gap_pct": 0.05,
        "completion_target": "2R",
        # Invalidation
        "use_close_invalidation": False,
        # Formation filter (applied at FVG completion time)
        "session_filter": False,
        "weekday_filter": False,
        "sessions": "ALL",
        # Entry filter (applied at first touch / anchor detection time)
        "entry_session_filter": False,
        "entry_weekday_filter": False,
        "entry_sessions": "ALL",
    }

    async def find_setups(
        self,
        symbol: str,
        provider: Any,
        params: Dict[str, Any],
    ) -> List[Any]:
        """Call ``get_extreme_setup_for_symbol`` and wrap result in a list.

        Strategy 2's engine returns at most one ``ExtremeTradeSetup`` per symbol;
        the uniform interface wraps it in a list so the daemon can iterate
        uniformly regardless of strategy.
        """
        # Import at call time — keeps module import-time clean.
        from strategy_extreme_fvg import get_extreme_setup_for_symbol

        session_config = _build_session_config(params)

        setup = await get_extreme_setup_for_symbol(
            symbol=symbol,
            ltf_timeframe=params.get("ltf_timeframe", "5m"),
            client=provider,
            use_close_invalidation=params.get("use_close_invalidation", False),
            min_gap_pct=params.get("min_gap_pct", 0.05),
            completion_target=params.get("completion_target", "2R"),
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
        """Run ``run_extreme_backtest`` with resolved params."""
        # Import at call time.
        from backtest_extreme_fvg import run_extreme_backtest

        session_config = _build_session_config(params)

        return await run_extreme_backtest(
            symbol=symbol,
            days=days,
            ltf_timeframe=params.get("ltf_timeframe", "5m"),
            use_close_invalidation=params.get("use_close_invalidation", False),
            min_gap_pct=params.get("min_gap_pct", 0.05),
            session_filter=params.get("session_filter", False),
            weekday_filter=params.get("weekday_filter", False),
            entry_session_filter=params.get("entry_session_filter", False),
            entry_weekday_filter=params.get("entry_weekday_filter", False),
            sessions=params.get("sessions"),
            entry_sessions=params.get("entry_sessions"),
            session_config=session_config,
            client=provider,
        )
