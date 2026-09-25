"""
Strategy-based TDD tests for Strategy 2 Step 6: the immutable trade ledger.

Rules under test (STRATEGIES.md Step 6 + openspec/specs/strategy-2-extreme):
- One cycle = process_live_setups: (1) ingest scanner setups -> NEW_SETUP /
  ENTRY_FILLED, (2) monitor open trades chronologically candle-by-candle from
  the LTF candle map -> SL_HIT / TP_HIT / SETUP_INVALIDATED, (3) archive
  resolved records to immutable history.
- PENDING_RETRACE: refresh to the newest formed_at emission, ignore older
  re-selections, invalidate on post-formation SL/anchor breach, expire after
  absent cycles, and NEVER resolve from pre-formation candles (PAXG spam
  regression).
- TRADE_ACTIVE: stop loss ALWAYS checked before take profit on a shared
  candle; exit timestamps/durations derive from candle evidence; active
  records are immutable against re-emitted scanner params.
- Events fire exactly once; resolved records leave active_trades.
"""

import pytest

import extreme_trade_tracker as ett
from extreme_trade_tracker import ExtremeTradeTracker

T0 = 1_788_527_700_000  # 5m-grid epoch (mirrors existing tracker tests)
FIVE_MIN_MS = 5 * 60 * 1000


def mk_c(ts, o, h, l, c):
    return {"t": ts, "o": o, "h": h, "l": l, "c": c}


def mk_setup(symbol="PAXG", direction="Bullish", formed_at=T0, state="PENDING_RETRACE",
             entry=4400.0, risk=12.0, anchor_bottom=4380.0, anchor_top=4440.0):
    """A scanner-shaped setup dict (same keys main.py emits)."""
    return {
        "symbol": symbol,
        "direction": direction,
        "state": state,
        "entry_price": entry,
        "stop_loss": entry - risk,
        "risk_r": risk,
        "risk_pct": round(risk / entry * 100, 2),
        "tp_1r": entry + risk,
        "tp_2r": entry + 2 * risk,
        "tp_3r": entry + 3 * risk,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "5m",
        "anchor": {"direction": direction, "bottom": anchor_bottom, "top": anchor_top,
                   "formed_time_ist": "04-Sep 01:30 AM IST"},
        "target_fvg": {"direction": direction, "bottom": entry, "top": entry + 2.0,
                       "gap_pct": 0.13, "formed_at": formed_at},
    }


def _tracker(tmp_path):
    return ExtremeTradeTracker(storage_path=str(tmp_path / "ledger.json"))


def _evtypes(events, symbol=None):
    return [e[0] for e in events if symbol is None or e[1].symbol == symbol]


def _get(tracker, symbol):
    for t in tracker.active_trades.values():
        if t.symbol == symbol:
            return t
    return None


# ==============================================================================
# Ingest gate: NEW_SETUP / ENTRY_FILLED / refresh semantics
# ==============================================================================

def test_ingest_creates_pending_record_and_emits_new_setup_once(tmp_path):
    tracker = _tracker(tmp_path)
    events = tracker.process_live_setups([mk_setup()], {"PAXG": 4400.0}, {})

    assert _evtypes(events) == ["NEW_SETUP"]
    trade = _get(tracker, "PAXG")
    assert trade.state == "PENDING_RETRACE"
    assert trade.entry_timestamp is None
    assert trade.floating_r == 0.0
    assert "Waiting for Retrace" in trade.status_detail


def test_reingesting_same_setup_stays_silent(tmp_path):
    """Anti-spam: the same emission processed again must not re-emit any event."""
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup()], {"PAXG": 4400.0}, {})
    events = tracker.process_live_setups([mk_setup()], {"PAXG": 4400.0}, {})
    assert _evtypes(events) == []
    assert len(tracker.active_trades) == 1


