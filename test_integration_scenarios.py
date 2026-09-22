"""
End-to-end integration tests — full pipeline scenarios through REAL code paths.

Unlike the unit suites, every test here drives actual orchestration code:
  - execute_extreme_screener_cycle()  (the exact function the daemon runs)
  - ExtremeTradeTracker.process_live_setups()  (state machine, persistence)
  - HTFFVGCache bootstrap/delta paths  (anchor discovery)
  - The FastAPI routers  (via TestClient, patch-surface resolvers included)
  - DashboardWSManager  (connect/broadcast/disconnect resilience)

All market data comes from a scripted FakeProvider (qa_live_sim's proven feed
geometry); all side effects (Telegram, dashboard WS, Redis dedup) land in
recording sinks. Nothing touches the network, the real ledger, or the real
Redis (root conftest.py + the pipeline fixture enforce this).

Scenario matrix
---------------
  L1  Formation -> fill -> 2R TP      (alert bodies, charts, dashboard, archive)
  L2  Formation -> fill -> SL         (-1.0R, STOP LOSS HIT body)
  L3  SL and TP on the same candle    (pessimistic: SL wins)
  L4  SL breach before entry          (invalidation: dashboard-only, no Telegram)
  L5  Anti-spam                       (rescans re-send nothing)
  M1  Two symbols, independent lifecycles in one scan
  P1  Ledger restart survival         (save -> fresh instance -> monitor continues)
  P2  Redis dedup across restart      (already-sent key suppresses repeat alert)
  P3  Pending expiry                  (setup absent N cycles -> INVALIDATED)
  S1  Entry outside allowed session   (fill ignored, stays PENDING_RETRACE)
  S2  Entry inside allowed session    (fills normally)
  C1  HTF cache bootstrap -> anchor + execution levels end to end
  C2  HTF cache delta path            (second call reuses cache, same anchor)
  A1  GET /api/extreme/scan           (includes the wkday_filter NameError fix)
  A2  GET /api/extreme/status
  A3  POST+GET /api/extreme/config    (round-trip)
  A4  POST /api/extreme/toggle-daemon
  A5  live-history + clear-live-history on a closed trade
  A6  GET /api/extreme/chart          (PNG smoke)
  A7  GET /api/extreme/backtest       (empty-feed smoke)
  W1  /ws/extreme-live initial_state contract + ping/pong (lifespan runs)
  W2  WS manager broadcast reaches a connected client
  W3  WS manager prunes dead clients without raising
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from qa_live_sim import (
    FakeProvider,
    SinkRecorder,
    c,
    configure_state,
    install_patches,
)
from extreme_trade_tracker import (
    PENDING_ABSENT_EXPIRY_CYCLES,
    extreme_trade_tracker,
)
from strategy_extreme_fvg import Candle, get_extreme_setup_for_symbol, htf_fvg_cache
from session_filter import SessionFilterConfig

FIVE_MIN_MS = 5 * 60 * 1000
FOUR_H_MS = 4 * 3600 * 1000

PATCH_KEYS = (
    "get_market_data_provider",
    "send_extreme_telegram_alert",
    "dashboard_ws_manager",
    "redis_client",
)


class RestartedSink(SinkRecorder):
    """Sink simulating a restart where Redis persists previously-sent alert keys."""

    def __init__(self, pres):
        super().__init__()
        self._pres = set(pres)

    async def is_alert_sent(self, symbol, evt_type, trade_id):
        return (symbol, evt_type, trade_id) in self._pres


class ClosableSink(SinkRecorder):
    """Sink that satisfies the lifespan-shutdown contract (await redis_client.close())."""

    async def close(self):
        pass


# --------------------------------------------------------------------------- #
# Fixtures & helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def pipeline():
    """Fresh fake-market pipeline wired to the REAL cycle for one test.

    Patches main's service attributes exactly like qa_harness/qa_live_sim do,
    configures runtime state for deterministic scanning, and restores both
    afterwards. Ledger/Redis isolation is provided by the root conftest.
    """
    import main
    import telegram_client
    from dashboard_ws import dashboard_ws_manager as real_ws_manager

    saved_attrs = {k: getattr(main, k) for k in PATCH_KEYS}
    saved_state = dict(main.state)
    # install_patches also mutates two module globals that nothing else restores:
    #   - telegram_client.is_telegram_thread_mode (replaced with a lambda)
    #   - dashboard_ws_manager.broadcast (the singleton IS main's attribute, so
    #     the sink recorder gets bound onto it and outlives `main`-attr restore)
    # Save/restore them here or every later test in the session inherits the fakes.
    saved_thread_mode = telegram_client.is_telegram_thread_mode
    saved_broadcast = real_ws_manager.broadcast

    provider = FakeProvider(["BTC", "ETH"])
    sink = ClosableSink()
    install_patches(provider, sink)
    configure_state(main, ["BTC"])          # single-symbol whitelist by default

    htf_fvg_cache.invalidate_cache()
    extreme_trade_tracker.active_trades = {}
    extreme_trade_tracker.history = []

    yield {"main": main, "provider": provider, "sink": sink}

    telegram_client.is_telegram_thread_mode = saved_thread_mode
    real_ws_manager.broadcast = saved_broadcast
    for k, v in saved_attrs.items():
        setattr(main, k, v)
    main.state.clear()
    main.state.update(saved_state)
    extreme_trade_tracker.active_trades = {}
    extreme_trade_tracker.history = []
    htf_fvg_cache.invalidate_cache()


async def run_scans(pipeline, feeds):
    """Appends scripted candles per step, then runs the real daemon cycle."""
    provider = pipeline["provider"]
    from main import execute_extreme_screener_cycle

    for feed in feeds:
        for sym, candles in feed.items():
            for cd in candles or []:
                provider.append_ltf(sym, cd["o"], cd["h"], cd["l"], cd["c"])
        await execute_extreme_screener_cycle()


def event_seq(sink):
    return [e for _, e, _ in sink.marked]


def trade_for(tracker, sym):
    return next((t for t in tracker.active_trades.values() if t.symbol == sym), None)


def history_for(tracker, sym):
    return [t for t in tracker.history if t.symbol == sym]


def dash_events(sink):
    return [m.get("event") for m in sink.dashboard if m.get("type") == "trade_event"]


def dash_kinds(sink):
    return [m.get("type") for m in sink.dashboard]


def feed_candles(provider, sym, tf):
    """The provider's scripted series as real Candle objects."""
    return [Candle.from_dict(x) for x in provider._feed[sym][tf]]


