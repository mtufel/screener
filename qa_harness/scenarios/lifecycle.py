"""
lifecycle.py -- Core trade lifecycle scenarios for the Extreme LTF (Strategy 2) screener.

Covers:
  - PENDING_RETRACE -> INVALIDATED        (SL or 4H anchor breach before entry)
  - PENDING_RETRACE -> TRADE_ACTIVE        (entry fill, no TP/SL yet)
  - TRADE_ACTIVE     -> COMPLETED_TP       (TP hit, with target = 1R/2R/3R)
  - TRADE_ACTIVE     -> STOPPED_OUT        (SL hit, -1.0R realized)

Each scenario factory has signature `(direction, completion_target) -> ScenarioRunner`
where direction in {"Bullish","Bearish"} and completion_target in {"1R","2R","3R"}.

The +1 scan lag applies to TP/SL detection: the cycle that *appends* a candle
is the same cycle whose tracker call only sees previously-fetched candles.
So TP/SL candle on scan N+1 produces TP_HIT/SL_HIT event on scan N+1.

Setup prices (entry, SL, TP) are derived by probing the real scanner once,
so the tests remain correct across builder refactors.
"""
from __future__ import annotations

import concurrent.futures
from typing import Any, Dict, List

from qa_harness.core import (
    FakeProvider, SinkRecorder, install_patches, configure_state,
    c, T0, FIVE_MIN_MS, FOUR_H_MS,
)
import main
from main import execute_extreme_screener_cycle
from extreme_trade_tracker import extreme_trade_tracker as tracker


SYMBOL = "LIFETEST"

_setup_cache: Dict[str, Dict[str, float]] = {}


# ---------------------------------------------------------------------------
# Anchor + LTF FVG builders
# ---------------------------------------------------------------------------

def _bullish_4h() -> List[Dict[str, Any]]:
    """4H FVG [2402..2415]; c4 low 2410 touches the zone."""
    return [
        c(T0, 2400.0, 2402.0, 2390.0, 2395.0),
        c(T0 + FOUR_H_MS, 2395.0, 2450.0, 2394.0, 2445.0),
        c(T0 + 2 * FOUR_H_MS, 2445.0, 2460.0, 2415.0, 2455.0),
        c(T0 + 3 * FOUR_H_MS, 2455.0, 2470.0, 2410.0, 2465.0),
    ]


def _bearish_4h() -> List[Dict[str, Any]]:
    """4H FVG [2395..2400]; c4 high 2397 touches the zone.
    Bearish FVG rule: c3.high < c1.low.
    c1.low=2400, c3.high=2395 -> zone [2395, 2400]. Touch: c4.high=2397.
    """
    return [
        c(T0, 2410.0, 2420.0, 2400.0, 2405.0),
        c(T0 + FOUR_H_MS, 2405.0, 2410.0, 2395.0, 2400.0),
        c(T0 + 2 * FOUR_H_MS, 2400.0, 2395.0, 2380.0, 2385.0),
        c(T0 + 3 * FOUR_H_MS, 2385.0, 2397.0, 2375.0, 2380.0),
    ]


def _build_4h(direction: str) -> List[Dict[str, Any]]:
    return _bullish_4h() if direction == "Bullish" else _bearish_4h()


def _build_ltf_fvg_bullish(formed_at_ms: int) -> List[Dict[str, Any]]:
    """Returns 3 closed 5m candles forming a valid Bullish FVG [2418..2429]."""
    return [
        c(formed_at_ms, 2412.0, 2418.0, 2414.0, 2416.0),
        c(formed_at_ms + FIVE_MIN_MS, 2416.0, 2427.0, 2416.0, 2426.0),
        c(formed_at_ms + 2 * FIVE_MIN_MS, 2426.0, 2431.0, 2429.0, 2430.5),
    ]


