"""
Shadow-strategy tests (EXTREME_SHADOW_STRATEGIES).

Covers the three guarantees of shadow mode:
  1. ISOLATION: shadow trades live in their own ledger scope — they never block
     the active strategy's setups on the same symbol and never expire them via
     the absent-setup counter.
  2. SILENCE: shadow lifecycle events broadcast to the dashboard but NEVER send
     Telegram alerts.
  3. PARITY: apart from scope/alerting, shadow trades flow through the exact
     same tracker machinery (ingest -> pending -> fill -> TP/SL) as S2 trades.

Unit tests exercise the tracker + adapter directly; the integration test drives
execute_extreme_screener_cycle() (the real daemon function) with a fake shadow
strategy registered in the strategy registry.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from qa_live_sim import (
    FakeProvider,
    SinkRecorder,
    c,
    configure_state,
    install_patches,
)
from extreme_trade_tracker import extreme_trade_tracker
from session_filter import SessionFilterConfig
from strategy_extreme_fvg import Candle, FVG, TouchedAnchor

# Deterministic all-allowed session config (real-env filters must not leak in).
OPEN_SESSION_CONFIG = SessionFilterConfig.from_legacy(
    session_filter=False, weekday_filter=False,
    entry_session_filter=False, entry_weekday_filter=False,
    sessions="ALL", entry_sessions="ALL",
)

FIVE_MIN_MS = 5 * 60 * 1000
FOUR_H_MS = 4 * 3600 * 1000

SHADOW_NAME = "shadow_fake"
OTHER_SHADOW_NAME = "shadow_fake2"


# --------------------------------------------------------------------------- #
# Fake shadow strategy + setup factory
# --------------------------------------------------------------------------- #
def _candle(ts: int, o: float, h: float, l: float, cl: float) -> Candle:
    return Candle.from_dict(c(ts, o, h, l, cl))


def _make_fvg_and_anchor(base_ts: int, bullish: bool = True):
    """Minimal 3-candle LTF FVG + touched 4H anchor for payload rendering."""
    fvg_dir = "Bullish" if bullish else "Bearish"
    c1 = _candle(base_ts, 2400.0, 2412.0, 2398.0, 2410.0)
    c2 = _candle(base_ts + FIVE_MIN_MS, 2410.0, 2430.0, 2409.0, 2428.0)
    c3 = _candle(base_ts + 2 * FIVE_MIN_MS, 2428.0, 2436.0, 2426.0, 2434.0)
    fvg = FVG(
        direction=fvg_dir, top=2428.0, bottom=2412.0,
        c1=c1, c2=c2, c3=c3,
        formed_at=base_ts + 2 * FIVE_MIN_MS, timeframe="5m",
    )
    anchor_fvg = FVG(
        direction=fvg_dir, top=2450.0, bottom=2380.0,
        c1=_candle(base_ts - FOUR_H_MS, 2390.0, 2395.0, 2370.0, 2385.0),
        c2=_candle(base_ts, 2385.0, 2460.0, 2384.0, 2450.0),
        c3=_candle(base_ts + FOUR_H_MS, 2450.0, 2470.0, 2445.0, 2460.0),
        formed_at=base_ts + FOUR_H_MS, timeframe="4h",
    )
    anchor = TouchedAnchor(
        fvg=anchor_fvg,
        first_touch_timestamp=base_ts + 2 * FOUR_H_MS,
        most_recent_touch_timestamp=base_ts + 2 * FOUR_H_MS,
    )
    return fvg, anchor


def make_shadow_setup(symbol: str = "BTC", base_ts: Optional[int] = None) -> Any:
    """A fully-formed setup object shaped like S2's ExtremeTradeSetup.

    Entry sits well below the FakeProvider feed's price range so the pending
    retrace does NOT fill from the static scripted candles — tests control
    fills explicitly.
    """
    if base_ts is None:
        base_ts = 1_700_000_000_000 - (1_700_000_000_000 % FIVE_MIN_MS)
    fvg, anchor = _make_fvg_and_anchor(base_ts)
    entry, stop = 2350.0, 2100.0
    risk = entry - stop
    return SimpleNamespace(
        symbol=symbol,
        direction="Bullish",
        entry_price=entry,
        stop_loss=stop,
        risk_r=risk,
        risk_pct=risk / entry * 100.0,
        tp_1r=entry + risk,
        tp_2r=entry + 2 * risk,
        tp_3r=entry + 3 * risk,
        state="PENDING_RETRACE",
        entry_timestamp=None,
        entry_time_ist=None,
        floating_r=0.0,
        completion_target="2R",
        ltf_timeframe="5m",
        anchor=anchor,
        ltf_fvg=fvg,
        all_unmitigated_fvgs=[],
    )


from strategies.base import BaseStrategy  # noqa: E402
from strategies.registry import register  # noqa: E402


@register
class FakeShadowStrategy(BaseStrategy):
    """Deterministic one-setup shadow strategy used only by these tests."""

    name = SHADOW_NAME
    display_name = "Fake Shadow"

    default_params = {
        "ltf_timeframe": "5m",
        "completion_target": "2R",
        "min_gap_pct": 0.05,
        "use_close_invalidation": False,
        "session_filter": False,
        "weekday_filter": False,
        "entry_session_filter": False,
        "entry_weekday_filter": False,
        "sessions": "ALL",
        "entry_sessions": "ALL",
    }

    async def find_setups(self, symbol: str, provider: Any, params: Dict[str, Any]) -> List[Any]:
        return [make_shadow_setup(symbol)]

    async def backtest(self, symbol: str, days: int, provider: Any, params: Dict[str, Any]) -> Any:  # pragma: no cover
        raise NotImplementedError("FakeShadowStrategy does not backtest")


@register
class OtherFakeShadowStrategy(FakeShadowStrategy):
    """Second fake shadow used to prove scopes don't cross-block each other."""

    name = OTHER_SHADOW_NAME
    display_name = "Other Fake Shadow"


