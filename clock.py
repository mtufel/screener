"""
Virtual clock module (clock.py).

Provides a process-wide injectable time source so replay backtests can run the
LIVE execution path against historical data: when a ``VirtualClock`` is
installed, every ``clock.now_ms()`` read resolves to the simulated time; when
none is installed (the default), reads resolve to real system time and
behavior is byte-identical to the previous direct ``time.time()`` calls.

Design notes (openspec change `unified-strategy-ui-replay-backtest`, D1):
- An explicit ``current_time_ms``/``now_ms`` argument at any call site always
  takes precedence over the clock (engine-level override).
- The installed clock is stored in a ContextVar so concurrent async tasks
  (e.g. the live daemon running while a replay advances elsewhere) never
  observe each other's clock.
- The context manager reinstalls the previous clock on exit, so nesting
  (a replay cycle triggering code that itself installs a clock) is safe.
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Iterator, Optional


class VirtualClock:
    """Simulated wall clock for replay runs. Monotonic: never goes backwards."""

    def __init__(self, start_ms: int):
        self._now_ms = int(start_ms)

    def now_ms(self) -> int:
        return self._now_ms

    def advance_to(self, ts_ms: int) -> int:
        """Advances the clock to ``ts_ms`` (ignored if it would move backwards).

        Returns the clock value after the call.
        """
        ts_ms = int(ts_ms)
        if ts_ms > self._now_ms:
            self._now_ms = ts_ms
        return self._now_ms


_current_clock: ContextVar[Optional[VirtualClock]] = ContextVar(
    "fvg_virtual_clock", default=None
)


def now_ms() -> int:
    """Epoch milliseconds from the installed virtual clock, else real time."""
    clock = _current_clock.get()
    if clock is not None:
        return clock.now_ms()
    return int(time.time() * 1000)


def now() -> datetime:
    """Timezone-aware UTC datetime from the installed clock, else real time."""
    return datetime.fromtimestamp(now_ms() / 1000.0, tz=timezone.utc)


def installed_clock() -> Optional[VirtualClock]:
    """Returns the installed VirtualClock for direct advance control, or None."""
    return _current_clock.get()


@contextmanager
def install_virtual_clock(clock: Optional[VirtualClock]) -> Iterator[Optional[VirtualClock]]:
    """Installs ``clock`` for the duration of the context, restoring prior state on exit."""
    token = _current_clock.set(clock)
    try:
        yield clock
    finally:
        _current_clock.reset(token)