def test_pending_refreshes_to_newer_formed_at(tmp_path):
    """A newer emission refreshes the pending record in place (no duplicate rows)."""
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(formed_at=T0 + 10 * FIVE_MIN_MS)], {"PAXG": 4400.0}, {})

    # Older re-selection must be ignored
    events_old = tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})
    assert _evtypes(events_old) == []
    trade = _get(tracker, "PAXG")
    assert trade.ltf_fvg["formed_at"] == T0 + 10 * FIVE_MIN_MS

    # Newer emission refreshes in place
    events_new = tracker.process_live_setups(
        [mk_setup(formed_at=T0 + 20 * FIVE_MIN_MS)], {"PAXG": 4400.0}, {})
    assert _evtypes(events_new) == []  # refresh itself is silent
    trade = _get(tracker, "PAXG")
    assert trade.ltf_fvg["formed_at"] == T0 + 20 * FIVE_MIN_MS
    assert "refreshed" in trade.status_detail.lower()
    assert len([t for t in tracker.active_trades.values() if t.state == "PENDING_RETRACE"]) == 1


def test_ingest_pre_formation_candles_cannot_fill_or_resolve(tmp_path):
    """PAXG spam regression: candles at/before formed_at must not fill or resolve."""
    tracker = _tracker(tmp_path)
    setup = mk_setup(formed_at=T0 + 10 * FIVE_MIN_MS)
    candles = {"PAXG": [mk_c(T0 + 5 * FIVE_MIN_MS, 4405, 4420, 4380, 4415)]}  # ts < formed_at

    events = tracker.process_live_setups([setup], {"PAXG": 4400.0}, candles)

    assert "ENTRY_FILLED" not in _evtypes(events)
    assert "SL_HIT" not in _evtypes(events)
    trade = _get(tracker, "PAXG")
    assert trade.state == "PENDING_RETRACE"


def test_ingest_trade_active_state_creates_active_record(tmp_path):
    """Scanner-emitted TRADE_ACTIVE state registers directly as filled."""
    tracker = _tracker(tmp_path)
    setup = mk_setup(state="TRADE_ACTIVE")
    setup["entry_timestamp"] = T0
    setup["entry_time_ist"] = "04-Sep 01:30 AM IST"
    events = tracker.process_live_setups([setup], {"PAXG": 4400.0}, {})

    assert _evtypes(events) == ["NEW_SETUP", "ENTRY_FILLED"]
    trade = _get(tracker, "PAXG")
    assert trade.state == "TRADE_ACTIVE"
    assert trade.entry_timestamp is not None
    assert trade.entry_filled_at_ist == "04-Sep 01:30 AM IST"


def test_pending_becomes_active_when_scanner_reports_fill(tmp_path):
    """PENDING -> TRADE_ACTIVE transition via a re-emitted setup emits ENTRY_FILLED once."""
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup()], {"PAXG": 4400.0}, {})

    filled = mk_setup(state="TRADE_ACTIVE")
    filled["entry_timestamp"] = T0 + FIVE_MIN_MS
    events = tracker.process_live_setups([filled], {"PAXG": 4400.0}, {})
    assert _evtypes(events) == ["ENTRY_FILLED"]
    assert _get(tracker, "PAXG").state == "TRADE_ACTIVE"

    # Repeat must stay silent (already active)
    events2 = tracker.process_live_setups([filled], {"PAXG": 4400.0}, {})
    assert _evtypes(events2) == []


def test_active_symbol_locked_against_any_new_ingest(tmp_path):
    """A symbol with an ACTIVE trade ignores every further scanner emission."""
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(state="TRADE_ACTIVE")], {"PAXG": 4400.0}, {})
    before = _get(tracker, "PAXG")

    events = tracker.process_live_setups(
        [mk_setup(formed_at=T0 + 99 * FIVE_MIN_MS, risk=30.0)], {"PAXG": 4400.0}, {})

    assert _evtypes(events) == []
    after = _get(tracker, "PAXG")
    assert after is before
    assert after.stop_loss == pytest.approx(4388.0)  # params never mutated
    assert len(tracker.active_trades) == 1


# ==============================================================================
# PENDING monitoring: fills and invalidations from post-formation candles only
# ==============================================================================

