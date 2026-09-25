"""
Tests for unified session_filter.py and SessionFilterConfig.
"""

from datetime import datetime, timezone
import pytest

from session_filter import (
    SESSION_PRESETS,
    SessionFilterConfig,
    is_in_ny_session,
    is_in_session,
    is_weekday,
    parse_minute_of_day,
    parse_session_intervals,
)


def make_ts(year=2026, month=9, day=15, hour=14, minute=0) -> int:
    """Helper creating millisecond UTC timestamp. (Sep 15, 2026 is Tuesday)"""
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def test_session_filter_config_defaults():
    cfg = SessionFilterConfig()
    assert cfg.fvg_sessions == "ALL"
    assert cfg.entry_sessions == "ALL"
    assert cfg.fvg_weekdays_only is False
    assert cfg.entry_weekdays_only is False

    # Tuesday 14:00 UTC
    ts = make_ts(hour=14)
    assert cfg.is_fvg_valid(ts) is True
    assert cfg.is_entry_valid(ts) is True

    # Sunday 03:00 UTC
    sunday_ts = make_ts(day=20, hour=3)
    assert cfg.is_fvg_valid(sunday_ts) is True
    assert cfg.is_entry_valid(sunday_ts) is True


def test_session_filter_config_ny_session():
    cfg = SessionFilterConfig(fvg_sessions="NY", entry_sessions="NY")
    # 14:00 UTC is inside NY (13:00 - 22:00 UTC)
    ts_in = make_ts(hour=14)
    assert cfg.is_fvg_valid(ts_in) is True
    assert cfg.is_entry_valid(ts_in) is True

    # 10:00 UTC is outside NY
    ts_out = make_ts(hour=10)
    assert cfg.is_fvg_valid(ts_out) is False
    assert cfg.is_entry_valid(ts_out) is False


def test_session_filter_config_multi_session():
    cfg = SessionFilterConfig(fvg_sessions="LONDON,NY", entry_sessions="LONDON_KZ,NY_KZ")
    # 08:30 UTC: inside London (07:00-16:00), inside London KZ (07:00-10:00)
    ts_lon = make_ts(hour=8, minute=30)
    assert cfg.is_fvg_valid(ts_lon) is True
    assert cfg.is_entry_valid(ts_lon) is True

    # 11:30 UTC: inside London, but outside London KZ and NY KZ
    ts_mid = make_ts(hour=11, minute=30)
    assert cfg.is_fvg_valid(ts_mid) is True
    assert cfg.is_entry_valid(ts_mid) is False

    # 14:00 UTC: inside NY, inside NY KZ (13:00-16:00)
    ts_ny = make_ts(hour=14, minute=0)
    assert cfg.is_fvg_valid(ts_ny) is True
    assert cfg.is_entry_valid(ts_ny) is True


def test_session_filter_config_weekdays_only():
    cfg = SessionFilterConfig(fvg_weekdays_only=True, entry_weekdays_only=True)
    # Tuesday 14:00 UTC
    ts_weekday = make_ts(day=15, hour=14)
    assert cfg.is_fvg_valid(ts_weekday) is True
    assert cfg.is_entry_valid(ts_weekday) is True

    # Sunday 14:00 UTC (Sep 20, 2026 is Sunday)
    ts_weekend = make_ts(day=20, hour=14)
    assert cfg.is_fvg_valid(ts_weekend) is False
    assert cfg.is_entry_valid(ts_weekend) is False


def test_session_filter_config_from_legacy():
    # 1. Default None -> "ALL"
    c1 = SessionFilterConfig.from_legacy()
    assert c1.fvg_sessions == "ALL"
    assert c1.entry_sessions == "ALL"
    assert c1.fvg_weekdays_only is False

    # 2. session_filter=False -> "ALL"
    c2 = SessionFilterConfig.from_legacy(session_filter=False, entry_session_filter=False)
    assert c2.fvg_sessions == "ALL"
    assert c2.entry_sessions == "ALL"

    # 3. session_filter=True, sessions=None -> defaults to "NY"
    c3 = SessionFilterConfig.from_legacy(session_filter=True, entry_session_filter=True)
    assert c3.fvg_sessions == "NY"
    assert c3.entry_sessions == "NY"

    # 4. session_filter=True, sessions="LONDON"
    c4 = SessionFilterConfig.from_legacy(
        session_filter=True,
        sessions="LONDON",
        entry_session_filter=True,
        entry_sessions="LONDON,NY",
        weekday_filter=True,
        entry_weekday_filter=True,
    )
    assert c4.fvg_sessions == "LONDON"
    assert c4.entry_sessions == "LONDON,NY"
    assert c4.fvg_weekdays_only is True
    assert c4.entry_weekdays_only is True


def test_session_filter_config_to_dict():
    cfg = SessionFilterConfig(fvg_sessions="LONDON", entry_sessions="NY", fvg_weekdays_only=True)
    d = cfg.to_dict()
    assert d["fvg_sessions"] == "LONDON"
    assert d["entry_sessions"] == "NY"
    assert d["fvg_weekdays_only"] is True
    assert d["session_filter_enabled"] is True
    assert d["entry_session_filter_enabled"] is True
    assert d["weekday_filter_enabled"] is True


def test_backward_compatibility_is_in_ny_session():
    # 14:00 UTC is inside NY
    assert is_in_ny_session(make_ts(hour=14)) is True
    # 04:00 UTC is outside NY
    assert is_in_ny_session(make_ts(hour=4)) is False
    # 22:00 UTC is outside (exclusive)
    assert is_in_ny_session(make_ts(hour=22)) is False


def test_from_legacy_session_string_precedence():
    # Explicit sessions string takes precedence over session_filter=False (e.g. from default kwargs)
    c1 = SessionFilterConfig.from_legacy(session_filter=False, sessions="LONDON")
    assert c1.fvg_sessions == "LONDON"

    c2 = SessionFilterConfig.from_legacy(entry_session_filter=False, entry_sessions="NY")
    assert c2.entry_sessions == "NY"

    c3 = SessionFilterConfig.from_legacy(
        session_filter=False,
        sessions="LONDON",
        entry_session_filter=False,
        entry_sessions="NY",
    )
    assert c3.fvg_sessions == "LONDON"
    assert c3.entry_sessions == "NY"

    # sessions='ALL' explicitly preserves ALL
    c4 = SessionFilterConfig.from_legacy(session_filter=False, sessions="ALL")
    assert c4.fvg_sessions == "ALL"

