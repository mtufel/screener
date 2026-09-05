"""
Unit & Integration Tests for Extreme Live Trade Tracker & History Logging.
"""

import pytest
from fastapi.testclient import TestClient
from main import app
from extreme_trade_tracker import ExtremeTradeTracker, TrackedExtremeTrade


@pytest.fixture
def client():
    return TestClient(app)


def test_tracker_lifecycle_and_resolution(tmp_path):
    storage_file = tmp_path / "test_trades.json"
    tracker = ExtremeTradeTracker(storage_path=str(storage_file))

    # 1. Simulate new setup discovery
    mock_setup = {
        "symbol": "BTC",
        "direction": "Bullish",
        "state": "PENDING_RETRACE",
        "entry_price": 60000.0,
        "stop_loss": 59000.0,
        "risk_r": 1000.0,
        "risk_pct": 1.67,
        "tp_1r": 61000.0,
        "tp_2r": 62000.0,
        "tp_3r": 63000.0,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "15m",
        "anchor": {"bottom": 59500, "top": 60500, "formed_time_ist": "03-Sep 09:00 AM IST"},
        "target_fvg": {"bottom": 59800, "top": 60000, "formed_at": 1788000000, "gap_pct": 0.33},
    }

    mids = {"BTC": 60500.0}
    events = tracker.process_live_setups([mock_setup], mids)
    assert len(events) == 1
    assert events[0][0] == "NEW_SETUP"
    assert len(tracker.active_trades) == 1

    # 2. Simulate entry filled
    mock_setup["state"] = "TRADE_ACTIVE"
    mock_setup["entry_time_ist"] = "03-Sep 09:15 AM IST"
    events = tracker.process_live_setups([mock_setup], {"BTC": 60000.0})
    assert any(e[0] == "ENTRY_FILLED" for e in events)

    trade = list(tracker.active_trades.values())[0]
    assert trade.state == "TRADE_ACTIVE"
    assert trade.entry_filled_at_ist == "03-Sep 09:15 AM IST"

    # 3. Simulate price rally hitting 2R TP (62000)
    events = tracker.process_live_setups([mock_setup], {"BTC": 62100.0})
    assert any(e[0] == "TP_HIT" for e in events)
    assert len(tracker.active_trades) == 0
    assert len(tracker.history) == 1

    closed = tracker.history[0]
    assert closed.state == "COMPLETED_TP"
    assert closed.realized_r == 2.0
    assert closed.mfe_r >= 2.0

    # 4. Verify summary metrics
    summary = tracker.get_summary()
    assert summary["total_closed_trades"] == 1
    assert summary["wins"] == 1
    assert summary["win_rate_pct"] == 100.0
    assert summary["net_realized_r"] == 2.0


# =============================================================================
# Pending Monitor Scoping / Expiry / Refresh regression tests
# (change: extreme-pending-touch-scoping-and-expiry)
# =============================================================================
import extreme_trade_tracker as ett

F = 1788527700000  # FVG formed_at (candle open, 5m cadence)


def _mk_setup(symbol="PAXG", direction="Bullish", formed_at=F, state="PENDING_RETRACE"):
    entry = 4397.8
    risk = 13.3
    return {
        "symbol": symbol,
        "direction": direction,
        "state": state,
        "entry_price": entry,
        "stop_loss": entry - risk,
        "risk_r": risk,
        "risk_pct": 0.3,
        "tp_1r": entry + risk,
        "tp_2r": entry + 2 * risk,
        "tp_3r": entry + 3 * risk,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "5m",
        "anchor": {"direction": direction, "bottom": 4399.7, "top": 4424.0,
                   "formed_time_ist": "04-Sep 01:30 AM IST"},
        "target_fvg": {"direction": direction, "bottom": 4392.0, "top": entry,
                       "gap_pct": 0.13, "formed_at": formed_at},
    }


def _c(ts, o, h, l, c):
    return {"t": ts, "o": o, "h": h, "l": l, "c": c}


