"""
Tests for the Strategy 3 entry-session gate probe (openspec/changes/fix-s3-entry-session-gate-probe).

The gate must constrain the FILL, not the FVG formation. The source states it
directly: "It's completely fine for this fair value gap to form outside of the
session, but the entry has to be during the key time of day."

Pre-fix the gate called `is_entry_valid(formed_close_ts)`, which discarded every
candidate that formed out-of-session — including ones that would have filled in
one — and disagreed with both the sibling anchor-age gate (fill probe) and the
backtester (`_in_entry_session(fill_ts, ...)`).

These tests pin the timestamp semantics of `select_gated_ltf_fvg`.
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from strategy_extreme_fvg import FVG, TouchedAnchor
from strategy_liquidity_sweep_fvg import select_gated_ltf_fvg
from session_filter import SessionFilterConfig

FIVE_MIN_MS = 5 * 60 * 1000
FOUR_H_MS = 4 * 3600 * 1000

BULL_TOP, BULL_BOTTOM = 2429.0, 2418.0      # 5m bullish FVG zone
BULL_ENTRY = BULL_TOP                        # bullish entry is the zone top
BULL_SL = 2414.0
BULL_RISK = BULL_ENTRY - BULL_SL


def _candle(ts: int, o: float, h: float, low: float, cl: float):
    from strategy_extreme_fvg import Candle
    return Candle(timestamp=ts, open=o, high=h, low=low, close=cl, volume=10.0)


def _day_at(hour_utc: int, day_offset: int = 0) -> int:
    """Epoch ms for `hour_utc` UTC on a weekday, `day_offset` days from today."""
    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    day = base.timestamp() * 1000 + day_offset * 24 * 3600 * 1000
    dt = datetime.fromtimestamp(day / 1000, timezone.utc)
    # walk forward to a weekday (Mon-Fri)
    while dt.weekday() >= 5:
        dt = datetime.fromtimestamp(dt.timestamp() + 24 * 3600, timezone.utc)
    return int(dt.replace(hour=hour_utc).timestamp() * 1000)


def _bullish_fvg() -> FVG:
    """3-candle bullish FVG with the proven qa_harness geometry."""
    from strategy_extreme_fvg import Candle
    c1 = Candle(timestamp=0, open=2412.0, high=2418.0, low=2414.0, close=2416.0, volume=10.0)
    c2 = Candle(timestamp=FIVE_MIN_MS, open=2416.0, high=2427.0, low=2416.0, close=2426.0, volume=10.0)
    c3 = Candle(timestamp=2 * FIVE_MIN_MS, open=2426.0, high=2431.0, low=2429.0, close=2430.5, volume=10.0)
    return FVG(direction="Bullish", top=BULL_TOP, bottom=BULL_BOTTOM,
               c1=c1, c2=c2, c3=c3, formed_at=0, timeframe="5m")


def _anchor(first_touch: int) -> TouchedAnchor:
    fvg = _bullish_fvg()
    return TouchedAnchor(
        fvg=fvg,
        first_touch_timestamp=first_touch,
        most_recent_touch_timestamp=first_touch,
        is_currently_inside=False,
        touch_timeframe="5m",
    )


def _series(formation_ts: int, fill_ts: Optional[int]):
    """LTF series producing one bullish FVG at `formation_ts`.

    When `fill_ts` is given, a candle at that time trades down through entry so the
    lifecycle resolves to TRADE_ACTIVE with `entry_timestamp == fill_ts`.
    """
    candles: List[Any] = []
    # three candles forming the gap
    candles.append(_candle(formation_ts, 2412.0, 2418.0, 2414.0, 2416.0))
    candles.append(_candle(formation_ts + FIVE_MIN_MS, 2416.0, 2427.0, 2416.0, 2426.0))
    candles.append(_candle(formation_ts + 2 * FIVE_MIN_MS, 2426.0, 2431.0, BULL_TOP, 2430.5))
    if fill_ts is not None:
        # retrace to entry (2429) without breaching the stop (2414)
        candles.append(_candle(fill_ts, 2430.0, 2432.0, 2420.0, 2424.0))
    return candles


def _select(candles, anchor, session_cfg, **kw):
    best, survivors, rejects = select_gated_ltf_fvg(
        candles_ltf=candles,
        anchor=anchor,
        current_price=candles[-1].close,
        ltf_timeframe="5m",
        min_gap_pct=0.05,
        completion_target="2R",
        require_sweep=False,           # isolate the session axis
        gap_band=None,                 # isolate the session axis
        anchor_age_dead_zone=(-1.0, -1.0),   # never blocks
        entry_session_config=session_cfg,
        **kw,
    )
    return best, survivors, rejects


def test_pending_fvg_forming_out_of_session_survives():
    """The core defect: an FVG that FORMS outside the window must not be discarded
    just because its fill has not happened yet."""
    out_of_session = _day_at(3)      # 03:00 UTC -> outside NY_KZ (13-16 UTC)
    formation = out_of_session
    candles = _series(formation, fill_ts=None)
    anchor = _anchor(formation - FIVE_MIN_MS)

    cfg = SessionFilterConfig(entry_sessions="NY_KZ")
    best, survivors, rejects = _select(candles, anchor, cfg)

    assert rejects["ENTRY_SESSION"] == 0, (
        "candidate was rejected on FORMATION time; the session constrains the FILL")
    assert best is not None
    assert len(survivors) == 1


def test_active_candidate_filled_out_of_session_is_rejected():
    """If the fill already happened outside the window, reject it."""
    out_of_session = _day_at(3)
    formation = _day_at(14)          # forms in-session
    fill = out_of_session            # ...but fills out-of-session
    candles = _series(formation, fill_ts=fill)
    anchor = _anchor(formation - FIVE_MIN_MS)

    cfg = SessionFilterConfig(entry_sessions="NY_KZ")
    best, survivors, rejects = _select(candles, anchor, cfg)

    assert rejects["ENTRY_SESSION"] == 1
    assert best is None
    assert survivors == []


def test_active_candidate_filled_in_session_is_accepted():
    in_session = _day_at(14)
    formation = _day_at(3)           # forms OUTSIDE the window
    fill = in_session                 # ...fills INSIDE it
    candles = _series(formation, fill_ts=fill)
    anchor = _anchor(formation - FIVE_MIN_MS)

    cfg = SessionFilterConfig(entry_sessions="NY_KZ")
    best, survivors, rejects = _select(candles, anchor, cfg)

    assert rejects["ENTRY_SESSION"] == 0
    assert best is not None


def test_fill_probe_decides_gate_for_pending_candidate():
    """An explicit fill probe (backtest/replay path) must still be able to reject."""
    formation = _day_at(3)
    candles = _series(formation, fill_ts=None)
    anchor = _anchor(formation - FIVE_MIN_MS)

    cfg = SessionFilterConfig(entry_sessions="NY_KZ")
    best, _, rejects = _select(candles, anchor, cfg,
                               fill_probe_ts_ms=_day_at(3))     # probe out of session
    assert rejects["ENTRY_SESSION"] == 1
    assert best is None

    best2, _, rejects2 = _select(candles, anchor, cfg,
                                 fill_probe_ts_ms=_day_at(14))    # probe in session
    assert rejects2["ENTRY_SESSION"] == 0
    assert best2 is not None


def test_unconstrained_config_is_unaffected():
    """entry_sessions='ALL' must not reject anything, whatever the timestamps."""
    formation = _day_at(3)
    candles = _series(formation, fill_ts=None)
    anchor = _anchor(formation - FIVE_MIN_MS)

    best, survivors, rejects = _select(candles, anchor,
                                       SessionFilterConfig(entry_sessions="ALL"))
    assert rejects["ENTRY_SESSION"] == 0
    assert best is not None


def test_gate_agrees_with_tracker_semantics():
    """Sanity: an out-of-session FILL is what the tracker refuses, so the gate must
    not let an already-filled out-of-session candidate through as active."""
    formation = _day_at(14)
    fill = _day_at(3)
    candles = _series(formation, fill_ts=fill)
    anchor = _anchor(formation - FIVE_MIN_MS)

    cfg = SessionFilterConfig(entry_sessions="NY_KZ")
    best, survivors, _ = _select(candles, anchor, cfg)

    # Whatever survives must not include a candidate whose entry timestamp is
    # outside the window.
    for fvg in survivors:
        if fvg.lifecycle_state == "TRADE_ACTIVE" and fvg.entry_timestamp:
            assert cfg.is_entry_valid(fvg.entry_timestamp)
    assert best is None or best.lifecycle_state != "TRADE_ACTIVE"