def find_slot(start_ts, hours):
    """First 5m timestamp at/after start_ts whose UTC hour is in `hours`."""
    ts = start_ts
    for _ in range(600):
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        if dt.hour in hours:
            return ts
        ts += FIVE_MIN_MS
    raise AssertionError("no matching 5m slot found")


# --------------------------------------------------------------------------- #
# L1-L5: Full lifecycle scenarios through the real cycle
# --------------------------------------------------------------------------- #
class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_formation_fill_tp_alerts_and_archive(self, pipeline):
        sink = pipeline["sink"]
        await run_scans(pipeline, [
            {},
            {"BTC": [c(0, 2430.0, 2430.5, 2426.5, 2429.5)]},   # retrace into entry
            {"BTC": [c(0, 2429.5, 2460.0, 2429.0, 2455.0)]},   # 2R wick
            {"BTC": [c(0, 2455.0, 2456.0, 2454.0, 2455.0)]},   # benign bar closes it
        ])

        assert event_seq(sink) == ["NEW_SETUP", "ENTRY_FILLED", "TP_HIT"]

        assert not extreme_trade_tracker.active_trades
        closed = history_for(extreme_trade_tracker, "BTC")
        assert len(closed) == 1
        t = closed[0]
        assert t.state == "COMPLETED_TP"
        assert t.realized_r == 2.0
        assert t.duration_min >= 1
        assert t.closed_at_ist

        assert len(sink.telegram) == 3
        assert all(kwargs.get("image_bytes") for _, kwargs in sink.telegram)
        bodies = [msg for msg, _ in sink.telegram]
        assert any("NEW SETUP" in b for b in bodies)
        assert any("IS NOW LIVE" in b for b in bodies)
        assert any("TARGET ACHIEVED" in b and "+2.0R" in b for b in bodies)

        assert dash_events(sink) == ["NEW_SETUP", "ENTRY_FILLED", "TP_HIT"]
        assert dash_kinds(sink).count("scan_complete") == 4
        assert {k[1] for k in sink.marked} <= {k[1] for k in sink.seen}

    @pytest.mark.asyncio
    async def test_formation_fill_sl(self, pipeline):
        sink = pipeline["sink"]
        await run_scans(pipeline, [
            {},
            {"BTC": [c(0, 2430.0, 2430.5, 2426.5, 2429.5)]},   # fill
            {"BTC": [c(0, 2428.0, 2430.0, 2412.0, 2415.0)]},   # SL wick
            {"BTC": [c(0, 2415.0, 2418.0, 2416.0, 2417.0)]},   # benign close bar
        ])

        assert event_seq(sink) == ["NEW_SETUP", "ENTRY_FILLED", "SL_HIT"]
        closed = history_for(extreme_trade_tracker, "BTC")
        assert len(closed) == 1 and closed[0].state == "STOPPED_OUT"
        assert closed[0].realized_r == -1.0
        assert any("STOP LOSS HIT" in msg and "-1.0R" in msg for msg, _ in sink.telegram)

    @pytest.mark.asyncio
    async def test_same_candle_sl_and_tp_is_pessimistic(self, pipeline):
        """A candle touching both SL and TP must resolve as a loss (SL checked first)."""
        sink = pipeline["sink"]
        await run_scans(pipeline, [
            {},
            {"BTC": [c(0, 2430.0, 2430.5, 2426.5, 2429.5)]},   # fill
            # Wicks both sides: low 2412 (< SL 2414) AND high 2461 (>= TP2 2459).
            {"BTC": [c(0, 2429.0, 2461.0, 2412.0, 2450.0)]},
            {"BTC": [c(0, 2450.0, 2451.0, 2449.0, 2450.0)]},   # benign close bar
        ])

        assert event_seq(sink) == ["NEW_SETUP", "ENTRY_FILLED", "SL_HIT"]
        closed = history_for(extreme_trade_tracker, "BTC")
        assert closed[0].state == "STOPPED_OUT" and closed[0].realized_r == -1.0
        assert not any("TARGET ACHIEVED" in msg for msg, _ in sink.telegram)

    @pytest.mark.asyncio
    async def test_invalidation_dashboard_only(self, pipeline):
        """SL breached before entry: invalidated, dashboard-notified, no Telegram alert."""
        sink = pipeline["sink"]
        await run_scans(pipeline, [
            {},
            {"BTC": [c(0, 2420.0, 2428.0, 2413.0, 2419.0)]},   # wick below SL, no fill
            {"BTC": [c(0, 2419.0, 2421.0, 2418.0, 2420.0)]},   # benign close bar
        ])

        # SETUP_INVALIDATED is dashboard-only BY DESIGN: its alert message is "",
        # so the dispatch loop never marks/sends it — the broadcast is the assertion
        # surface (same mechanism the qa_live_sim run verified).
        assert event_seq(sink) == ["NEW_SETUP"]
        assert dash_events(sink) == ["NEW_SETUP", "SETUP_INVALIDATED"]
        assert len(sink.telegram) == 1            # NEW_SETUP only, by design
        assert not extreme_trade_tracker.active_trades
        # Invalidations ARE archived to history like every resolved trade —
        # only the Telegram alert is suppressed (its alert message is "" by design).
        archived = history_for(extreme_trade_tracker, "BTC")
        assert len(archived) == 1 and archived[0].state == "INVALIDATED"
        assert archived[0].closed_timestamp is not None

    @pytest.mark.asyncio
    async def test_anti_spam_rescans_send_nothing_new(self, pipeline):
        from main import execute_extreme_screener_cycle

        await run_scans(pipeline, [
            {},
            {"BTC": [c(0, 2430.0, 2430.5, 2426.5, 2429.5)]},
            {"BTC": [c(0, 2429.5, 2460.0, 2429.0, 2455.0)]},
            # Close bar ends BELOW the displacement gap so the post-trade feed has
            # no residual unmitigated FVG — a leftover setup would legitimately
            # re-alert (dedup only suppresses repeats of the SAME trade).
            {"BTC": [c(0, 2455.0, 2456.0, 2420.0, 2428.0)]},
        ])
        assert len(pipeline["sink"].telegram) == 3
        events_before = list(pipeline["sink"].marked)

        for _ in range(3):
            await execute_extreme_screener_cycle()

        assert len(pipeline["sink"].telegram) == 3
        assert pipeline["sink"].marked == events_before