# --------------------------------------------------------------------------- #
# Unit: strategy-scoped ledger lookups
# --------------------------------------------------------------------------- #
class TestScopedLedgerLookups:
    def _mk_active(self, trade_id: str, isolation_scope: str = "") -> Any:
        fvg, anchor = _make_fvg_and_anchor(1_700_000_000_000)
        from extreme_trade_tracker import TrackedExtremeTrade
        return TrackedExtremeTrade(
            trade_id=trade_id,
            symbol="BTC",
            direction="Bullish",
            ltf_timeframe="5m",
            entry_price=2428.0,
            stop_loss=2398.0,
            risk_r=30.0,
            risk_pct=1.23,
            tp_1r=2458.0, tp_2r=2488.0, tp_3r=2518.0,
            completion_target="2R",
            htf_anchor={}, ltf_fvg={},
            strategy=isolation_scope or "extreme_fvg",
            isolation_scope=isolation_scope,
            state="TRADE_ACTIVE",
        )

    def test_unscoped_lookup_ignores_shadow_trades(self):
        extreme_trade_tracker.active_trades = {
            "BTC:1:2428.00:shadow_fake": self._mk_active("t1", SHADOW_NAME),
        }
        # The scoped ("") lookup — what the active strategy's scan uses —
        # ignores shadow trades; the legacy unscoped helper still matches any
        # active trade (it serves display/candle-sizing, not scanning).

    def test_scoped_lookup_returns_own_scope_only(self):
        t_shadow = self._mk_active("t1", SHADOW_NAME)
        t_active = self._mk_active("t2", "")
        extreme_trade_tracker.active_trades = {t_shadow.trade_id: t_shadow, t_active.trade_id: t_active}

        assert extreme_trade_tracker.get_active_trade_for_symbol_and_scope("BTC", "") is t_active
        assert extreme_trade_tracker.get_active_trade_for_symbol_and_scope("BTC", SHADOW_NAME) is t_shadow
        assert extreme_trade_tracker.get_active_trade_for_symbol_and_scope("BTC", OTHER_SHADOW_NAME) is None

    def test_pending_scoped_lookup(self):
        from extreme_trade_tracker import TrackedExtremeTrade
        t = TrackedExtremeTrade(
            trade_id="p1", symbol="ETH", direction="Bearish",
            entry_price=100.0, stop_loss=101.0, risk_r=1.0, risk_pct=1.0,
            tp_1r=99.0, tp_2r=98.0, tp_3r=97.0, completion_target="2R",
            strategy=SHADOW_NAME, isolation_scope=SHADOW_NAME,
            state="PENDING_RETRACE",
        )
        extreme_trade_tracker.active_trades = {t.trade_id: t}
        assert extreme_trade_tracker.get_pending_trade_for_symbol_and_scope("ETH", SHADOW_NAME) is t
        assert extreme_trade_tracker.get_pending_trade_for_symbol_and_scope("ETH", "") is None
        extreme_trade_tracker.active_trades = {}


