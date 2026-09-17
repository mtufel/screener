"""
Consolidated Trading Session and Weekday Filtering Module.

Provides SessionFilterConfig domain model, standard presets (NY, London, Asia, Killzones),
multi-session support, custom UTC intervals, cross-midnight handling, and deterministic
validation helpers for FVG formation and entry fills.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Standard predefined trading sessions (UTC)
SESSION_PRESETS: Dict[str, List[Tuple[int, int]]] = {
    # Full day (24/7)
    "ALL": [(0, 1440)],
    # New York Session: 13:00 - 22:00 UTC (780 - 1320 mins)
    "NY": [(780, 1320)],
    # London Session: 07:00 - 16:00 UTC (420 - 960 mins)
    "LONDON": [(420, 960)],
    # Asia / Tokyo Session: 00:00 - 09:00 UTC (0 - 540 mins)
    "ASIA": [(0, 540)],
    "TOKYO": [(0, 540)],
    # Combined London & NY Session: 07:00 - 22:00 UTC (420 - 1320 mins)
    "LONDON_NY": [(420, 1320)],
    # London Killzone: 07:00 - 10:00 UTC (420 - 600 mins)
    "LONDON_KZ": [(420, 600)],
    # New York Killzone: 13:00 - 16:00 UTC (780 - 960 mins)
    "NY_KZ": [(780, 960)],
}

_TIME_INTERVAL_REGEX = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


def is_weekday(timestamp_ms: int) -> bool:
    """Checks whether timestamp_ms falls on Monday-Friday in UTC (0=Mon, 4=Fri)."""
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return dt.weekday() < 5


def parse_minute_of_day(time_str: str) -> int:
    """Parses 'HH:MM' string to minute of day (0..1440)."""
    parts = time_str.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: '{time_str}', expected 'HH:MM'")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 24 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time values: {hour}:{minute}")
    return hour * 60 + minute


def parse_session_intervals(session_str: str) -> List[Tuple[int, int]]:
    """
    Parses a session specification into a list of [start_minute, end_minute) intervals.
    Supports:
      - Preset names: 'NY', 'LONDON', 'ASIA', 'LONDON_NY', 'ALL', etc.
      - Comma-separated presets/ranges: 'LONDON,NY' or 'LONDON_KZ,NY_KZ'
      - Custom UTC intervals: '08:00-12:00,13:00-17:00'
      - Cross-midnight intervals: '22:00-04:00' (split into [1320, 1440) and [0, 240))
    """
    if not session_str:
        return [(0, 1440)]

    raw_tokens = [t.strip() for t in session_str.split(",") if t.strip()]
    if not raw_tokens:
        return [(0, 1440)]

    intervals: List[Tuple[int, int]] = []

    for token in raw_tokens:
        token_upper = token.upper()

        if token_upper in SESSION_PRESETS:
            intervals.extend(SESSION_PRESETS[token_upper])
            continue

        match = _TIME_INTERVAL_REGEX.match(token)
        if match:
            start_h, start_m, end_h, end_m = map(int, match.groups())
            start_mins = start_h * 60 + start_m
            end_mins = end_h * 60 + end_m

            if start_mins < end_mins:
                intervals.append((start_mins, end_mins))
            elif start_mins > end_mins:
                # Cross-midnight interval
                intervals.append((start_mins, 1440))
                intervals.append((0, end_mins))
            else:
                # Equal start and end represents a full 24-hour cycle
                intervals.append((0, 1440))
            continue

        normalized = token_upper.replace(" ", "_").replace("-", "_")
        if normalized in SESSION_PRESETS:
            intervals.extend(SESSION_PRESETS[normalized])
            continue

        logger.warning("Unrecognized session token '%s'. Defaulting token to full 24h.", token)
        intervals.append((0, 1440))

    return intervals


def is_in_session(timestamp_ms: int, sessions: Union[str, List[str], None]) -> bool:
    """
    Validates whether timestamp_ms falls within any of the specified sessions.
    If sessions is None, empty, or 'ALL', returns True.
    """
    if sessions is None:
        return True

    if isinstance(sessions, (list, tuple, set)):
        session_str = ",".join(str(s).strip() for s in sessions if s)
    else:
        session_str = str(sessions).strip()

    if not session_str or session_str.upper() == "ALL":
        return True

    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    current_minute = dt.hour * 60 + dt.minute

    try:
        intervals = parse_session_intervals(session_str)
    except Exception as exc:
        logger.warning("Failed to parse sessions '%s': %s. Defaulting to True.", session_str, exc)
        return True

    for start_min, end_min in intervals:
        if start_min <= current_minute < end_min:
            return True

    return False


def is_in_ny_session(timestamp_ms: int) -> bool:
    """
    Backward-compatible check for New York session (13:00 - 22:00 UTC).
    Preserved for 100% compatibility with existing test suites.
    """
    return is_in_session(timestamp_ms, "NY")


@dataclass
class SessionFilterConfig:
    """
    Unified domain model managing FVG formation and trade entry session/weekday filtering.
    Treats 'ALL' as unconstrained/24/7.
    """
    fvg_sessions: str = "ALL"
    entry_sessions: str = "ALL"
    fvg_weekdays_only: bool = False
    entry_weekdays_only: bool = False

    def is_fvg_valid(self, timestamp_ms: int) -> bool:
        """Validates whether an FVG formation timestamp satisfies the session & weekday rules."""
        if self.fvg_weekdays_only and not is_weekday(timestamp_ms):
            return False
        if not self.fvg_sessions or self.fvg_sessions.strip().upper() == "ALL":
            return True
        return is_in_session(timestamp_ms, self.fvg_sessions)

    def is_entry_valid(self, timestamp_ms: int) -> bool:
        """Validates whether a trade entry trigger timestamp satisfies the session & weekday rules."""
        if self.entry_weekdays_only and not is_weekday(timestamp_ms):
            return False
        if not self.entry_sessions or self.entry_sessions.strip().upper() == "ALL":
            return True
        return is_in_session(timestamp_ms, self.entry_sessions)

    @classmethod
    def from_legacy(
        cls,
        session_filter: Optional[bool] = None,
        weekday_filter: Optional[bool] = None,
        entry_session_filter: Optional[bool] = None,
        entry_weekday_filter: Optional[bool] = None,
        sessions: Optional[str] = None,
        entry_sessions: Optional[str] = None,
        default_session: str = "NY",
    ) -> "SessionFilterConfig":
        """
        Factory method converting legacy boolean flags and optional session strings
        into a clean, unified SessionFilterConfig instance.
        """
        # Resolve FVG session
        if session_filter is False:
            resolved_fvg = "ALL"
        elif session_filter is True:
            resolved_fvg = sessions.strip() if (sessions and sessions.strip() and sessions.strip().upper() != "ALL") else default_session
        else:
            resolved_fvg = sessions.strip() if (sessions and sessions.strip()) else "ALL"

        # Resolve Entry session
        if entry_session_filter is False:
            resolved_entry = "ALL"
        elif entry_session_filter is True:
            resolved_entry = entry_sessions.strip() if (entry_sessions and entry_sessions.strip() and entry_sessions.strip().upper() != "ALL") else default_session
        else:
            resolved_entry = entry_sessions.strip() if (entry_sessions and entry_sessions.strip()) else "ALL"

        return cls(
            fvg_sessions=resolved_fvg,
            entry_sessions=resolved_entry,
            fvg_weekdays_only=bool(weekday_filter),
            entry_weekdays_only=bool(entry_weekday_filter),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes configuration to dictionary."""
        return {
            "fvg_sessions": self.fvg_sessions,
            "entry_sessions": self.entry_sessions,
            "fvg_weekdays_only": self.fvg_weekdays_only,
            "entry_weekdays_only": self.entry_weekdays_only,
            # Legacy backward-compatible fields
            "session_filter_enabled": self.fvg_sessions.strip().upper() != "ALL",
            "entry_session_filter_enabled": self.entry_sessions.strip().upper() != "ALL",
            "weekday_filter_enabled": self.fvg_weekdays_only,
            "entry_weekday_filter_enabled": self.entry_weekdays_only,
            "sessions": self.fvg_sessions,
        }