def test_pre_formation_candles_never_complete_pending_setup(tmp_path):
    """PAXG 2026-09-04 spam-loop replay: pre-formation candle crosses entry AND
    target, post-formation candles never touch entry -> must stay pending."""
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path / "t.json"))
    setup = _mk_setup()
    candles = [
        # pre-formation: low below entry AND high above 2R (would fake completion)
        _c(F - 900000, 4398.9, 4430.0, 4374.8, 4386.5),
        _c(F - 600000, 4386.2, 4392.0, 4383.8, 4392.0),
        _c(F, 4398.8, 4403.3, 4397.8, 4402.0),        # completing candle (wick = entry)
        # post-formation: rallies toward target but never re-touches entry
        _c(F + 300000, 4403.5, 4410.3, 4402.6, 4410.3),
        _c(F + 600000, 4410.4, 4420.0, 4410.3, 4416.2),
    ]
    events = tracker.process_live_setups([setup], {"PAXG": 4410.0}, recent_candles_map={"PAXG": candles})
    assert [e[0] for e in events] == ["NEW_SETUP"]
    # Repeat cycles (the spam amplifier): still no completion, no duplicates
    for _ in range(3):
        events = tracker.process_live_setups([setup], {"PAXG": 4410.0}, recent_candles_map={"PAXG": candles})
        assert events == []
    trade = tracker.get_pending_trade_for_symbol("PAXG")
    assert trade is not None and trade.state == "PENDING_RETRACE"
    assert len(tracker.history) == 0


def test_pre_formation_extremes_never_invalidate_pending_setup(tmp_path):
    """SOL-like: pre-formation candle breaches SL/anchor, post-formation respects both."""
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path / "t.json"))
    setup = _mk_setup(symbol="SOL")
    setup["entry_price"] = 99.024
    setup["stop_loss"] = 98.219
    setup["risk_r"] = 0.805
    setup["tp_1r"], setup["tp_2r"], setup["tp_3r"] = 99.829, 100.634, 101.439
    setup["anchor"] = {"direction": "Bullish", "bottom": 97.234, "top": 100.55}
    setup["target_fvg"] = {"direction": "Bullish", "bottom": 98.6, "top": 99.024, "formed_at": F}
    candles = [
        _c(F - 300000, 99.5, 99.6, 97.0, 97.5),  # pre-formation: breaches SL & anchor
        _c(F + 300000, 99.2, 99.5, 99.1, 99.3),  # post-formation: healthy (above entry 99.024)
    ]
    tracker.process_live_setups([setup], {"SOL": 99.3}, recent_candles_map={"SOL": candles})
    trade = tracker.get_pending_trade_for_symbol("SOL")
    assert trade is not None and trade.state == "PENDING_RETRACE"


def test_post_formation_fill_and_tp_completes_with_evidence_stats(tmp_path):
    """Real fill at candle 1, real 2R at candle 2 -> completes with candle-derived times."""
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path / "t.json"))
    setup = _mk_setup()
    candles = [
        _c(F + 300000, 4398.0, 4405.0, 4397.0, 4404.0),  # fill candle (low <= entry)
        _c(F + 600000, 4404.0, 4430.0, 4400.0, 4429.0),  # 2R hit candle (high >= 4424.45)
    ]
    events = tracker.process_live_setups([setup], {"PAXG": 4429.0}, recent_candles_map={"PAXG": candles})
    assert any(e[0] == "TP_HIT" for e in events)
    closed = tracker.history[0]
    assert closed.state == "COMPLETED_TP" and closed.realized_r == 2.0
    assert closed.entry_timestamp == F + 300000
    assert closed.closed_timestamp == F + 600000
    assert closed.duration_min == 5
    assert closed.mfe_r >= 2.4  # post-fill high 4430 -> (4430-4397.8)/13.3


def test_absent_pending_setup_expires_after_threshold(tmp_path, monkeypatch):
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path / "t.json"))
    monkeypatch.setattr(ett, "PENDING_ABSENT_EXPIRY_CYCLES", 3)
    setup = _mk_setup(symbol="SOL")
    tracker.process_live_setups([setup], {"SOL": 4400.5})
    # Two absent cycles: survives
    for _ in range(2):
        events = tracker.process_live_setups([], {"SOL": 4400.5})
        assert events == []
    assert tracker.get_pending_trade_for_symbol("SOL") is not None
    # Third consecutive absent cycle: expires
    events = tracker.process_live_setups([], {"SOL": 4400.5})
    assert any(e[0] == "SETUP_INVALIDATED" for e in events)
    expired = tracker.history[0]
    assert expired.state == "INVALIDATED"
    assert "absent from scanner" in expired.status_detail
    assert expired.absent_cycles == 3


