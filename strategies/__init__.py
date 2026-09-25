"""
Pluggable trading-strategy framework.

Importing this package triggers strategy registration: every strategy module
under this package is imported so its ``@register`` decorator runs, populating
the registry. Callers then use ``get_strategy(name)`` / ``list_strategy_names()``
without importing engines directly.

Import order matters: ``registry`` (and its ``base`` dependency) is imported
before any concrete strategy module so the ``@register`` decorator is available
when strategies import it.
"""

from strategies.registry import get_strategy, list_strategy_names, register, registry_snapshot

# Import concrete strategies so they self-register. Each must be import-safe
# with no import of the daemon/API.
from strategies import strategy2_extreme  # noqa: E402,F401  (triggers registration)

__all__ = [
    "get_strategy",
    "list_strategy_names",
    "register",
    "registry_snapshot",
]