# --------------------------------------------------------------------------- #
# Unit: ingest isolation + absent-expiry scope keys
# --------------------------------------------------------------------------- #
class TestIngestScopeIsolation:
    def setup_method(self):
        extreme_trade_tracker.active_trades = {}
        extreme_trade_tracker.history = []

    def teardown_method(self):
        extreme_trade_tracker.active_trades = {}
        extreme_trade_tracker.history = []

    def _payload(self, symbol: str = "BTC", entry: float = 2428.0) -> Dict[str, Any]:
        fvg, anchor = _make_fvg_and_anchor(1_700_000_000_000)
        return {
            "symbol": symbol,
            "direction": "Bullish",
            "state": "PENDING_RETRACE",
            "entry_price": entry,
            "current_price": 2430.0,
            "stop_loss": 2398.0,
            "risk_r": entry - 2398.0,
            "risk_pct": 1.0,
            "tp_1r": entry + 30.0, "tp_2r": entry + 60.0, "tp_3r": entry + 90.0,
            "floating_r": 0.0,
            "entry_time_ist": None,
            "entry_timestamp": None,
            "completion_target": "2R",
            "ltf_timeframe": "5m",
            "strategy": SHADOW_NAME,
            "strategy_params": {},
            "anchor": {"direction": "Bullish", "bottom": 2380.0, "top": 2450.0,
                       "formed_time_ist": "x", "first_touch_time_ist": "y"},
            "target_fvg": {"direction": "Bullish", "bottom": 2412.0, "top": 2428.0,
                           "width": 16.0, "gap_pct": 0.78, "formed_time_ist": "z",
                           "formed_at": 1_700_000_000_000 + 2 * FIVE_MIN_MS},
        }

    def test_same_setup_two_scopes_two_ledger_records(self):
        """The identical setup payload ingests under two scopes without collision."""
        mids = {"BTC": 2430.0}
        e1 = extreme_trade_tracker.process_live_setups(
            [self._payload()], mids, recent_candles_map={},
            session_config=OPEN_SESSION_CONFIG, isolation_scope="",
        )
        e2 = extreme_trade_tracker.process_live_setups(
            [self._payload()], mids, recent_candles_map={},
            session_config=OPEN_SESSION_CONFIG, isolation_scope=SHADOW_NAME,
        )
        assert [evt for evt, _ in e1] == ["NEW_SETUP"]
        assert [evt for evt, _ in e2] == ["NEW_SETUP"]
        assert len(extreme_trade_tracker.active_trades) == 2
        by_scope = {t.isolation_scope: t for t in extreme_trade_tracker.active_trades.values()}
        assert set(by_scope) == {"", SHADOW_NAME}
        # Shadow trade id carries the scope suffix; active-strategy id is legacy format.
        assert by_scope[""].trade_id == "BTC:1700000600000:2428.00"
        assert by_scope[SHADOW_NAME].trade_id == "BTC:1700000600000:2428.00:shadow_fake"
        assert by_scope[SHADOW_NAME].strategy == SHADOW_NAME

    def test_shadow_absence_does_not_expire_active_pending(self):
        """Shadow scope going quiet must not increment the active pending's absent counter."""
        mids = {"BTC": 2430.0}
        extreme_trade_tracker.process_live_setups(
            [self._payload()], mids, recent_candles_map={},
            session_config=OPEN_SESSION_CONFIG, isolation_scope="",
        )
        active_pending = next(iter(extreme_trade_tracker.active_trades.values()))
        assert active_pending.absent_cycles == 0

        # Several shadow-scope passes with NO shadow setups: the ""-scoped pending
        # is not the shadow pass's business.
        for _ in range(5):
            extreme_trade_tracker.process_live_setups(
                [], mids, recent_candles_map={},
                session_config=OPEN_SESSION_CONFIG, isolation_scope=SHADOW_NAME,
            )
        assert active_pending.absent_cycles == 0
        assert active_pending in extreme_trade_tracker.active_trades.values()

        # ...and vice versa: a quiet active scope doesn't expire a shadow pending.
        extreme_trade_tracker.process_live_setups(
            [self._payload("ETH")], mids, recent_candles_map={},
            session_config=OPEN_SESSION_CONFIG, isolation_scope=SHADOW_NAME,
        )
        shadow_pending = [t for t in extreme_trade_tracker.active_trades.values() if t.symbol == "ETH"][0]
        for _ in range(5):
            extreme_trade_tracker.process_live_setups(
                [], mids, recent_candles_map={},
                session_config=OPEN_SESSION_CONFIG, isolation_scope="",
            )
        assert shadow_pending.absent_cycles == 0

    def test_shadow_active_trade_does_not_block_active_strategy_scan(self):
        """Ingest for the active scope succeeds even though a shadow trade is ACTIVE on the symbol."""
        from extreme_trade_tracker import TrackedExtremeTrade
        t = TrackedExtremeTrade(
            trade_id="s1", symbol="BTC", direction="Bullish",
            entry_price=2428.0, stop_loss=2398.0, risk_r=30.0, risk_pct=1.0,
            tp_1r=2458.0, tp_2r=2488.0, tp_3r=2518.0, completion_target="2R",
            strategy=SHADOW_NAME, isolation_scope=SHADOW_NAME,
            state="TRADE_ACTIVE",
        )
        extreme_trade_tracker.active_trades = {t.trade_id: t}

        events = extreme_trade_tracker.process_live_setups(
            [self._payload()], {"BTC": 2430.0}, recent_candles_map={},
            session_config=OPEN_SESSION_CONFIG, isolation_scope="",
        )
        assert [evt for evt, _ in events] == ["NEW_SETUP"]


