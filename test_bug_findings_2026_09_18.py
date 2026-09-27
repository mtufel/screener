"""Regression tests for docs/bug-findings-2026-09-18.md. Does not modify existing tests."""

import os
from datetime import datetime, timezone

import pytest

from hyperliquid_client import (
    SYMBOL_ALIASES,
    expand_mids_with_aliases,
    lookup_mid,
    resolve_symbol,
)
from session_filter import SessionFilterConfig, SESSION_PRESETS
from test_trade_lifecycle import FIVE_MIN_MS, T0, _evtypes, _tracker, mk_c, mk_setup
from backtest_extreme_fvg import simulate_trade_execution
from strategy_extreme_fvg import Candle, FVG, TouchedAnchor


def test_session_env_defaults_do_not_force_ny():
    """1.1: boolean filter off + sessions ALL must accept non-NY hours."""
    cfg = SessionFilterConfig.from_legacy(
        session_filter=False,
        sessions="ALL",
        entry_session_filter=False,
        entry_sessions="ALL",
    )
    london_morning = int(datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert cfg.fvg_sessions == "ALL"
    assert cfg.is_fvg_valid(london_morning) is True
    assert cfg.is_entry_valid(london_morning) is True
    if os.getenv("EXTREME_SESSIONS") is None:
        from main import EXTREME_SESSIONS, EXTREME_ENTRY_SESSIONS
        assert EXTREME_SESSIONS == "ALL"
        assert EXTREME_ENTRY_SESSIONS == "ALL"


def test_pending_anchor_breach_without_entry_invalidates(tmp_path):
    """1.2: 4H wick without tagging entry invalidates pending (SL not required)."""
    tracker = _tracker(tmp_path)
    setup = mk_setup(formed_at=T0, anchor_bottom=4392)  # SL is 4388
    tracker.process_live_setups([setup], {"PAXG": 4400.0}, {})

    # low 4390 < 4H bottom 4392, high 4395 < entry 4400, above SL 4388
    dip = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4394, 4395, 4390, 4392)]}
    events = tracker.process_live_setups([setup], {"PAXG": 4400.0}, dip)

    assert _evtypes(events) == ["SETUP_INVALIDATED"]
    assert len(tracker.active_trades) == 0
    assert tracker.history[0].state == "INVALIDATED"


def test_pending_bearish_anchor_breach_without_entry_invalidates(tmp_path):
    """1.2 bearish: high through 4H top without tagging entry invalidates."""
    bear = mk_setup(direction="Bearish", formed_at=T0, entry=4400.0, risk=12.0)
    bear["stop_loss"] = 4412.0
    bear["anchor"]["top"] = 4408.0
    tracker = _tracker(tmp_path)
    # high 4410 > top 4408, low 4405 > entry 4400, below SL 4412
    spike = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4406, 4410, 4405, 4407)]}
    events = tracker.process_live_setups([bear], {"PAXG": 4400.0}, spike)
    assert "SETUP_INVALIDATED" in _evtypes(events)
    assert len(tracker.active_trades) == 0


def test_gold_alias_and_rest_mids_expansion():
    """1.3 + 1.5: GOLD maps to PAXG; REST mids expand reverse aliases."""
    assert SYMBOL_ALIASES["GOLD"] == "PAXG"
    assert resolve_symbol("GOLD") == "PAXG"
    assert resolve_symbol("OIL") == "WTIOIL"

    expanded = expand_mids_with_aliases({"PAXG": 3400.5, "WTIOIL": 71.25, "BTC": 100000.0})
    assert expanded["GOLD"] == 3400.5
    assert expanded["XAU"] == 3400.5
    assert expanded["OIL"] == 71.25
    assert expanded["BTC"] == 100000.0

    rest_only = {"PAXG": 3400.5, "WTIOIL": 71.25}
    assert lookup_mid(rest_only, "GOLD", 0.0) == 3400.5
    assert lookup_mid(rest_only, "OIL", 0.0) == 71.25
    assert lookup_mid(expanded, "GOLD", 0.0) == 3400.5


def test_telegram_distance_uses_aliased_mid():
    """1.4: whitelist OIL against Hyperliquid WTIOIL mids is not 0%."""
    mids = {"WTIOIL": 70.0}
    entry = 68.0
    dist = ((lookup_mid(mids, "OIL", entry) - entry) / entry) * 100
    assert dist == pytest.approx(((70.0 - 68.0) / 68.0) * 100)


def test_session_preset_labels_match_engine():
    """1.6: Asia is 00:00-09:00 UTC; NY KZ is 13:00-16:00 UTC."""
    assert SESSION_PRESETS["ASIA"] == [(0, 540)]
    assert SESSION_PRESETS["NY_KZ"] == [(780, 960)]
    html = open("templates/index.html", encoding="utf-8").read()
    assert "Asia (00-09 UTC)" in html
    assert "NY KZ (13-16 UTC)" in html
    assert "Asia (00-08 UTC)" not in html
    assert "NY KZ (12-15 UTC)" not in html


def _candle(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=100.0)


def test_headline_losses_exclude_trades_that_already_hit_1r():
    """1.7: STOPPED_OUT after 1R is not a headline loss (independent targets)."""
    c1 = _candle(0, 85, 95, 80, 92)
    c2 = _candle(1000, 92, 115, 91, 114)
    c3 = _candle(2000, 114, 120, 100, 118)
    ltf_fvg = FVG("Bullish", 100, 95, c1, c2, c3, formed_at=2000, timeframe="15m")
    anchor = TouchedAnchor(ltf_fvg, first_touch_timestamp=1000, most_recent_touch_timestamp=1000)

    hit_then_sl = simulate_trade_execution(
        symbol="BTC",
        direction="Bullish",
        entry_price=100.0,
        stop_loss=90.0,
        entry_timestamp=3000,
        subsequent_candles=[
            _candle(3000, 100, 112, 98, 111),  # 1R
            _candle(4000, 109, 110, 88, 89),   # SL
        ],
        anchor=anchor,
        ltf_fvg=ltf_fvg,
    )
    pure_sl = simulate_trade_execution(
        symbol="ETH",
        direction="Bullish",
        entry_price=100.0,
        stop_loss=90.0,
        entry_timestamp=3000,
        subsequent_candles=[_candle(4000, 100, 101, 88, 89)],
        anchor=anchor,
        ltf_fvg=ltf_fvg,
    )
    trades = [hit_then_sl, pure_sl]
    losses = sum(1 for t in trades if t.exit_reason == "STOPPED_OUT" and not t.hit_1r)
    assert hit_then_sl.hit_1r is True
    assert hit_then_sl.exit_reason == "STOPPED_OUT"
    assert pure_sl.hit_1r is False
    assert losses == 1