def test_pending_fills_on_post_formation_entry_touch(tmp_path):
    """First post-formation candle touching entry -> ENTRY_FILLED, state TRADE_ACTIVE."""
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})

    touch = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4395, 4405, 4398, 4400)]}  # low <= 4400
    events = tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, touch)

    assert _evtypes(events) == ["ENTRY_FILLED"]
    trade = _get(tracker, "PAXG")
    assert trade.state == "TRADE_ACTIVE"
    assert trade.entry_timestamp == T0 + FIVE_MIN_MS


def test_pending_pre_formation_candles_never_fill_even_if_touching(tmp_path):
    """Candles at/before formed_at are filtered out of the fill scan entirely."""
    tracker = _tracker(tmp_path)
    setup = mk_setup(formed_at=T0 + 10 * FIVE_MIN_MS)
    candles = {"PAXG": [mk_c(T0 + 10 * FIVE_MIN_MS, 4395, 4405, 4398, 4400)]}  # ts == formed_at

    events = tracker.process_live_setups([setup], {"PAXG": 4400.0}, candles)
    assert "ENTRY_FILLED" not in _evtypes(events)
    assert _get(tracker, "PAXG").state == "PENDING_RETRACE"


def test_pending_fill_then_stop_on_same_candle(tmp_path):
    """Fill candle also breaching SL resolves immediately as SL_HIT (-1.0R)."""
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})

    nasty = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4410, 4412, 4385, 4390)]}  # touches entry AND SL
    events = tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, nasty)

    assert _evtypes(events) == ["SL_HIT"]
    h = tracker.history[0]
    assert h.state == "STOPPED_OUT" and h.realized_r == pytest.approx(-1.0)
    assert len(tracker.active_trades) == 0


def test_pending_fill_then_tp_on_same_candle(tmp_path):
    """Fill candle reaching the 2R target resolves immediately as TP_HIT (+2.0R)."""
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})

    moon = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4398, 4425, 4396, 4424)]}  # touches entry AND TP2
    events = tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, moon)

    assert _evtypes(events) == ["TP_HIT"]
    h = tracker.history[0]
    assert h.state == "COMPLETED_TP" and h.realized_r == pytest.approx(2.0)


def test_pending_invalidated_on_sl_breach_without_entry_touch(tmp_path):
    """Post-formation SL breach with high below entry -> SETUP_INVALIDATED, archived."""
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})

    breach = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4395, 4398, 4385, 4390)]}  # low<=4388, high<4400
    events = tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, breach)

    assert _evtypes(events) == ["SETUP_INVALIDATED"]
    assert len(tracker.active_trades) == 0
    h = tracker.history[0]
    assert h.state == "INVALIDATED"
    assert "Invalidated" in h.status_detail
    assert h.closed_timestamp == T0 + FIVE_MIN_MS


def test_pending_anchor_only_breach_does_not_invalidate(tmp_path):
    """Dipping below the anchor bottom WITHOUT breaching SL still fills on entry touch.

    The invalidation branch requires c_low <= stop_loss AND c_high < entry_price;
    an anchor-only dip falls through to the fill scan.
    """
    tracker = _tracker(tmp_path)
    # anchor bottom 4392 sits above SL 4388
    setup = mk_setup(formed_at=T0, anchor_bottom=4392)
    tracker.process_live_setups([setup], {"PAXG": 4400.0}, {})

    # low 4390: below anchor bottom (4392) but above SL (4388); touches entry 4400
    dip_touch = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4396, 4401, 4390, 4398)]}
    events = tracker.process_live_setups([setup], {"PAXG": 4400.0}, dip_touch)

    assert _evtypes(events) == ["ENTRY_FILLED"]
    trade = _get(tracker, "PAXG")
    assert trade.state == "TRADE_ACTIVE"
    assert trade.entry_timestamp == T0 + FIVE_MIN_MS