# --------------------------------------------------------------------------- #
# M1: Multi-symbol independence
# --------------------------------------------------------------------------- #
class TestMultiSymbol:
    @pytest.mark.asyncio
    async def test_two_symbols_independent_lifecycles(self, pipeline):
        main, sink = pipeline["main"], pipeline["sink"]
        main.state["coins_whitelist"] = "BTC,ETH"   # FakeProvider pre-seeds both feeds

        await run_scans(pipeline, [
            {},
            {"BTC": [c(0, 2430.0, 2430.5, 2426.5, 2429.5)]},   # BTC fills
            {"BTC": [c(0, 2429.5, 2460.0, 2429.0, 2455.0)]},   # BTC TP wick
            {"BTC": [c(0, 2455.0, 2456.0, 2454.0, 2455.0)]},   # BTC close bar
        ])

        assert event_seq(sink) == [
            "NEW_SETUP", "NEW_SETUP",          # scan 1: both symbols
            "ENTRY_FILLED",                     # scan 2: BTC only
            "TP_HIT",                           # scan 3: BTC only
        ]
        eth = trade_for(extreme_trade_tracker, "ETH")
        assert eth is not None and eth.state == "PENDING_RETRACE"
        assert history_for(extreme_trade_tracker, "BTC")[0].state == "COMPLETED_TP"


