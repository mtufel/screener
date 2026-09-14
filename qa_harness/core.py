"""
qa_harness/core.py

Shared utilities and base classes for the modular QA harness.
Provides FakeProvider, SinkRecorder, install_patches, configure_state,
AtomicScenarioContext for atomic per-scenario reset of mutable state.
"""
import logging
from typing import Any, Dict, List

from extreme_trade_tracker import extreme_trade_tracker as global_extreme_trade_tracker
from strategy_extreme_fvg import htf_fvg_cache
from candle_store import candle_store
import main
import telegram_client

logger = logging.getLogger("qa_harness.core")

FIVE_MIN_MS = 5 * 60 * 1000
FOUR_H_MS = 4 * 3600 * 1000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % FIVE_MIN_MS)

def c(ts: int, o: float, h: float, l: float, cl: float) -> Dict[str, Any]:
    return {"t": ts, "o": o, "h": h, "l": l, "c": cl, "v": 10.0}

def bullish_4h() -> List[Dict[str, Any]]:
    return [
        c(T0, 2400.0, 2402.0, 2390.0, 2395.0),
        c(T0 + FOUR_H_MS, 2395.0, 2450.0, 2394.0, 2445.0),
        c(T0 + 2 * FOUR_H_MS, 2445.0, 2460.0, 2415.0, 2455.0),
        c(T0 + 3 * FOUR_H_MS, 2455.0, 2470.0, 2410.0, 2465.0),
    ]

def ltf_formation() -> List[Dict[str, Any]]:
    base = T0 + 3 * FOUR_H_MS + FIVE_MIN_MS
    return [
        c(base, 2412.0, 2418.0, 2414.0, 2416.0),
        c(base + FIVE_MIN_MS, 2416.0, 2427.0, 2416.0, 2426.0),
        c(base + 2 * FIVE_MIN_MS, 2426.0, 2431.0, 2429.0, 2430.5),
    ]


class FakeProvider:
    """Stand-in BaseMarketDataProvider serving scripted, advancing candle feeds."""

    def __init__(self, symbols: List[str]):
        self._symbols = symbols
        self._feed: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for idx, s in enumerate(symbols):
            off = (idx + 1) * FOUR_H_MS * 100  # stagger so anchors are independent
            self._feed[s] = {
                "4h": [dict(x, t=x["t"] + off) for x in bullish_4h()],
                "5m": [dict(x, t=x["t"] + off) for x in ltf_formation()],
            }

    @property
    def name(self) -> str:
        return "fake_provider"

    @property
    def supports_websocket(self) -> bool:
        return True

    @property
    def is_websocket_connected(self) -> bool:
        return True

    def resolve_symbol(self, raw: str) -> str:
        return raw.upper()

    async def get_all_mids(self) -> Dict[str, float]:
        out = {}; 
        for s in self._symbols:
            series = self._feed.get(s, {}).get("5m", [])
            if series:
                out[s] = float(series[-1]["c"])
        return out

    async def get_last_n_candles(self, symbol, timeframe="5m", n=300):
        series = self._feed.get(symbol.upper(), {}).get(timeframe)
        return list(series[-n:]) if series else []

    async def get_historical_candles_range(self, coin, interval, start_time_ms, end_time_ms):
        return []

    async def get_universe_coins(self, min_volume=0.0):
        return []

    async def close(self):
        pass

    async def start_websocket(self, symbols=None, timeframes=None):
        return True

    async def stop_websocket(self):
        pass

    def append_ltf(self, symbol: str, o, h, l, cl):
        series = self._feed[symbol]["5m"]
        if not series:
            # No base timestamp; use T0
            last_t = T0
        else:
            last_t = series[-1]["t"]
        series.append(c(last_t + FIVE_MIN_MS, o, h, l, cl))