def _build_ltf_fvg_bearish(formed_at_ms: int) -> List[Dict[str, Any]]:
    """Returns 3 closed 5m candles forming a valid Bearish FVG.
    From diagnostic: entry=2387.75, SL=2406.5.
    Construct: c1.low=2406.5, c3.high=2387.75 (gap), c3.high<c1.low.
    """
    top = 2406.5
    bottom = 2387.75
    return [
        c(formed_at_ms, top + 0.5, top + 1.5, top, top + 0.5),
        c(formed_at_ms + FIVE_MIN_MS, bottom - 0.5, top + 0.5, bottom - 0.5, bottom + 0.15),
        c(formed_at_ms + 2 * FIVE_MIN_MS, bottom + 0.5, bottom, bottom, bottom - 0.15),
    ]


def _build_ltf_fvg(direction: str, formed_at_ms: int) -> List[Dict[str, Any]]:
    if direction == "Bullish":
        return _build_ltf_fvg_bullish(formed_at_ms)
    return _build_ltf_fvg_bearish(formed_at_ms)


# ---------------------------------------------------------------------------
# Probe setup prices from the real scanner
# ---------------------------------------------------------------------------

def _probe_setup_prices(direction: str, completion_target: str) -> Dict[str, float]:
    """Probe the real scanner to discover the entry/SL/TP it would emit for
    the anchor+FVG constructed by `_anchor_feed(direction)`. Cache the result.
    """
    key = f"{direction}_{completion_target}"
    if key in _setup_cache:
        return _setup_cache[key]
    formed_at = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
    feed_4h = _build_4h(direction)
    feed_ltf = _build_ltf_fvg(direction, formed_at)

    async def probe():
        from strategy_extreme_fvg import get_extreme_setup_for_symbol, htf_fvg_cache
        try:
            from redis_client import redis_client
            if redis_client.is_configured():
                for mode in ("wick", "close"):
                    rk = redis_client.get_key(f"htf_cache:{SYMBOL}:{mode}")
                    try:
                        await redis_client.delete(rk)
                    except Exception:
                        pass
        except Exception:
            pass
        htf_fvg_cache.invalidate_cache()
        provider = FakeProvider([SYMBOL])
        provider._feed[SYMBOL]["4h"] = feed_4h
        provider._feed[SYMBOL]["5m"] = feed_ltf
        return await get_extreme_setup_for_symbol(
            symbol=SYMBOL, ltf_timeframe="5m", client=provider,
            min_gap_pct=0.05, completion_target=completion_target,
        )

    with concurrent.futures.ThreadPoolExecutor() as ex:
        setup = ex.submit(lambda: __import__("asyncio").run(probe())).result()
    assert setup is not None, f"No setup returned for {key}"
    prices = {
        "entry": setup.entry_price,
        "sl": setup.stop_loss,
        "tp_1r": setup.tp_1r,
        "tp_2r": setup.tp_2r,
        "tp_3r": setup.tp_3r,
    }
    _setup_cache[key] = prices
    return prices


# ---------------------------------------------------------------------------
# ScenarioRunner
# ---------------------------------------------------------------------------

class ScenarioRunner:
    """Executes a feed sequence against the real worker.

    Each feed step's candles REPLACE the provider's 5m feed entirely (not append)
    so that direction-specific price levels are preserved and tracker state
    decisions reflect only the scenario's intended candles.
    """

    def __init__(self, name: str, symbols: List[str],
                 feeds: List[Dict[str, List[Dict[str, Any]]]],
                 direction: str):
        self.name = name
        self.symbols = symbols
        self.feeds = feeds
        self.direction = direction

    async def run(self) -> SinkRecorder:
        # Wipe stale cache once at the start of each scenario.
        from strategy_extreme_fvg import htf_fvg_cache
        try:
            from redis_client import redis_client
            if redis_client.is_configured():
                for sym in self.symbols:
                    for mode in ("wick", "close"):
                        rk = redis_client.get_key(f"htf_cache:{sym}:{mode}")
                        try:
                            await redis_client.delete(rk)
                        except Exception:
                            pass
        except Exception:
            pass
        htf_fvg_cache.invalidate_cache()

        provider = FakeProvider(self.symbols)
        # Replace default 4h feed entirely with scenario's intended anchor.
        provider._feed[self.symbols[0]]["4h"] = _build_4h(self.direction)
        # Seed 5m feed with the LTF FVG so the scanner can discover the setup.
        formed_at = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
        provider._feed[self.symbols[0]]["5m"] = list(_build_ltf_fvg(self.direction, formed_at))

        sink = SinkRecorder()
        install_patches(provider, sink)
        configure_state(main, self.symbols)

        for step, feed in enumerate(self.feeds):
            for sym, candles in feed.items():
                for cand in candles or []:
                    provider.append_ltf(sym, cand["o"], cand["h"], cand["l"], cand["c"])
            await execute_extreme_screener_cycle()
            states = {t.symbol: (t.state, t.status_detail) for t in tracker.active_trades.values()}
            print(f"    [{self.name}] scan {step} active={states}")
        print(f"    [{self.name}] events={len(sink.marked)} telegram={len(sink.telegram)}")
        return sink


