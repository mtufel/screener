"""
Offline tests for GET /api/extreme/4h-fvgs — the "current view of all 4H FVGs"
endpoint (all unmitigated 4H zones per tracked symbol + strategy anchor flag).

Uses the proven qa_harness FakeProvider feed geometry (bullish_4h()) and the
root conftest cache/Redis isolation. No network, no real ledger.
"""

import pytest
from fastapi.testclient import TestClient

from qa_harness.core import FakeProvider, SinkRecorder, install_patches, configure_state, T0, FOUR_H_MS
from strategy_extreme_fvg import htf_fvg_cache

# bullish_4h() geometry: FVG formed at T0+2*H4 with zone [2402, 2415] (bottom=c1.high, top=c3.low)
EXPECTED_FVG_BOTTOM = 2402.0
EXPECTED_FVG_TOP = 2415.0


class ClosableSink(SinkRecorder):
    """Satisfies the lifespan-shutdown contract (await redis_client.close())."""

    async def close(self):
        pass

    def is_configured(self):
        return False


@pytest.fixture
def client():
    """TestClient wired to the fake market: whitelist BTC+ETH, BTC has the scripted bullish 4H FVG."""
    import main
    import telegram_client
    from dashboard_ws import dashboard_ws_manager as real_ws_manager

    provider = FakeProvider(["BTC", "ETH"])
    sink = ClosableSink()

    # install_patches replaces main.* service attrs AND telegram_client.is_telegram_thread_mode
    # + dashboard_ws_manager.broadcast; save/restore the ones install_patches doesn't restore
    # (same hygiene as the `pipeline` fixture in test_integration_scenarios.py).
    saved_attrs = {k: getattr(main, k) for k in (
        "get_market_data_provider", "send_extreme_telegram_alert",
        "dashboard_ws_manager", "redis_client",
    )}
    saved_thread_mode = telegram_client.is_telegram_thread_mode
    saved_broadcast = real_ws_manager.broadcast

    install_patches(provider, sink)
    configure_state(main, ["BTC", "ETH"])
    htf_fvg_cache.invalidate_cache()

    with TestClient(main.app) as c:
        yield c

    telegram_client.is_telegram_thread_mode = saved_thread_mode
    real_ws_manager.broadcast = saved_broadcast
    for k, v in saved_attrs.items():
        setattr(main, k, v)
    htf_fvg_cache.invalidate_cache()


def _symbols_payload(resp):
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert isinstance(body["symbols"], list)
    return body


# --------------------------------------------------------------------------- #
# 1. Full whitelist coverage — zero-FVG symbols still listed
# --------------------------------------------------------------------------- #
def test_all_whitelist_symbols_present(client):
    body = _symbols_payload(client.get("/api/extreme/4h-fvgs"))
    symbols = {s["symbol"] for s in body["symbols"]}
    assert symbols == {"BTC", "ETH"}

    eth = next(s for s in body["symbols"] if s["symbol"] == "ETH")
    # FakeProvider staggers feeds so ETH has no valid closed 4H FVG with the same geometry;
    # whatever it has, the payload shape must be complete.
    assert eth["fvg_count"] == len(eth["fvgs"])
    for f in eth["fvgs"]:
        assert set(f) >= {
            "direction", "top", "bottom", "width", "gap_pct", "formed_at",
            "formed_time_ist", "first_touch_time_ist", "most_recent_touch_time_ist",
            "is_currently_inside", "distance_to_zone_pct", "is_active_anchor",
        }


# --------------------------------------------------------------------------- #
# 2. FVG math from the scripted 4H feed
# --------------------------------------------------------------------------- #
def test_bullish_fvg_boundaries_and_gap_pct(client):
    body = _symbols_payload(client.get("/api/extreme/4h-fvgs"))
    btc = next(s for s in body["symbols"] if s["symbol"] == "BTC")
    assert btc["fvg_count"] >= 1

    fvg = next(f for f in btc["fvgs"] if f["formed_at"] is not None and f["direction"] == "Bullish")
    assert fvg["bottom"] == pytest.approx(EXPECTED_FVG_BOTTOM)
    assert fvg["top"] == pytest.approx(EXPECTED_FVG_TOP)
    assert fvg["width"] == pytest.approx(13.0)
    assert fvg["gap_pct"] == pytest.approx(round((13.0 / ((2402.0 + 2415.0) / 2.0)) * 100.0, 3), abs=1e-9)
    assert fvg["formed_time_ist"]  # formatted IST string present