# --------------------------------------------------------------------------- #
# P1-P3: Persistence, restart dedup, pending expiry
# --------------------------------------------------------------------------- #
class TestPersistenceAndExpiry:
    @pytest.mark.asyncio
    async def test_ledger_survives_restart_and_monitor_continues(self, pipeline):
        from main import execute_extreme_screener_cycle

        await run_scans(pipeline, [
            {},
            {"BTC": [c(0, 2430.0, 2430.5, 2426.5, 2429.5)]},   # fill
        ])
        live = trade_for(extreme_trade_tracker, "BTC")
        assert live is not None and live.state == "TRADE_ACTIVE"
        extreme_trade_tracker._save()

        # Simulate restart: brand-new instance from the same (isolated) file.
        restarted = _restarted_tracker()
        restored = next(t for t in restarted.active_trades.values() if t.symbol == "BTC")
        assert restored.state == "TRADE_ACTIVE"
        assert restored.entry_price == live.entry_price
        assert restored.stop_loss == live.stop_loss
        assert restored.entry_timestamp == live.entry_timestamp

        # The restarted instance continues monitoring the position to completion.
        pipeline["provider"].append_ltf("BTC", 2429.5, 2460.0, 2429.0, 2455.0)
        candles = feed_candles(pipeline["provider"], "BTC", "5m")
        restarted.process_live_setups([], {}, recent_candles_map={"BTC": candles})

        closed = next(t for t in restarted.history if t.symbol == "BTC")
        assert closed.state == "COMPLETED_TP"
        assert closed.realized_r == 2.0

    @pytest.mark.asyncio
    async def test_redis_dedup_suppresses_repeat_alert_after_restart(self, pipeline):
        """A restart where Redis already marked TP_HIT must not re-send the alert."""
        await run_scans(pipeline, [
            {},
            {"BTC": [c(0, 2430.0, 2430.5, 2426.5, 2429.5)]},   # fill
        ])
        tid = trade_for(extreme_trade_tracker, "BTC").trade_id

        restarted_sink = RestartedSink(pres=[("BTC", "TP_HIT", tid)])
        main = pipeline["main"]
        main.send_extreme_telegram_alert = restarted_sink.send_telegram
        main.dashboard_ws_manager.broadcast = restarted_sink.broadcast
        main.redis_client = restarted_sink
        pipeline["sink"] = restarted_sink

        await run_scans(pipeline, [
            {"BTC": [c(0, 2429.5, 2460.0, 2429.0, 2455.0)]},   # TP wick
            {"BTC": [c(0, 2455.0, 2456.0, 2454.0, 2455.0)]},   # close bar
        ])

        # The trade still resolves in the ledger...
        closed = history_for(extreme_trade_tracker, "BTC")
        assert closed and closed[0].state == "COMPLETED_TP"
        # ...but the dedup key suppresses the repeat alert entirely.
        assert not any("TARGET ACHIEVED" in msg for msg, _ in restarted_sink.telegram)
        assert "TP_HIT" not in dash_events(restarted_sink)

    @pytest.mark.asyncio
    async def test_pending_setup_expires_after_absent_cycles(self, pipeline):
        """A pending setup absent from the scanner for N cycles auto-invalidates."""
        await run_scans(pipeline, [{}])
        pend = trade_for(extreme_trade_tracker, "BTC")
        assert pend is not None and pend.state == "PENDING_RETRACE"
        assert pend.absent_cycles == 0

        for _ in range(PENDING_ABSENT_EXPIRY_CYCLES + 1):
            extreme_trade_tracker.process_live_setups(
                [{"symbol": "OTHER", "direction": "Bullish", "state": "PENDING_RETRACE",
                  "entry_price": 100.0, "stop_loss": 95.0, "risk_r": 5.0, "risk_pct": 5.0,
                  "tp_1r": 105.0, "tp_2r": 110.0, "tp_3r": 115.0,
                  "anchor": {}, "target_fvg": {"formed_at": 0}}],
                {},
                recent_candles_map={},
            )
        assert pend.state == "INVALIDATED"
        assert "cycles" in pend.status_detail