# ---------------------------------------------------------------------------
# Candle builders
# ---------------------------------------------------------------------------

def _bullish_fill_candle(entry: float, sl: float, fraction: float = 0.15):
    risk = entry - sl
    return c(0, entry - 0.5, entry + risk * (fraction + 0.1),
             entry - 0.3, entry + risk * fraction)


def _bullish_tp_candle(tp: float):
    return c(0, tp - 1, tp + 1, tp - 0.5, tp)


def _bullish_sl_candle(sl: float, risk: float):
    return c(0, sl - 0.5, sl + 1, sl - 1, sl - risk * 0.3)


def _bearish_fill_candle(entry: float, sl: float, fraction: float = 0.15):
    """Bearish fill: high >= entry, low < entry but > (sl - 0.5*risk) (no SL hit)."""
    risk = sl - entry
    return c(0, entry - risk * (fraction + 0.1), entry + 0.5,
             entry + risk * fraction, entry - 0.3)


def _bearish_tp_candle(tp: float):
    return c(0, tp + 1, tp + 1.5, tp - 1, tp)


def _bearish_sl_candle(sl: float, risk: float):
    return c(0, sl + 0.5, sl + 1, sl + 1, sl + risk * 0.3)


def _bullish_invalidation_candle(sl: float, entry: float):
    return c(0, sl - 0.5, entry - 1, sl - 2, sl - 1)


def _bearish_invalidation_candle(sl: float, entry: float):
    return c(0, entry + 1, sl + 1, entry + 2, sl + 2)


def _bullish_benign_candle(entry: float, sl: float):
    risk = entry - sl
    return c(0, entry + risk * 0.2, entry + risk * 0.4, entry, entry + risk * 0.3)


def _bearish_benign_candle(entry: float, sl: float):
    risk = sl - entry
    return c(0, entry - risk * 0.4, entry - risk * 0.2, entry - risk * 0.3, entry - risk * 0.2)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _anchor_feed(direction: str) -> Dict[str, List[Dict[str, Any]]]:
    formed_at = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
    return {SYMBOL: _build_ltf_fvg(direction, formed_at)}


def same_candle_fill_tp(direction: str, completion_target: str) -> ScenarioRunner:
    """PENDING_RETRACE -> TRADE_ACTIVE -> TP_HIT."""
    p = _probe_setup_prices(direction, completion_target)
    entry, sl, tp = p["entry"], p["sl"], p[f"tp_{completion_target.lower()}"]
    risk = abs(entry - sl)
    if direction == "Bullish":
        fill = _bullish_fill_candle(entry, sl)
        tp_candle = _bullish_tp_candle(tp)
    else:
        fill = _bearish_fill_candle(entry, sl)
        tp_candle = _bearish_tp_candle(tp)
    feeds = [{}, _anchor_feed(direction), {SYMBOL: [fill]}, {SYMBOL: [tp_candle]}]
    name = f"{direction} {completion_target} same-candle fill+TP"
    return ScenarioRunner(name, [SYMBOL], feeds, direction)