def test_reoffered_setup_resets_absence_counter(tmp_path, monkeypatch):
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path / "t.json"))
    monkeypatch.setattr(ett, "PENDING_ABSENT_EXPIRY_CYCLES", 3)
    setup = _mk_setup(symbol="SOL")
    tracker.process_live_setups([setup], {"SOL": 4400.5})
    tracker.process_live_setups([], {"SOL": 4400.5})       # absent 1
    tracker.process_live_setups([setup], {"SOL": 4400.5})  # re-offered -> reset
    tracker.process_live_setups([], {"SOL": 4400.5})       # absent 1 again
    tracker.process_live_setups([], {"SOL": 4400.5})       # absent 2
    trade = tracker.get_pending_trade_for_symbol("SOL")
    assert trade is not None and trade.absent_cycles == 2


def test_daemon_restart_does_not_mass_expire(tmp_path):
    """Downtime never advances the counter: only executed cycles do."""
    storage = str(tmp_path / "t.json")
    tracker = ExtremeTradeTracker(storage_path=storage)
    tracker.process_live_setups([_mk_setup(symbol="BTC")], {"BTC": 78000.0})
    tracker._save()
    fresh = ExtremeTradeTracker(storage_path=storage)  # "restart"
    fresh.process_live_setups([], {"BTC": 78000.0})    # first cycle back
    trade = fresh.get_pending_trade_for_symbol("BTC")
    assert trade is not None and trade.absent_cycles == 1  # not 40, not expired


def test_fresher_emission_refreshes_stale_pending_record(tmp_path):
    """ETH-style stale anchor pairing: newer emission replaces entry/anchor/FVG in place."""
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path / "t.json"))
    stale = _mk_setup(symbol="ETH", formed_at=1788434100000)
    stale.update(entry_price=2399.3, stop_loss=2386.0, risk_r=13.3,
                 tp_1r=2412.6, tp_2r=2425.9, tp_3r=2439.2)
    stale["anchor"] = {"direction": "Bullish", "bottom": 1905.0, "top": 1910.0}
    tracker.process_live_setups([stale], {"ETH": 2430.0})
    old = tracker.get_pending_trade_for_symbol("ETH")
    assert old.entry_price == 2399.3

    fresh = _mk_setup(symbol="ETH", formed_at=1788530000000)
    fresh.update(entry_price=2410.0, stop_loss=2396.7, risk_r=13.3,
                 tp_1r=2423.3, tp_2r=2436.6, tp_3r=2449.9)
    fresh["anchor"] = {"direction": "Bullish", "bottom": 2400.0, "top": 2430.0}
    events = tracker.process_live_setups([fresh], {"ETH": 2430.0})
    assert events == []  # refresh is silent (no duplicate NEW_SETUP alert)
    refreshed = tracker.get_pending_trade_for_symbol("ETH")
    assert refreshed.trade_id == "ETH:1788530000000:2410.00"
    assert refreshed.entry_price == 2410.0
    assert refreshed.htf_anchor["bottom"] == 2400.0  # stale 19-Aug zone replaced
    assert len(tracker.active_trades) == 1  # no duplicate rows
    assert len(tracker.history) == 0


def test_trade_active_record_never_mutated_by_scanner_emission(tmp_path):
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path / "t.json"))
    active = _mk_setup(symbol="BTC", formed_at=1788433200000, state="TRADE_ACTIVE")
    active["entry_time_ist"] = "03-Sep 06:52 PM IST"
    active["entry_timestamp"] = 1788433500000
    tracker.process_live_setups([active], {"BTC": 4395.0})  # mid between SL and TP
    before = tracker.get_active_trade_for_symbol("BTC")
    assert before is not None and before.state == "TRADE_ACTIVE"

    different = _mk_setup(symbol="BTC", formed_at=1788530000000)  # newer pending setup
    tracker.process_live_setups([different], {"BTC": 4395.0})
    after = tracker.get_active_trade_for_symbol("BTC")
    assert after is before and after.entry_price == before.entry_price
    assert after.entry_timestamp == 1788433500000
    assert len(tracker.active_trades) == 1


def test_api_live_history_endpoints(client):
    res = client.get("/api/extreme/live-history")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "summary" in data
    assert "active_trades" in data
    assert "history" in data

    res_clear = client.post("/api/extreme/clear-live-history")
    assert res_clear.status_code == 200
    assert res_clear.json()["status"] == "success"