def _restarted_tracker():
    """Fresh ExtremeTradeTracker from the (conftest-isolated) ledger file."""
    from extreme_trade_tracker import ExtremeTradeTracker
    return ExtremeTradeTracker(storage_path=str(extreme_trade_tracker.storage_path))


# --------------------------------------------------------------------------- #
# S1-S2: Entry-session gating through the real ingest path
# --------------------------------------------------------------------------- #
class TestSessionGatedEntries:
    def _enable_entry_filter(self, main):
        main.state["extreme_entry_session_filter"] = True
        main.state["extreme_entry_sessions"] = "NY"
        extreme_trade_tracker.update_session_config(
            SessionFilterConfig.from_legacy(entry_session_filter=True, entry_sessions="NY")
        )

    async def _place_fill_candle(self, pipeline, hours):
        """Moves the next 5m candle to a slot with the given UTC hours and fills entry."""
        from main import execute_extreme_screener_cycle

        formation_end = pipeline["provider"]._feed["BTC"]["5m"][-1]["t"]
        fill_ts = find_slot(formation_end + FIVE_MIN_MS, hours)
        pipeline["provider"]._feed["BTC"]["5m"].append(
            c(fill_ts, 2430.0, 2430.5, 2426.5, 2429.5)
        )
        await execute_extreme_screener_cycle()

    @pytest.mark.asyncio
    async def test_fill_outside_session_stays_pending(self, pipeline):
        """Entry outside NY session: fill ignored, NOT deferred — trade stays pending."""
        self._enable_entry_filter(pipeline["main"])
        await run_scans(pipeline, [{}])                            # formation
        await self._place_fill_candle(pipeline, hours={5, 6, 7})   # out-of-session attempt

        t = trade_for(extreme_trade_tracker, "BTC")
        assert t.state == "PENDING_RETRACE"
        assert t.entry_filled_at_ist is None
        assert not any("ENTRY_FILLED" in e for e in event_seq(pipeline["sink"]))

    @pytest.mark.asyncio
    async def test_fill_inside_session_activates(self, pipeline):
        """A fill whose timestamp IS in-session activates the trade normally."""
        self._enable_entry_filter(pipeline["main"])
        await run_scans(pipeline, [{}])                       # formation
        await self._place_fill_candle(pipeline, hours={14, 15, 16, 17})   # NY-hours fill

        t = trade_for(extreme_trade_tracker, "BTC")
        assert t.state == "TRADE_ACTIVE"
        assert t.entry_filled_at_ist is not None
        assert "ENTRY_FILLED" in event_seq(pipeline["sink"])