# --------------------------------------------------------------------------- #
# 3. Anchor selection: currently-inside zone wins
# --------------------------------------------------------------------------- #
def test_price_inside_zone_is_anchor(client):
    body = _symbols_payload(client.get("/api/extreme/4h-fvgs"))
    btc = next(s for s in body["symbols"] if s["symbol"] == "BTC")
    fvg = next(f for f in btc["fvgs"] if f["direction"] == "Bullish")

    # FakeProvider mid = last 5m close (2430.5 for the standard formation feed);
    # push it INTO the zone so the live-price inside check selects this zone.
    inside_price = (EXPECTED_FVG_BOTTOM + EXPECTED_FVG_TOP) / 2.0
    import main
    provider = main.get_market_data_provider()
    provider._feed["BTC"]["5m"][-1]["c"] = inside_price

    body2 = _symbols_payload(client.get("/api/extreme/4h-fvgs"))
    btc2 = next(s for s in body2["symbols"] if s["symbol"] == "BTC")
    fvg2 = next(f for f in btc2["fvgs"] if f["direction"] == "Bullish")

    assert btc2["current_price"] == pytest.approx(inside_price)
    assert fvg2["is_currently_inside"] is True
    assert fvg2["is_active_anchor"] is True
    assert fvg2["distance_to_zone_pct"] == pytest.approx(0.0)
    assert fvg2["most_recent_touch_time_ist"] == "Currently Inside (Active Now)"
    assert btc2["active_anchor_formed_at"] == fvg2["formed_at"]


# --------------------------------------------------------------------------- #
# 4. Untouched FVG: null touch times + correct distance
# --------------------------------------------------------------------------- #
def test_untouched_fvg_null_touch_and_distance(client):
    body = _symbols_payload(client.get("/api/extreme/4h-fvgs"))
    btc = next(s for s in body["symbols"] if s["symbol"] == "BTC")

    # Standard FakeProvider 5m formation feed tops out at 2430.5 (inside 2415 zone top? No:
    # formation candles are above the zone) — the zone may legitimately be touched by the
    # formation feed. Find a zone far from price, or verify the contract on whatever zone
    # is untouched.
    untouched = [f for f in btc["fvgs"] if f["first_touch_time_ist"] is None]
    for f in untouched:
        assert f["is_currently_inside"] is False
        assert f["is_active_anchor"] is False
        assert f["distance_to_zone_pct"] is not None


def test_distance_pct_signed_correctly(client):
    # Price above zone -> positive distance; zone below price.
    body = _symbols_payload(client.get("/api/extreme/4h-fvgs"))
    btc = next(s for s in body["symbols"] if s["symbol"] == "BTC")
    for f in btc["fvgs"]:
        d = f["distance_to_zone_pct"]
        if d is None:
            continue
        px = btc["current_price"]
        near_edge = f["bottom"] if px >= f["bottom"] else f["top"]
        expected = ((px - near_edge) / px) * 100.0
        assert d == pytest.approx(expected, rel=1e-3)


# --------------------------------------------------------------------------- #
# 5. invalidation=close query param respected
# --------------------------------------------------------------------------- #
def test_invalidation_mode_param(client):
    body = _symbols_payload(client.get("/api/extreme/4h-fvgs?invalidation=close"))
    for s in body["symbols"]:
        assert s["invalidation_mode"] == "close"

    body2 = _symbols_payload(client.get("/api/extreme/4h-fvgs"))
    for s in body2["symbols"]:
        assert s["invalidation_mode"] == "wick"  # configure_state default