def test_pending_bearish_fill_and_invalidation_paths(tmp_path):
    """Bearish mirror: fill on high >= entry; invalidation on high >= SL with low > entry."""
    bear = mk_setup(direction="Bearish", formed_at=T0, entry=4400.0, risk=12.0)
    bear["stop_loss"] = 4400.0 + 12.0   # 4412
    bear["tp_1r"] = 4388.0
    bear["tp_2r"] = 4376.0
    bear["tp_3r"] = 4364.0

    # 1) invalidation: candle high 4413 >= SL 4412, low 4405 > entry 4400 (no touch).
    #    The ledger monitors in the same call it ingests, so both events fire together.
    tracker_b = _tracker(tmp_path)
    inval = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4405, 4413, 4404, 4410)]}
    events = tracker_b.process_live_setups([bear], {"PAXG": 4400.0}, inval)
    assert _evtypes(events) == ["NEW_SETUP", "SETUP_INVALIDATED"]
    assert len(tracker_b.active_trades) == 0
    assert tracker_b.history[0].state == "INVALIDATED"

    # 2) fill: separate ledger, candle high 4403 >= entry 4400
    tracker_c = _tracker(tmp_path)
    fill = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4402, 4403, 4390, 4395)]}
    events_fill = tracker_c.process_live_setups([bear], {"PAXG": 4400.0}, fill)
    assert _evtypes(events_fill) == ["NEW_SETUP", "ENTRY_FILLED"]
    assert _get(tracker_c, "PAXG").state == "TRADE_ACTIVE"


def test_pending_live_price_below_sl_invalidates_without_candles(tmp_path):
    """Live mid beyond SL with no candle data invalidates the pending setup."""
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})

    events = tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4380.0}, {})
    assert _evtypes(events) == ["SETUP_INVALIDATED"]
    assert len(tracker.active_trades) == 0


def test_pending_live_price_inside_zone_survives(tmp_path):
    """Live mid within anchor/above SL keeps the pending record alive."""
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})

    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4395.0}, {})
    assert _get(tracker, "PAXG") is not None
    assert _get(tracker, "PAXG").state == "PENDING_RETRACE"


def test_pending_absent_expiry_after_configured_cycles(tmp_path, monkeypatch):
    """Consecutive absent cycles expire the setup as INVALIDATED (never a loss)."""
    monkeypatch.setattr(ett, "PENDING_ABSENT_EXPIRY_CYCLES", 2)
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})

    # Cycle 1: setup present -> counter resets
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})
    assert _get(tracker, "PAXG").absent_cycles == 0

    # Cycles 2-3: setup absent -> counter climbs to threshold
    events2 = tracker.process_live_setups([], {"PAXG": 4400.0}, {})
    assert _evtypes(events2) == []
    assert _get(tracker, "PAXG").absent_cycles == 1

    events3 = tracker.process_live_setups([], {"PAXG": 4400.0}, {})
    assert _evtypes(events3) == ["SETUP_INVALIDATED"]
    assert len(tracker.active_trades) == 0
    h = tracker.history[0]
    assert h.state == "INVALIDATED"
    assert h.status_detail.startswith("Expired")


def test_pending_presence_resets_absent_counter(tmp_path, monkeypatch):
    """An interleaved cycle with the setup present resets the absent counter."""
    monkeypatch.setattr(ett, "PENDING_ABSENT_EXPIRY_CYCLES", 3)
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})

    tracker.process_live_setups([], {"PAXG": 4400.0}, {})                 # absent 1
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})  # present -> reset
    tracker.process_live_setups([], {"PAXG": 4400.0}, {})                 # absent 1 again

    trade = _get(tracker, "PAXG")
    assert trade is not None and trade.absent_cycles == 1


def test_active_trade_never_expires_absent(tmp_path, monkeypatch):
    """Absent-expiry only applies to PENDING records, never ACTIVE positions."""
    monkeypatch.setattr(ett, "PENDING_ABSENT_EXPIRY_CYCLES", 1)
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(state="TRADE_ACTIVE")], {"PAXG": 4400.0}, {})

    for _ in range(5):
        events = tracker.process_live_setups([], {"PAXG": 4405.0}, {})
        assert _evtypes(events) == []
    assert _get(tracker, "PAXG") is not None
    assert _get(tracker, "PAXG").state == "TRADE_ACTIVE"


# ==============================================================================
# ACTIVE monitoring: completion targets, SL-first precedence, immutability
# ==============================================================================