# --------------------------------------------------------------------------- #
# C1-C2: HTF cache integration (anchor discovery -> execution levels)
# --------------------------------------------------------------------------- #
class TestHTFCacheIntegration:
    @pytest.mark.asyncio
    async def test_bootstrap_to_setup_end_to_end(self, pipeline):
        """Cold cache + real candle feed -> anchor, extreme FVG, and execution levels."""
        setup = await get_extreme_setup_for_symbol(
            symbol="BTC", ltf_timeframe="5m",
            client=pipeline["provider"],
            candles_4h=feed_candles(pipeline["provider"], "BTC", "4h"),
            candles_ltf=feed_candles(pipeline["provider"], "BTC", "5m"),
        )
        assert setup is not None
        assert setup.direction == "Bullish"
        assert setup.entry_price == 2429.0
        assert setup.stop_loss == 2414.0
        assert setup.risk_r == 15.0
        assert setup.tp_2r == pytest.approx(2459.0)
        assert setup.anchor.fvg.bottom == 2402.0
        assert setup.anchor.fvg.top == 2415.0
        assert setup.anchor.first_touch_timestamp is not None

    @pytest.mark.asyncio
    async def test_delta_path_reuses_cache(self, pipeline):
        """Second call with one new candle goes through update_delta, same anchor."""
        c4 = feed_candles(pipeline["provider"], "BTC", "4h")
        first = await get_extreme_setup_for_symbol(
            symbol="BTC", ltf_timeframe="5m", client=pipeline["provider"],
            candles_4h=c4,
            candles_ltf=feed_candles(pipeline["provider"], "BTC", "5m"),
        )
        assert first is not None

        last = c4[-1].timestamp
        c4_new = c4 + [Candle(timestamp=last + FOUR_H_MS, open=2465.0, high=2470.0,
                              low=2440.0, close=2450.0, volume=10.0)]
        second = await get_extreme_setup_for_symbol(
            symbol="BTC", ltf_timeframe="5m", client=pipeline["provider"],
            candles_4h=c4_new,
            candles_ltf=feed_candles(pipeline["provider"], "BTC", "5m"),
        )
        assert second is not None
        assert (second.anchor.fvg.bottom, second.anchor.fvg.top) == (2402.0, 2415.0)