# --------------------------------------------------------------------------- #
# Unit: S3 setup normalization (dict -> attribute view)
# --------------------------------------------------------------------------- #
class TestS3SetupNormalization:
    def test_normalize_fills_defaults_and_attr_access(self):
        from strategies.strategy3_liquidity_sweep import _normalize_setup

        raw = {
            "symbol": "BTC",
            "direction": "Bullish",
            "entry_price": 2428.0,
            "stop_loss": 2398.0,
            "risk_r": 30.0,
            "tp_price": 2488.0,
            "targets": {"1R": 2458.0, "2R": 2488.0, "3R": 2518.0},
            "ltf_timeframe": "5m",
        }
        setup = _normalize_setup(raw)
        assert setup.state == "PENDING_RETRACE"
        assert setup.entry_timestamp is None
        assert setup.floating_r == 0.0
        assert setup.risk_pct == pytest.approx(30.0 / 2428.0 * 100.0)
        assert setup.tp_1r == 2458.0 and setup.tp_2r == 2488.0 and setup.tp_3r == 2518.0
        with pytest.raises(AttributeError):
            _ = setup.nonexistent_field


# --------------------------------------------------------------------------- #
# Integration: shadow strategy through the REAL daemon cycle
# --------------------------------------------------------------------------- #
PATCH_KEYS = (
    "get_market_data_provider",
    "send_extreme_telegram_alert",
    "dashboard_ws_manager",
    "redis_client",
)


class ClosableSink(SinkRecorder):
    async def close(self):
        pass


