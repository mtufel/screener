"""
Pluggable trading-strategy framework.

This package mirrors freqtrade's extensibility model (``IStrategy`` +
``StrategyResolver``): each strategy is a self-contained class that declares
its own tunable parameters as defaults, is addressable by a stable name, and
implements a uniform ``find_setups``/``backtest`` interface. The daemon, API,
and trade ledger are strategy-agnostic and reach strategies only through the
registry by name.

Design notes (see openspec change `strategy-extensibility`):
- `BaseStrategy` is the `IStrategy` analog: declarative class-attribute
  parameters and an explicit interface version.
- `registry` (in ``registry.py``) is the `StrategyResolver` analog: strategies
  self-register by stable name and are resolved at runtime by name.
- Parameter precedence follows freqtrade: explicit runtime config >
  strategy-declared default > built-in default (see `BaseStrategy.resolve_params`).
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from app_config import (
    EXTREME_COMPLETION_TARGET,
    EXTREME_ENTRY_SESSIONS,
    EXTREME_ENTRY_SESSION_FILTER_ENABLED,
    EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED,
    EXTREME_LTF_TIMEFRAME,
    EXTREME_MIN_GAP_PCT,
    EXTREME_SESSIONS,
    EXTREME_SESSION_FILTER_ENABLED,
    EXTREME_USE_CLOSE_INVALIDATION,
    EXTREME_WEEKDAY_FILTER_ENABLED,
)


class BaseStrategy(ABC):
    """Abstract interface every trading strategy must implement.

    A strategy declares its identity via ``name``/``display_name`` and its
    tunable parameters via ``default_params`` (freqtrade's class-attribute
    pattern). Subclasses implement ``find_setups`` (live setup generation)
    and ``backtest`` (historical simulation). Strategies must NOT import the
    daemon or API; they depend only on this interface and the data provider.
    """

    # Stable registry key (freqtrade uses the class name; here we use a stable
    # slug so renames don't break persisted trades / API routes).
    name: str = ""
    display_name: str = ""

    # Strategy interface version (freqtrade's `INTERFACE_VERSION` analog).
    interface_version: int = 1

    # Declarative default parameters. Subclasses override to declare their own.
    default_params: Dict[str, Any] = {
        "ltf_timeframe": EXTREME_LTF_TIMEFRAME,
        "completion_target": EXTREME_COMPLETION_TARGET,
        "min_gap_pct": EXTREME_MIN_GAP_PCT,
        "use_close_invalidation": EXTREME_USE_CLOSE_INVALIDATION,
        "session_filter": EXTREME_SESSION_FILTER_ENABLED,
        "weekday_filter": EXTREME_WEEKDAY_FILTER_ENABLED,
        "entry_session_filter": EXTREME_ENTRY_SESSION_FILTER_ENABLED,
        "entry_weekday_filter": EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED,
        "sessions": EXTREME_SESSIONS,
        "entry_sessions": EXTREME_ENTRY_SESSIONS,
    }

    def resolve_params(self, runtime_overrides: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve effective parameters with freqtrade precedence.

        Precedence: explicit ``runtime_overrides`` > ``self.default_params`` >
        built-in base defaults. The returned dict contains every key present in
        the union of runtime overrides and declarative defaults so downstream
        consumers can rely on a complete, observable parameter set.
        """
        resolved: Dict[str, Any] = dict(self.default_params)
        resolved.update(runtime_overrides or {})
        return resolved

    @abstractmethod
    async def find_setups(
        self,
        symbol: str,
        provider: Any,
        params: Dict[str, Any],
    ) -> List[Any]:
        """Generate trade setups for ``symbol`` using ``params``.

        ``provider`` is a market-data provider exposing the candle / mids
        interface used by the daemon. Returns a (possibly empty) list of
        strategy-specific setup objects the orchestration loop can render into
        scan/alert payloads.
        """

    @abstractmethod
    async def backtest(
        self,
        symbol: str,
        days: int,
        provider: Any,
        params: Dict[str, Any],
    ) -> Any:
        """Run a historical backtest for ``symbol`` over ``days`` using ``params``.

        Returns a strategy-specific backtest report object.
        """

    def __repr__(self) -> str:  # pragma: no cover - convenience only
        return f"<{type(self).__name__} name={self.name!r}>"