# --------------------------------------------------------------------------- #
# A1-A7: HTTP API integration (TestClient drives the REAL routers)
# --------------------------------------------------------------------------- #
class TestApiEndpoints:
    @pytest.fixture
    def client(self, pipeline):
        from main import app

        pipeline["main"].state["extreme_is_running"] = False   # keep daemon worker idle
        with TestClient(app) as c_:      # context manager runs lifespan
            yield c_, pipeline

    def test_scan_endpoint_returns_setups(self, client):
        c_, _pipeline = client
        resp = c_.get("/api/extreme/scan", params={"symbols": "BTC"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["count"] == 1
        s = data["setups"][0]
        assert s["symbol"] == "BTC"
        assert s["state"] == "PENDING_RETRACE"
        assert s["entry_price"] == 2429.0
        assert s["stop_loss"] == 2414.0
        assert s["anchor"]["direction"] == "Bullish"
        assert s["target_fvg"]["gap_pct"] > 0

    def test_status_endpoint(self, client):
        c_, _ = client
        resp = c_.get("/api/extreme/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        for key in ("is_running", "interval_seconds", "ltf_timeframe",
                    "completion_target", "setups", "total_cycles"):
            assert key in data

    def test_config_roundtrip(self, client):
        c_, _ = client
        get1 = c_.get("/api/extreme/config").json()["config"]
        assert get1["ltf_timeframe"] == "5m"
        post = c_.post("/api/extreme/config", params={"min_gap_pct": 0.08})
        assert post.status_code == 200
        assert post.json()["config"]["min_gap_pct"] == 0.08
        get2 = c_.get("/api/extreme/config").json()["config"]
        assert get2["min_gap_pct"] == 0.08

    def test_toggle_daemon(self, client):
        c_, _ = client
        r1 = c_.post("/api/extreme/toggle-daemon").json()
        r2 = c_.post("/api/extreme/toggle-daemon").json()
        assert r1["is_running"] != r2["is_running"]

    @pytest.mark.asyncio
    async def test_live_history_and_clear(self, client):
        c_, pipeline = client
        await run_scans(pipeline, [
            {},
            {"BTC": [c(0, 2430.0, 2430.5, 2426.5, 2429.5)]},
            {"BTC": [c(0, 2429.5, 2460.0, 2429.0, 2455.0)]},
            {"BTC": [c(0, 2455.0, 2456.0, 2420.0, 2428.0)]},
        ])
        resp = c_.get("/api/extreme/live-history")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("total", len(body.get("trades", []))) >= 1
        assert any(t["state"] == "COMPLETED_TP" for t in body["trades"])

        cleared = c_.post("/api/extreme/clear-live-history")
        assert cleared.status_code == 200
        after = c_.get("/api/extreme/live-history").json()
        assert after.get("total", len(after.get("trades", []))) == 0

    def test_chart_endpoint_png(self, client):
        c_, _ = client
        resp = c_.get("/api/extreme/chart", params={
            "symbol": "BTC", "direction": "Bullish", "ltf": "5m",
            "entry_price": 2429.0, "stop_loss": 2414.0,
            "tp_1r": 2444.0, "tp_2r": 2459.0, "tp_3r": 2474.0,
            "htf_bottom": 2402.0, "htf_top": 2415.0,
            "ltf_bottom": 2418.0, "ltf_top": 2429.0,
        })
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_backtest_endpoint_smoke(self, client):
        c_, _ = client
        resp = c_.get("/api/extreme/backtest", params={"symbol": "BTC", "days": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "total_trades" in data


# --------------------------------------------------------------------------- #
# W1-W3: WebSocket endpoint + manager
# --------------------------------------------------------------------------- #
class TestWebsocketIntegration:
    def test_ws_endpoint_initial_state_and_ping_pong(self, pipeline):
        from main import app

        pipeline["main"].state["extreme_is_running"] = False
        with TestClient(app) as client:
            with client.websocket_connect("/ws/extreme-live") as ws:
                initial = ws.receive_json()
                assert initial["type"] == "initial_state"
                for key in ("is_running", "interval_seconds", "last_scan_time_ist",
                            "total_cycles", "setups", "history_data"):
                    assert key in initial
                ws.send_text("ping")
                assert ws.receive_text() == "pong"

    def test_ws_manager_broadcast_reaches_client(self, pipeline):
        from main import app
        from dashboard_ws import DashboardWSManager, dashboard_ws_manager as real_manager

        pipeline["main"].state["extreme_is_running"] = False
        with TestClient(app) as client:
            with client.websocket_connect("/ws/extreme-live") as ws:
                ws.receive_json()   # consume initial_state
                # install_patches binds the sink recorder onto the singleton
                # (main.dashboard_ws_manager IS the singleton object) — restore
                # the real method, then broadcast on the app's loop where the
                # socket actually lives.
                real_manager.broadcast = DashboardWSManager.broadcast.__get__(real_manager)
                client.portal.call(real_manager.broadcast,
                                   {"type": "trade_event", "event": "TEST_EVENT", "trade": {}})
                pushed = ws.receive_json()
                assert pushed["type"] == "trade_event"
                assert pushed["event"] == "TEST_EVENT"

    @pytest.mark.asyncio
    async def test_ws_manager_prunes_dead_clients(self, pipeline):
        from dashboard_ws import DashboardWSManager

        mgr = DashboardWSManager()
        dead = MagicMock()
        dead.send_json = AsyncMock(side_effect=RuntimeError("client gone"))
        mgr.active_connections.add(dead)
        await mgr.broadcast({"type": "scan_complete", "setups": []})
        assert dead not in mgr.active_connections