def _active_trade(tracker, tmp_path, completion_target="2R"):
    setup = mk_setup(state="TRADE_ACTIVE")
    setup["completion_target"] = completion_target
    setup["entry_timestamp"] = T0
    tracker.process_live_setups([setup], {"PAXG": 4400.0}, {})
    return _get(tracker, "PAXG")


def test_active_resolves_1r_target(tmp_path):
    """completion_target 1R: TP candle archives TP_HIT with realized_r=+1.0."""
    tracker = _tracker(tmp_path)
    _active_trade(tracker, tmp_path, completion_target="1R")

    tp1 = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4405, 4413, 4398, 4412)]}  # high >= 4412
    events = tracker.process_live_setups([], {"PAXG": 4405.0}, tp1)

    assert _evtypes(events) == ["TP_HIT"]
    h = tracker.history[0]
    assert h.state == "COMPLETED_TP" and h.realized_r == pytest.approx(1.0)
    assert h.closed_timestamp == T0 + FIVE_MIN_MS
    assert h.duration_min == 5
    assert len(tracker.active_trades) == 0


def test_active_resolves_2r_target_ignores_1r_poke(tmp_path):
    """completion_target 2R: a 1R poke keeps the position open; 2R completes."""
    tracker = _tracker(tmp_path)
    trade = _active_trade(tracker, tmp_path, completion_target="2R")
    assert trade.completion_target == "2R"

    poke = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4405, 4413, 4398, 4410)]}  # >= TP1 only
    tracker.process_live_setups([], {"PAXG": 4405.0}, poke)
    assert _get(tracker, "PAXG") is not None

    tp2 = {"PAXG": [mk_c(T0 + 2 * FIVE_MIN_MS, 4405, 4425, 4398, 4424)]}  # >= 4424
    events = tracker.process_live_setups([], {"PAXG": 4410.0}, tp2)
    assert _evtypes(events) == ["TP_HIT"]
    assert tracker.history[0].realized_r == pytest.approx(2.0)


def test_active_resolves_3r_target(tmp_path):
    """completion_target 3R: completes only on the 3R multiple."""
    tracker = _tracker(tmp_path)
    _active_trade(tracker, tmp_path, completion_target="3R")

    tp2 = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4405, 4425, 4398, 4420)]}
    tracker.process_live_setups([], {"PAXG": 4410.0}, tp2)
    assert _get(tracker, "PAXG") is not None

    tp3 = {"PAXG": [mk_c(T0 + 2 * FIVE_MIN_MS, 4405, 4437, 4398, 4436)]}  # >= 4436
    events = tracker.process_live_setups([], {"PAXG": 4420.0}, tp3)
    assert _evtypes(events) == ["TP_HIT"]
    assert tracker.history[0].realized_r == pytest.approx(3.0)


def test_sl_checked_before_tp_on_shared_candle(tmp_path):
    """A candle touching BOTH TP and SL must resolve as SL_HIT (-1.0R), never TP."""
    tracker = _tracker(tmp_path)
    _active_trade(tracker, tmp_path, completion_target="2R")

    both = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4405, 4426, 4385, 4400)]}  # high>=TP2, low<=SL
    events = tracker.process_live_setups([], {"PAXG": 4400.0}, both)

    assert _evtypes(events) == ["SL_HIT"]
    h = tracker.history[0]
    assert h.state == "STOPPED_OUT" and h.realized_r == pytest.approx(-1.0)


def test_active_sl_hit_carries_exit_evidence_and_duration(tmp_path):
    """SL_HIT derives closed_timestamp and duration_min from the exit candle."""
    tracker = _tracker(tmp_path)
    trade = _active_trade(tracker, tmp_path)
    trade.entry_timestamp = T0

    stop = {"PAXG": [mk_c(T0 + 3 * FIVE_MIN_MS, 4395, 4396, 4387, 4388)]}  # low <= 4388
    events = tracker.process_live_setups([], {"PAXG": 4390.0}, stop)

    assert _evtypes(events) == ["SL_HIT"]
    h = tracker.history[0]
    assert h.closed_timestamp == T0 + 3 * FIVE_MIN_MS
    assert h.duration_min == 15
    assert h.closed_at_ist.endswith("IST")