class TestShadowDaemonCycle:
    @pytest.fixture
    def shadow_pipeline(self, monkeypatch):
        """Real cycle pipeline with a fake shadow strategy enabled."""
        import main
        import telegram_client

        saved_attrs = {k: getattr(main, k) for k in PATCH_KEYS}
        saved_state = dict(main.state)
        # install_patches replaces this module global; restore it or later tests
        # in the session inherit the fake (same hazard test_integration_scenarios
        # documents for its pipeline fixture).
        saved_thread_mode = telegram_client.is_telegram_thread_mode

        provider = FakeProvider(["BTC", "ETH"])
        sink = ClosableSink()
        install_patches(provider, sink)
        configure_state(main, ["BTC"])
        main.state["extreme_shadow_strategies"] = SHADOW_NAME

        extreme_trade_tracker.active_trades = {}
        extreme_trade_tracker.history = []

        yield {"main": main, "provider": provider, "sink": sink}

        telegram_client.is_telegram_thread_mode = saved_thread_mode
        for k, v in saved_attrs.items():
            setattr(main, k, v)
        main.state.clear()
        main.state.update(saved_state)
        extreme_trade_tracker.active_trades = {}
        extreme_trade_tracker.history = []

    @pytest.mark.asyncio
    async def test_shadow_setup_tracked_silent_and_isolated(self, shadow_pipeline, monkeypatch):
        import screener_cycle as sc
        from main import execute_extreme_screener_cycle

        sink = shadow_pipeline["sink"]

        # Spy on the Telegram dispatch path: record every event that WOULD have
        # been alerted, without changing behavior for the active strategy.
        dispatched = []
        orig_dispatch = sc._dispatch_trade_alert

        async def _spy_dispatch(evt_type, tr, msg, chart_img, side):
            dispatched.append((evt_type, tr.trade_id))
            return await orig_dispatch(evt_type, tr, msg, chart_img, side)

        monkeypatch.setattr(sc, "_dispatch_trade_alert", _spy_dispatch)

        setups = await execute_extreme_screener_cycle()

        # Both the active strategy (extreme_fvg, scripted feed geometry) and the
        # fake shadow strategy emit a pending setup for BTC.
        strategies_seen = {s["strategy"] for s in setups}
        assert SHADOW_NAME in strategies_seen
        assert "extreme_fvg" in strategies_seen

        shadow_trades = [
            t for t in extreme_trade_tracker.active_trades.values()
            if t.isolation_scope == SHADOW_NAME
        ]
        assert len(shadow_trades) == 1
        st = shadow_trades[0]
        assert st.strategy == SHADOW_NAME
        assert st.state == "PENDING_RETRACE"
        assert st.trade_id.endswith(f":{SHADOW_NAME}")

        # SILENCE: no shadow-tagged trade ever reached the Telegram dispatch.
        assert dispatched, "active strategy events should still alert"
        assert all(not tid.endswith(f":{SHADOW_NAME}") for _, tid in dispatched)
        assert st.telegram_message_id is None

        # Dashboard DID get the shadow trade event (via broadcast).
        dash_events = [m for m in sink.dashboard if m.get("type") == "trade_event"]
        shadow_dash = [
            m for m in dash_events
            if m.get("trade", {}).get("isolation_scope") == SHADOW_NAME
        ]
        assert [m.get("event") for m in shadow_dash] == ["NEW_SETUP"]

        # Per-strategy summary split works.
        assert extreme_trade_tracker.get_summary(strategy=SHADOW_NAME)["total_tracked_trades"] == 1
        assert extreme_trade_tracker.get_summary()["total_tracked_trades"] >= 2

    @pytest.mark.asyncio
    async def test_shadow_active_does_not_suppress_active_strategy_scan(self, shadow_pipeline):
        """A shadow ACTIVE trade on BTC must not stop the active strategy from scanning BTC."""
        from main import execute_extreme_screener_cycle

        # Seed: run one cycle so both strategies emit pendings.
        await execute_extreme_screener_cycle()

        # Force the shadow pending to ACTIVE state (as if filled).
        st = next(t for t in extreme_trade_tracker.active_trades.values() if t.isolation_scope == SHADOW_NAME)
        st.state = "TRADE_ACTIVE"
        st.entry_timestamp = 1_700_000_000_000
        st.entry_price = st.entry_price

        setups = await execute_extreme_screener_cycle()

        # The active strategy must still have scanned and emitted for BTC.
        active_scope_setups = [
            s for s in setups
            if s["strategy"] == "extreme_fvg" and s["symbol"] == "BTC"
        ]
        assert active_scope_setups, "active strategy was suppressed by shadow ACTIVE trade"

        # And the shadow ACTIVE trade is reported as-is in the same cycle.
        shadow_active_payloads = [
            s for s in setups
            if s["strategy"] == SHADOW_NAME and s["state"] == "TRADE_ACTIVE"
        ]
        assert shadow_active_payloads

    @pytest.mark.asyncio
    async def test_two_shadow_scopes_independent(self, shadow_pipeline):
        from main import execute_extreme_screener_cycle

        shadow_pipeline["main"].state["extreme_shadow_strategies"] = f"{SHADOW_NAME},{OTHER_SHADOW_NAME}"
        await execute_extreme_screener_cycle()

        scopes = {t.isolation_scope for t in extreme_trade_tracker.active_trades.values()}
        assert {"" , SHADOW_NAME, OTHER_SHADOW_NAME} <= scopes

        # Each scope owns exactly its own BTC pending record.
        for scope in (SHADOW_NAME, OTHER_SHADOW_NAME):
            own = [t for t in extreme_trade_tracker.active_trades.values()
                   if t.isolation_scope == scope and t.symbol == "BTC"]
            assert len(own) == 1