def test_pending_setup_invalidation_when_sl_breached(tmp_path):
    storage_file = tmp_path / "test_trades_inval.json"
    tracker = ExtremeTradeTracker(storage_path=str(storage_file))

    mock_setup = {
        "symbol": "BTC",
        "direction": "Bullish",
        "state": "PENDING_RETRACE",
        "entry_price": 60000.0,
        "stop_loss": 59000.0,
        "risk_r": 1000.0,
        "risk_pct": 1.67,
        "tp_1r": 61000.0,
        "tp_2r": 62000.0,
        "tp_3r": 63000.0,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "15m",
        "anchor": {"bottom": 59000, "top": 60500, "formed_time_ist": "03-Sep 09:00 AM IST"},
        "target_fvg": {"bottom": 59800, "top": 60000, "formed_at": 1788000000, "gap_pct": 0.33},
    }

    # Setup created as PENDING_RETRACE
    events = tracker.process_live_setups([mock_setup], {"BTC": 60500.0})
    assert len(tracker.active_trades) == 1

    # Price crashes below stop loss (58500 < 59000) without filling entry
    events = tracker.process_live_setups([], {"BTC": 58500.0})
    assert any(e[0] == "SETUP_INVALIDATED" for e in events)
    assert len(tracker.active_trades) == 0
    assert len(tracker.history) == 1
    assert tracker.history[0].state == "INVALIDATED"


def test_pending_setup_fills_and_completes_same_candle(tmp_path):
    """Regression: a PENDING_RETRACE trade whose FVG fills entry and reaches the
    completion target within one closed candle must resolve to COMPLETED_TP even
    though the scanner classifies the FVG as COMPLETED and no longer emits the setup."""
    storage_file = tmp_path / "test_trades_pending_tp.json"
    tracker = ExtremeTradeTracker(storage_path=str(storage_file))

    mock_setup = {
        "symbol": "BTC",
        "direction": "Bullish",
        "state": "PENDING_RETRACE",
        "entry_price": 100.25,
        "stop_loss": 100.05,
        "risk_r": 0.20,
        "risk_pct": 0.199,
        "tp_1r": 100.45,
        "tp_2r": 100.65,
        "tp_3r": 100.85,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "15m",
        "anchor": {"bottom": 100.05, "top": 100.60, "formed_time_ist": "04-Sep 09:00 AM IST"},
        "target_fvg": {"bottom": 100.05, "top": 100.25, "formed_at": 1788000000, "gap_pct": 0.20},
    }

    # Cycle 1: setup registered as PENDING_RETRACE (price above entry, no touch yet)
    events = tracker.process_live_setups([mock_setup], {"BTC": 100.30})
    assert len(tracker.active_trades) == 1
    assert any(e[0] == "NEW_SETUP" for e in events)

    # Cycle 2: FVG classified COMPLETED by the scanner -> NO setup emitted for BTC.
    # One closed candle touched entry (low 100.20 <= 100.25) and reached 2R (high 100.80 >= 100.65).
    fill_candle = {"t": 1788001800, "o": 100.40, "h": 100.80, "l": 100.20, "c": 100.70, "v": 1.0}
    events = tracker.process_live_setups([], {"BTC": 100.70}, recent_candles_map={"BTC": [fill_candle]})

    assert any(e[0] == "TP_HIT" for e in events)
    assert len(tracker.active_trades) == 0
    assert len(tracker.history) == 1
    resolved = tracker.history[0]
    assert resolved.state == "COMPLETED_TP"
    assert resolved.realized_r == 2.0
    assert resolved.entry_filled_at_ist is not None
    assert resolved.closed_at_ist is not None


def test_symbol_alias_resolution_for_live_mids(tmp_path):
    storage_file = tmp_path / "test_trades_alias.json"
    tracker = ExtremeTradeTracker(storage_path=str(storage_file))

    mock_xau_setup = {
        "symbol": "XAU",
        "direction": "Bullish",
        "state": "TRADE_ACTIVE",
        "entry_price": 2500.0,
        "stop_loss": 2480.0,
        "risk_r": 20.0,
        "risk_pct": 0.8,
        "tp_1r": 2520.0,
        "tp_2r": 2540.0,
        "tp_3r": 2560.0,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "15m",
        "anchor": {"bottom": 2470, "top": 2510, "formed_time_ist": "03-Sep 09:00 AM IST"},
        "target_fvg": {"bottom": 2490, "top": 2500, "formed_at": 1788000000, "gap_pct": 0.4},
        "entry_timestamp": 1788001000,
    }

    # Hyperliquid mids keyed by PAXG for XAU
    mids = {"PAXG": 2530.0}
    events = tracker.process_live_setups([mock_xau_setup], mids)
    trade = list(tracker.active_trades.values())[0]

    # floating_r should resolve PAXG mid-price (+1.5R) instead of staying frozen at 0.00R
    assert trade.floating_r == 1.5
    assert trade.max_favorable_price == 2530.0
    assert trade.mfe_r == 1.5