def test_bearish_active_sl_before_tp_mirror(tmp_path):
    """Bearish: SL (high >= SL) checked before TP (low <= target) on a shared candle."""
    tracker = _tracker(tmp_path)
    setup = mk_setup(direction="Bearish", state="TRADE_ACTIVE", entry=4400.0, risk=12.0)
    setup["stop_loss"] = 4412.0
    setup["tp_1r"] = 4388.0
    setup["tp_2r"] = 4376.0
    setup["tp_3r"] = 4364.0
    setup["entry_timestamp"] = T0
    tracker.process_live_setups([setup], {"PAXG": 4400.0}, {})

    both = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4395, 4413, 4375, 4390)]}  # low<=TP2, high>=SL
    events = tracker.process_live_setups([], {"PAXG": 4400.0}, both)

    assert _evtypes(events) == ["SL_HIT"]
    assert tracker.history[0].realized_r == pytest.approx(-1.0)


def test_bearish_active_tp_hit(tmp_path):
    """Bearish 2R: candle low <= 4376 completes at +2.0R."""
    tracker = _tracker(tmp_path)
    setup = mk_setup(direction="Bearish", state="TRADE_ACTIVE", entry=4400.0, risk=12.0)
    setup["stop_loss"] = 4412.0
    setup["tp_1r"] = 4388.0
    setup["tp_2r"] = 4376.0
    setup["tp_3r"] = 4364.0
    setup["entry_timestamp"] = T0
    tracker.process_live_setups([setup], {"PAXG": 4400.0}, {})

    tp = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4395, 4399, 4375, 4377)]}
    events = tracker.process_live_setups([], {"PAXG": 4380.0}, tp)
    assert _evtypes(events) == ["TP_HIT"]
    assert tracker.history[0].realized_r == pytest.approx(2.0)


def test_active_live_mid_resolves_tp_without_candles(tmp_path):
    """Live-mid fallback: mid >= target completes; mid <= SL would stop out first."""
    tracker = _tracker(tmp_path)
    _active_trade(tracker, tmp_path, completion_target="1R")

    events = tracker.process_live_setups([], {"PAXG": 4415.0}, {})
    assert _evtypes(events) == ["TP_HIT"]
    assert tracker.history[0].realized_r == pytest.approx(1.0)


def test_active_live_mid_sl_takes_precedence_over_tp(tmp_path):
    """Live-mid beyond SL resolves as SL_HIT even when a TP was also crossed earlier."""
    tracker = _tracker(tmp_path)
    _active_trade(tracker, tmp_path, completion_target="2R")

    events = tracker.process_live_setups([], {"PAXG": 4380.0}, {})
    assert _evtypes(events) == ["SL_HIT"]
    assert tracker.history[0].state == "STOPPED_OUT"


def test_active_record_immutable_against_reemitted_params(tmp_path):
    """Re-emitted scanner setups NEVER mutate an ACTIVE trade's locked parameters."""
    tracker = _tracker(tmp_path)
    trade = _active_trade(tracker, tmp_path)
    locked = (trade.entry_price, trade.stop_loss, trade.risk_r, trade.tp_2r,
              trade.completion_target, trade.entry_timestamp)

    mutated = mk_setup(formed_at=T0 + 50 * FIVE_MIN_MS, risk=30.0)
    mutated["completion_target"] = "3R"
    events = tracker.process_live_setups([mutated], {"PAXG": 4405.0}, {})

    assert _evtypes(events) == []
    now = _get(tracker, "PAXG")
    assert now is trade
    assert (now.entry_price, now.stop_loss, now.risk_r, now.tp_2r,
            now.completion_target, now.entry_timestamp) == locked


