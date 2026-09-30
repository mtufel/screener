"""
Strategy registry / resolver (freqtrade `StrategyResolver` analog).

Strategies self-register by stable name via the ``@register`` decorator. The
daemon and API resolve strategies by name through ``get_strategy`` rather than
importing engine modules directly, so adding a new strategy is a drop-in
operation (new file + ``@register``) that needs no orchestration changes.
"""

from typing import Dict, List, Type

from strategies.base import BaseStrategy

# Name -> strategy class. Classes (not instances) are stored; instances are
# created on resolution so per-run state is never shared across callers.
_STRATEGIES: Dict[str, Type[BaseStrategy]] = {}


def register(cls: Type[BaseStrategy]) -> Type[BaseStrategy]:
    """Class decorator that registers a strategy under its ``name``."""
    if not getattr(cls, "name", ""):
        raise ValueError(
            f"{cls.__name__} must define a non-empty `name` to be registered"
        )
    if cls.name in _STRATEGIES:
        raise ValueError(
            f"Strategy name {cls.name!r} already registered by {_STRATEGIES[cls.name].__name__}"
        )
    _STRATEGIES[cls.name] = cls
    return cls


def get_strategy(name: str, **init_kwargs) -> BaseStrategy:
    """Return an instance of the strategy registered under ``name``.

    Raises ``KeyError`` (via ``_unknown_strategy``) when ``name`` is not
    registered, listing the available strategies so the caller can recover.
    """
    cls = _STRATEGIES.get(name)
    if cls is None:
        available = ", ".join(sorted(_STRATEGIES)) or "(none)"
        raise KeyError(
            f"Unknown strategy {name!r}. Available strategies: {available}"
        )
    return cls(**init_kwargs)


def list_strategy_names() -> List[str]:
    """Return the sorted list of registered strategy names."""
    return sorted(_STRATEGIES)


def registry_snapshot() -> Dict[str, str]:
    """Return name -> display_name for every registered strategy (for APIs)."""
    return {name: cls.display_name for name, cls in _STRATEGIES.items()}