def same_candle_fill_sl(direction: str, completion_target: str) -> ScenarioRunner:
    """PENDING_RETRACE -> TRADE_ACTIVE -> SL_HIT."""
    p = _probe_setup_prices(direction, completion_target)
    entry, sl, tp = p["entry"], p["sl"], p[f"tp_{completion_target.lower()}"]
    risk = abs(entry - sl)
    if direction == "Bullish":
        fill = _bullish_fill_candle(entry, sl)
        sl_candle = _bullish_sl_candle(sl, risk)
    else:
        fill = _bearish_fill_candle(entry, sl)
        sl_candle = _bearish_sl_candle(sl, risk)
    feeds = [{}, _anchor_feed(direction), {SYMBOL: [fill]}, {SYMBOL: [sl_candle]}]
    name = f"{direction} {completion_target} same-candle fill+SL"
    return ScenarioRunner(name, [SYMBOL], feeds, direction)


def pending_to_entry_fill(direction: str, completion_target: str) -> ScenarioRunner:
    """PENDING_RETRACE -> TRADE_ACTIVE (no TP/SL yet)."""
    p = _probe_setup_prices(direction, completion_target)
    entry, sl = p["entry"], p["sl"]
    if direction == "Bullish":
        fill = _bullish_fill_candle(entry, sl)
        benign = _bullish_benign_candle(entry, sl)
    else:
        fill = _bearish_fill_candle(entry, sl)
        benign = _bearish_benign_candle(entry, sl)
    feeds = [{}, _anchor_feed(direction), {SYMBOL: [fill]}, {SYMBOL: [benign]}]
    name = f"{direction} {completion_target} pending->entry"
    return ScenarioRunner(name, [SYMBOL], feeds, direction)


def active_to_tp(direction: str, completion_target: str) -> ScenarioRunner:
    """Anchor -> fill -> TP."""
    p = _probe_setup_prices(direction, completion_target)
    entry, sl, tp = p["entry"], p["sl"], p[f"tp_{completion_target.lower()}"]
    if direction == "Bullish":
        fill = _bullish_fill_candle(entry, sl)
        tp_candle = _bullish_tp_candle(tp)
    else:
        fill = _bearish_fill_candle(entry, sl)
        tp_candle = _bearish_tp_candle(tp)
    feeds = [{}, _anchor_feed(direction), {SYMBOL: [fill]}, {SYMBOL: [tp_candle]}]
    name = f"{direction} {completion_target} active->TP"
    return ScenarioRunner(name, [SYMBOL], feeds, direction)


def active_to_sl(direction: str, completion_target: str) -> ScenarioRunner:
    """Anchor -> fill -> SL."""
    p = _probe_setup_prices(direction, completion_target)
    entry, sl = p["entry"], p["sl"]
    risk = abs(entry - sl)
    if direction == "Bullish":
        fill = _bullish_fill_candle(entry, sl)
        sl_candle = _bullish_sl_candle(sl, risk)
    else:
        fill = _bearish_fill_candle(entry, sl)
        sl_candle = _bearish_sl_candle(sl, risk)
    feeds = [{}, _anchor_feed(direction), {SYMBOL: [fill]}, {SYMBOL: [sl_candle]}]
    name = f"{direction} {completion_target} active->SL"
    return ScenarioRunner(name, [SYMBOL], feeds, direction)


def pending_to_invalidated(direction: str, completion_target: str) -> ScenarioRunner:
    """PENDING_RETRACE -> INVALIDATED."""
    p = _probe_setup_prices(direction, completion_target)
    entry, sl = p["entry"], p["sl"]
    if direction == "Bullish":
        inv = _bullish_invalidation_candle(sl, entry)
    else:
        inv = _bearish_invalidation_candle(sl, entry)
    feeds = [{}, _anchor_feed(direction), {SYMBOL: [inv]}]
    name = f"{direction} {completion_target} pending->invalidated"
    return ScenarioRunner(name, [SYMBOL], feeds, direction)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

SCENARIOS = {
    **{f"lifecycle/{d}_{t}_same_candle_fill_tp": same_candle_fill_tp
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"lifecycle/{d}_{t}_same_candle_fill_sl": same_candle_fill_sl
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"lifecycle/{d}_{t}_pending_to_entry": pending_to_entry_fill
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"lifecycle/{d}_{t}_active_to_tp": active_to_tp
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"lifecycle/{d}_{t}_active_to_sl": active_to_sl
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"lifecycle/{d}_{t}_pending_to_invalidated": pending_to_invalidated
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
}


ALL_SCENARIO_NAMES = list(SCENARIOS.keys())