def test_active_mfe_r_tracks_high_water_mark(tmp_path):
    """mfe_r keeps the max favorable excursion across monitored candles."""
    tracker = _tracker(tmp_path)
    trade = _active_trade(tracker, tmp_path)
    assert trade.mfe_r == 0.0

    poke = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4405, 4409, 4398, 4404)]}  # +0.75R high
    tracker.process_live_setups([], {"PAXG": 4403.0}, poke)
    trade = _get(tracker, "PAXG")
    assert trade.mfe_r == pytest.approx(0.75)

    pullback = {"PAXG": [mk_c(T0 + 2 * FIVE_MIN_MS, 4400, 4402, 4395, 4398)]}
    tracker.process_live_setups([], {"PAXG": 4398.0}, pullback)
    trade = _get(tracker, "PAXG")
    assert trade.mfe_r == pytest.approx(0.75)  # high-water mark never regresses
    assert trade.floating_r == pytest.approx(-2 / 12, abs=0.01)


def test_chronological_multi_candle_processing_stops_at_first_exit(tmp_path):
    """Candles are processed in order; the first exit candle ends the trade."""
    tracker = _tracker(tmp_path)
    _active_trade(tracker, tmp_path, completion_target="1R")

    candles = {"PAXG": [
        mk_c(T0 + FIVE_MIN_MS, 4405, 4407, 4398, 4403),   # no exit
        mk_c(T0 + 2 * FIVE_MIN_MS, 4403, 4404, 4380, 4390),  # SL
        mk_c(T0 + 3 * FIVE_MIN_MS, 4390, 4420, 4390, 4418),  # would be TP if reached
    ]}
    events = tracker.process_live_setups([], {"PAXG": 4395.0}, candles)

    assert _evtypes(events) == ["SL_HIT"]
    assert tracker.history[0].closed_timestamp == T0 + 2 * FIVE_MIN_MS


# ==============================================================================
# History archiving + summary stats
# ==============================================================================

def test_history_record_is_frozen_copy_of_resolved_trade(tmp_path):
    """Resolved records move to history and leave active_trades entirely."""
    tracker = _tracker(tmp_path)
    _active_trade(tracker, tmp_path, completion_target="1R")

    tp = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4405, 4413, 4398, 4412)]}
    tracker.process_live_setups([], {"PAXG": 4405.0}, tp)

    assert len(tracker.history) == 1
    assert len(tracker.active_trades) == 0
    h = tracker.history[0]
    assert h.state == "COMPLETED_TP" and h.realized_r == pytest.approx(1.0)
    # history records are full TrackedExtremeTrade objects (persisted verbatim)
    assert h.symbol == "PAXG"

    # New cycle with a fresh emission must NOT resurrect the archived trade_id row
    events = tracker.process_live_setups([mk_setup()], {"PAXG": 4400.0}, {})
    assert _evtypes(events) == ["NEW_SETUP"]
    assert len(tracker.history) == 1  # history untouched


def test_invalidated_setup_archived_with_reason_string(tmp_path):
    """Invalidation archives the record with its reason preserved in status_detail."""
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})
    breach = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4395, 4398, 4385, 4390)]}
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, breach)

    h = tracker.history[0]
    assert h.state == "INVALIDATED"
    assert "Invalidated" in h.status_detail


def test_expired_setup_never_counts_as_loss(tmp_path, monkeypatch):
    """Expired pendings archive as INVALIDATED, keeping win-rate math honest."""
    monkeypatch.setattr(ett, "PENDING_ABSENT_EXPIRY_CYCLES", 1)
    tracker = _tracker(tmp_path)
    tracker.process_live_setups([mk_setup(formed_at=T0)], {"PAXG": 4400.0}, {})

    tracker.process_live_setups([], {"PAXG": 4400.0}, {})  # absent -> expire
    assert len(tracker.history) == 1 and len(tracker.active_trades) == 0
    assert tracker.history[0].state == "INVALIDATED"


def test_get_summary_counts_only_real_resolutions(tmp_path):
    """Summary win-rate/net_r include TP/SL only; pending/active tracked separately."""
    tracker = _tracker(tmp_path)
    active = mk_setup(state="TRADE_ACTIVE")
    active["entry_timestamp"] = T0
    active["completion_target"] = "1R"
    pending = mk_setup(symbol="DOGE", formed_at=T0 + 50 * FIVE_MIN_MS)
    tracker.process_live_setups([active, pending],
                                {"PAXG": 4400.0, "DOGE": 4395.0}, {})

    tp = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4405, 4413, 4398, 4412)]}
    tracker.process_live_setups([], {"PAXG": 4405.0, "DOGE": 4395.0}, tp)

    s = tracker.get_summary()
    assert s["wins"] == 1 and s["losses"] == 0
    assert s["win_rate_pct"] == 100.0
    assert s["net_realized_r"] == pytest.approx(1.0)
    assert s["active_now"] == 0
    assert s["pending_now"] == 1
    assert s["total_closed_trades"] == 1


