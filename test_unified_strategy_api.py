"""
Tests for the unified strategy API surface (openspec change
`unified-strategy-ui-replay-backtest`, task 3.9):

- GET  /api/strategies           -> registry enumeration + active flag
- POST /api/{strategy}/activate  -> valid switch / unknown 404
- GET  /api/{strategy}/info      -> metadata + declared default_params
- GET  /api/{strategy}/scan      -> live setup scan envelope (with provider patch)
- POST /api/replay/start (+ snapshot/report via the manager) -> replay flow
  start -> RUNNING/COMPLETED -> snapshot has ledger summary -> report has
  engine:"replay" + closed_trades + metrics. Uses TestClient so the ASGI
  lifespan (service registration) runs exactly as in production.
"""

import asyncio
import contextlib
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from replay_manager import replay_manager
from strategies import get_strategy, list_strategy_names

# Replay runs against the shared registry; the extreme strategy resolves.
_KNOWN = "extreme_fvg"


def _wait_replay_done(client: TestClient, replay_id: str, timeout: float = 60) -> None:
    """Polls the replay snapshot endpoint until the run leaves PREPARING/RUNNING.

    Done from the test's (portal) thread via HTTP only — no cross-thread event
    loop access — which mirrors exactly how the dashboard consumes progress.
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get(f"/api/replay/{replay_id}").json()
        if last.get("status") not in ("PREPARING", "RUNNING", "PAUSED"):
            return
        time.sleep(0.05)
    raise AssertionError(f"Replay {replay_id} did not finish in {timeout}s (last status: {last and last.get('status')})")


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _restore_active_strategy():
    """Activation mutates global daemon state; restore it after each test."""
    from app_config import state

    prev = state.get("extreme_active_strategy")
    yield
    state["extreme_active_strategy"] = prev


@pytest.fixture(autouse=True)
def _isolate_replay_tracker(monkeypatch, tmp_path):
    """Replay tests must never read the real ledger file or touch Redis."""
    def _init(self):
        self.active_trades = {}
        self.history = []
    monkeypatch.setattr("extreme_trade_tracker.ExtremeTradeTracker._load", _init)
    monkeypatch.setattr(
        "extreme_trade_tracker.ExtremeTradeTracker._save_local", lambda self: None
    )
    monkeypatch.setattr(replay_manager, "_runs", {})


def _fake_dataset(dataset):
    async def _fn(symbols, days, ltf_timeframe, provider):
        return dataset
    return _fn


def _synthetic_dataset():
    from strategy_extreme_fvg import Candle

    def _mk(ts_min, open_px, close_px):
        return Candle(
            timestamp=ts_min * 60 * 1000,
            open=open_px,
            high=max(open_px, close_px) + 2.0,
            low=min(open_px, close_px) - 2.0,
            close=close_px,
            volume=100.0,
        )

    ltf = []
    px = 100.0
    for m in range(0, 6 * 24 * 60, 5):
        nxt = px + (-0.02 if (m // 60) % 12 < 6 else 0.02)
        ltf.append(_mk(m, px, nxt))
        px = nxt
    htf = []
    px4 = 100.0
    for h in range(0, 8 * 24, 4):
        nxt = px4 + (-0.3 if (h // 4) % 12 < 6 else 0.3)
        htf.append(_mk(h * 60, px4, nxt))
        px4 = nxt
    return {"BTC": {"ltf": ltf, "4h": htf}}


# ======================================================================
# /api/strategies
# ======================================================================

class TestStrategiesList:
    def test_shape_and_registry_names(self, client):
        resp = client.get("/api/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["active_strategy"] in list_strategy_names()
        names = [s["name"] for s in data["strategies"]]
        assert set(names) == set(list_strategy_names())
        for entry in data["strategies"]:
            assert set(entry) == {"name", "display_name", "description", "is_active"}
            assert isinstance(entry["display_name"], str) and entry["display_name"]
            assert isinstance(entry["description"], str)

    def test_exactly_one_active_flag(self, client):
        data = client.get("/api/strategies").json()
        actives = [s for s in data["strategies"] if s["is_active"]]
        assert len(actives) == 1
        assert actives[0]["name"] == data["active_strategy"]


# ======================================================================
# /api/{strategy}/activate
# ======================================================================

class TestStrategyActivate:
    def test_activate_valid_strategy(self, client):
        from app_config import state

        names = list_strategy_names()
        target = names[0] if names[0] != state.get("extreme_active_strategy") else names[-1]
        resp = client.post(f"/api/{target}/activate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["active_strategy"] == target
        assert state["extreme_active_strategy"] == target

    def test_activate_unknown_strategy_404(self, client):
        resp = client.post("/api/not_a_strategy/activate")
        assert resp.status_code == 404
        assert "not_a_strategy" in resp.json()["detail"]
        assert "Available" in resp.json()["detail"]


# ======================================================================
# /api/{strategy}/info
# ======================================================================

class TestStrategyInfo:
    def test_info_shape_matches_strategy_declaration(self, client):
        strat = get_strategy(_KNOWN)
        resp = client.get(f"/api/{_KNOWN}/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["name"] == _KNOWN
        assert data["display_name"] == strat.display_name
        assert data["interface_version"] == strat.interface_version
        assert data["default_params"] == strat.default_params

    def test_info_unknown_strategy_404(self, client):
        resp = client.get("/api/does_not_exist/info")
        assert resp.status_code == 404
        assert "Available" in resp.json()["detail"]


# ======================================================================
# /api/{strategy}/scan
# ======================================================================

class TestStrategyScan:
    def test_scan_returns_setups_envelope(self, client):
        strat = get_strategy(_KNOWN)

        class _FakeSetup:
            direction = "Bullish"
            entry_price = 100.0
            stop_loss = 99.0
            risk_r = 1.0
            floating_r = 0.0
            entry_time_ist = ""
            entry_timestamp = 0
            tp_1r = 101.0
            tp_2r = 102.0
            tp_3r = 103.0
            ltf_timeframe = "5m"
            completion_target = "2R"
            state = "SETUP"
            dist_pct = 0.1
            risk_pct = 0.5
            anchor = None
            ltf_fvg = None

        captured = {}

        async def _fake_find(self, symbol, provider, params):
            captured["params"] = params
            return [_FakeSetup()]

        async def _fake_mids():
            return {"BTC": 100.0}

        fake_provider = type("P", (), {})()
        fake_provider.resolve_symbol = lambda s: s
        fake_provider.get_all_mids = _fake_mids

        with patch.object(type(strat), "find_setups", _fake_find), \
             patch("main.lookup_mid", side_effect=lambda mids, sym, default: 100.0), \
             patch("main.get_market_data_provider", return_value=fake_provider):
            resp = client.get(f"/api/{_KNOWN}/scan?symbols=BTC")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["strategy"] == _KNOWN
        assert data["count"] == 1
        assert data["setups"][0]["symbol"] == "BTC"
        assert data["setups"][0]["strategy"] == _KNOWN
        assert captured["params"]["ltf_timeframe"] == strat.default_params["ltf_timeframe"]


# ======================================================================
# Replay flow via /api/replay/start + manager snapshot/report
# ======================================================================

class TestReplayFlow:
    def _start(self, client, **overrides):
        payload = {"strategy": _KNOWN, "symbols": "BTC", "days": 2, "ltf_timeframe": "5m", "speed": "MAX"}
        payload.update(overrides)
        return client.post("/api/replay/start", params=payload)

    def test_start_unknown_strategy_404(self, client):
        resp = self._start(client, strategy="nope")
        assert resp.status_code == 404
        assert "nope" in resp.json()["detail"]

    def test_replay_start_status_report_flow(self, client):
        with patch("replay_manager.build_replay_dataset", new=_fake_dataset(_synthetic_dataset())):
            resp = self._start(client)
            assert resp.status_code == 200, resp.text
            replay_id = resp.json()["replay_id"]

            _wait_replay_done(client, replay_id, timeout=60)

            snap = client.get(f"/api/replay/{replay_id}").json()
            assert snap["status"] == "COMPLETED"
            assert snap["cycles"] > 0
            assert snap["ledger_summary"]["total_tracked_trades"] >= 0
            assert snap["virtual_time_ms"] is not None

            report = client.get(f"/api/replay/{replay_id}/report").json()
            assert report["report"]["engine"] == "replay"
            assert report["report"]["status"] == "COMPLETED"
            assert "metrics" in report["report"]
            assert isinstance(report["report"]["closed_trades"], list)
            assert report["report"]["effective_params"]["ltf_timeframe"] == "5m"

    def test_replay_snapshot_unknown_id_404(self, client):
        assert client.get("/api/replay/zzz").status_code == 404
        assert client.get("/api/replay/zzz/report").status_code == 404
        assert client.post("/api/replay/zzz/pause").status_code == 404