class SinkRecorder:
    def __init__(self):
        self.telegram: List[tuple] = []
        self.dashboard: List[dict] = []
        self.marked: List[tuple] = []
        self.seen: set = set()

    async def send_telegram(self, msg, **kwargs):
        self.telegram.append((msg, kwargs))
        return True, 1111

    async def broadcast(self, message):
        self.dashboard.append(message)

    async def is_alert_sent(self, symbol, evt_type, trade_id):
        key = (symbol, evt_type, trade_id)
        self.seen.add(key)
        return key in self.seen and False

    async def mark_alert_sent(self, symbol, evt_type, trade_id):
        self.marked.append((symbol, evt_type, trade_id))

    async def get_key(self, *a, **k):
        return "k"


def install_patches(provider: FakeProvider, sink: SinkRecorder):
    main.get_market_data_provider = lambda *a, **k: provider
    main.send_extreme_telegram_alert = sink.send_telegram
    main.dashboard_ws_manager.broadcast = sink.broadcast
    main.redis_client = sink
    telegram_client.is_telegram_thread_mode = lambda: False

    from extreme_trade_tracker import extreme_trade_tracker
    extreme_trade_tracker.active_trades = {}
    extreme_trade_tracker.history = []
    return main


def configure_state(main, symbols, ltf="5m"):
    main.state["coins_whitelist"] = ",".join(symbols)
    main.state["extreme_ltf"] = ltf
    main.state["extreme_target"] = "2R"
    main.state["extreme_min_gap"] = 0.05
    main.state["extreme_use_close"] = False
    main.state["extreme_session_filter"] = False
    main.state["extreme_weekday_filter"] = False
    main.state["extreme_entry_session_filter"] = False
    main.state["extreme_entry_weekday_filter"] = False
    main.state["data_provider"] = "fake"
    main.state["extreme_is_running"] = True
    main.state["extreme_interval_seconds"] = 30
    main.state["extreme_total_cycles"] = 0
    main.state["extreme_setups"] = []


class AtomicScenarioContext:
    """Ensures atomic per-scenario reset of mutable, cross-scenario state."""

    def __init__(self):
        self._state = {}

    async def __aenter__(self):
        from extreme_trade_tracker import extreme_trade_tracker as tracker
        self._state["tracker_active"] = dict(tracker.active_trades)
        self._state["tracker_history"] = list(tracker.history)
        tracker.active_trades.clear()
        tracker.history.clear()

        self._state["htf_cache_symbols"] = {
            s: list(v) for s, v in htf_fvg_cache.active_fvgs.items()
        }
        for sym in list(htf_fvg_cache.active_fvgs.keys()):
            htf_fvg_cache.active_fvgs[sym].clear()

        self._state["candle_keys"] = set(candle_store._candles.keys())
        for key in list(candle_store._candles.keys()):
            candle_store._candles.pop(key, None)
            candle_store._last_sync.pop(key, None)
        for prov in list(candle_store._mids_cache.keys()):
            candle_store._mids_cache.pop(prov, None)
            candle_store._rate_limit_cooldown.pop(prov, None)

        self._state["main_state_keys"] = set(main.state.keys())
        main.state.clear()

        htf_fvg_cache.invalidate_cache()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


async def run_scenario(name, symbols, feeds):
    """Runs a scenario on the real worker; feeds = list of {sym: candles} to append per scan."""
    from main import execute_extreme_screener_cycle

    async with AtomicScenarioContext() as ctx:
        provider = FakeProvider(list(symbols))
        sink = SinkRecorder()
        install_patches(provider, sink)
        configure_state(main, list(symbols))

        print(f"\n======================= SCENARIO: {name} =======================")
        for step, feed in enumerate(feeds):
            for sym, candles in feed.items():
                for cand in candles or []:
                    provider.append_ltf(sym, cand["o"], cand["h"], cand["l"], cand["c"])
            await execute_extreme_screener_cycle()
            from extreme_trade_tracker import extreme_trade_tracker as _tr
            states = {t.symbol: (t.state, t.status_detail) for t in _tr.active_trades.values()}
            print(f"  [scan {step}] active_states={states}")
        print(f"  -> events fired: {len(sink.marked)} | telegram sends: {len(sink.telegram)}")
        return sink