def test_persistence_roundtrip_preserves_ledger_state(tmp_path):
    """Ledger state survives a reload from disk (active + history)."""
    tracker = _tracker(tmp_path)
    _active_trade(tracker, tmp_path, completion_target="1R")
    tracker.process_live_setups([mk_setup(formed_at=T0 + 50 * FIVE_MIN_MS)], {"PAXG": 4400.0}, {})

    tp = {"PAXG": [mk_c(T0 + FIVE_MIN_MS, 4405, 4413, 4398, 4412)]}
    tracker.process_live_setups([], {"PAXG": 4405.0}, tp)
    n_active, n_hist = len(tracker.active_trades), len(tracker.history)

    reloaded = ExtremeTradeTracker(storage_path=str(tmp_path / "ledger.json"))
    assert len(reloaded.active_trades) == n_active
    assert len(reloaded.history) == n_hist
    h = reloaded.history[0]
    assert h.state == "COMPLETED_TP" and h.realized_r == pytest.approx(1.0)
    assert h.symbol == "PAXG"


def test_no_ledger_file_loads_empty(tmp_path):
    """A fresh storage path starts with an empty ledger."""
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path / "missing.json"))
    assert tracker.active_trades == {} and tracker.history == []


def test_pending_live_anchor_breach_invalidates_without_sl_touch(tmp_path):
    """Live mid outside the anchor zone (but above SL) invalidates a pending setup.

    Uses entry below the anchor bottom — the only geometry where the live
    anchor-breach branch is reachable (px between entry and zone avoids the
    fill branch, which always wins for entry inside/above the zone).
    """
    tracker = _tracker(tmp_path)
    setup = mk_setup(formed_at=T0, entry=4386.0, risk=12.0, anchor_bottom=4392.0)
    setup["stop_loss"] = 4374.0
    tracker.process_live_setups([setup], {"PAXG": 4394.0}, {})
    assert _get(tracker, "PAXG").state == "PENDING_RETRACE"  # control: inside zone, above entry

    events = tracker.process_live_setups(
        [mk_setup(formed_at=T0, entry=4386.0, risk=12.0, anchor_bottom=4392.0)],
        {"PAXG": 4390.0}, {})  # 4390 > SL 4374, > entry 4386, < zone bottom 4392

    assert _evtypes(events) == ["SETUP_INVALIDATED"]
    assert len(tracker.active_trades) == 0
    assert tracker.history[0].state == "INVALIDATED"


def test_opposite_direction_pendings_coexist_across_symbols(tmp_path):
    """A bullish and a bearish pending on different symbols track independently."""
    tracker = _tracker(tmp_path)
    bull = mk_setup(symbol="PAXG", direction="Bullish", formed_at=T0, entry=4400.0, risk=12.0)
    bear = mk_setup(symbol="DOGE", direction="Bearish", formed_at=T0, entry=100.0, risk=3.0)
    bear["stop_loss"] = 103.0
    bear["tp_1r"], bear["tp_2r"], bear["tp_3r"] = 97.0, 94.0, 91.0

    tracker.process_live_setups([bull, bear], {"PAXG": 4400.0, "DOGE": 100.0}, {})

    assert len(tracker.active_trades) == 2
    assert _get(tracker, "PAXG").direction == "Bullish"
    assert _get(tracker, "DOGE").direction == "Bearish"

    # Both survive a monitoring cycle with harmless prices
    tracker.process_live_setups([], {"PAXG": 4405.0, "DOGE": 100.5}, {})
    assert _get(tracker, "PAXG").state == "PENDING_RETRACE"
    assert _get(tracker, "DOGE").state == "PENDING_RETRACE"