def test_earlier_sl_hit_never_overridden_by_later_tp_touch(tmp_path):
    """PAXG 2026-09-04 replay: trade stopped out at bar 1, later bar 50 touches TP.
    Must resolve as STOPPED_OUT on bar 1, NEVER as COMPLETED_TP."""
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path / "t.json"))
    entry_t = 1788443700000
    setup = {
        "symbol": "PAXG",
        "direction": "Bearish",
        "state": "TRADE_ACTIVE",
        "entry_price": 4466.30,
        "stop_loss": 4476.70,
        "risk_r": 10.40,
        "risk_pct": 0.23,
        "tp_1r": 4455.90,
        "tp_2r": 4445.50,
        "tp_3r": 4435.10,
        "floating_r": 0.0,
        "completion_target": "3R",
        "ltf_timeframe": "5m",
        "anchor": {"bottom": 4463.2, "top": 4517.2, "formed_time_ist": "29-Aug 05:30 AM IST"},
        "target_fvg": {"bottom": 4466.3, "top": 4470.2, "formed_at": 1788443400000, "gap_pct": 0.087},
        "entry_timestamp": entry_t,
        "entry_time_ist": "03-Sep 07:25 PM IST",
    }
    tracker.process_live_setups([setup], {"PAXG": 4466.30})
    assert len(tracker.active_trades) == 1

    # Batch of candles:
    # Bar 1 (20m post-entry): spikes to 4476.90 >= SL (4476.70) -> STOPPED OUT!
    # Bar 2 (25h post-entry): drops to 4430.00 <= TP3 (4435.10)
    c1 = {"t": entry_t + 1200000, "o": 4470.0, "h": 4476.90, "l": 4468.0, "c": 4475.0}
    c2 = {"t": entry_t + 90000000, "o": 4440.0, "h": 4442.0, "l": 4430.0, "c": 4432.0}

    events = tracker.process_live_setups([], {"PAXG": 4432.0}, recent_candles_map={"PAXG": [c1, c2]})
    assert any(e[0] == "SL_HIT" for e in events)
    assert not any(e[0] == "TP_HIT" for e in events)
    assert len(tracker.active_trades) == 0
    assert len(tracker.history) == 1
    closed = tracker.history[0]
    assert closed.state == "STOPPED_OUT"
    assert closed.realized_r == -1.0
    assert closed.closed_timestamp == entry_t + 1200000  # Bar 1 exit timestamp!
    assert closed.duration_min == 20


def test_bullish_earlier_sl_hit_never_overridden_by_later_tp_touch(tmp_path):
    """Bullish: Bar 1 breaches SL (95.0), Bar 2 touches TP (110.0). Must resolve STOPPED_OUT."""
    tracker = ExtremeTradeTracker(storage_path=str(tmp_path / "t.json"))
    entry_t = 1000000
    setup = {
        "symbol": "BTC",
        "direction": "Bullish",
        "state": "TRADE_ACTIVE",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "risk_r": 5.0,
        "risk_pct": 5.0,
        "tp_1r": 105.0,
        "tp_2r": 110.0,
        "tp_3r": 115.0,
        "floating_r": 0.0,
        "completion_target": "2R",
        "ltf_timeframe": "5m",
        "anchor": {"bottom": 90.0, "top": 105.0},
        "target_fvg": {"bottom": 98.0, "top": 100.0, "formed_at": 900000},
        "entry_timestamp": entry_t,
    }
    tracker.process_live_setups([setup], {"BTC": 100.0})
    c1 = {"t": entry_t + 300000, "o": 98.0, "h": 99.0, "l": 94.0, "c": 96.0}     # SL breach
    c2 = {"t": entry_t + 600000, "o": 108.0, "h": 112.0, "l": 107.0, "c": 111.0} # TP touch
    events = tracker.process_live_setups([], {"BTC": 111.0}, recent_candles_map={"BTC": [c1, c2]})
    assert any(e[0] == "SL_HIT" for e in events)
    assert not any(e[0] == "TP_HIT" for e in events)
    assert tracker.history[0].state == "STOPPED_OUT"
    assert tracker.history[0].realized_r == -1.0
    assert tracker.history[0].closed_timestamp == entry_t + 300000
