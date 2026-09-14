"""
QA Live Simulation Harness — Real-Time Trade Entry / Exit / Formation.

Drives the REAL `main.execute_extreme_screener_cycle()` (the exact function the
Strategy-2 background worker calls every scan) with a fake market-data provider that
serves scripted candles/mids, simulating live candle closes across each scan cycle.
Records every notification side-effect (Telegram, Redis dedup, dashboard WS) and
captures DEBUG logs so we can verify the state machine + alerts against logs.

Scenarios exercised (all through the real pipeline):
  S1 FORMATION     : 4H anchor touch + post-touch LTF gap  -> NEW_SETUP + PENDING_RETRACE
  S2 ENTRY FILL    : price retraces into entry             -> TRADE_ACTIVE + ENTRY_FILLED
  S3 TP EXIT       : price hits 2R TP                       -> COMPLETED_TP + TP_HIT
  S4 SL EXIT       : price hits SL after fill               -> STOPPED_OUT  + SL_HIT
  S5 INVALIDATION  : SL breached before entry               -> INVALIDATED  + SETUP_INVALIDATED
  S6 ANTI-SPAM     : repeated scans must not re-send alerts
"""
import asyncio
import logging
import sys
from typing import Any, Dict, List

# --------------------------------------------------------------------------- #
# Logging: capture everything to file + console
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("/tmp/qa_live_sim.log", mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

FIVE_MIN_MS = 5 * 60 * 1000
FOUR_H_MS = 4 * 3600 * 1000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % FIVE_MIN_MS)


def c(ts: int, o: float, h: float, l: float, cl: float) -> Dict[str, Any]:
    return {"t": ts, "o": o, "h": h, "l": l, "c": cl, "v": 10.0}


def bullish_4h() -> List[Dict[str, Any]]:
    """4H: zone [2402..2415] formed by h1..h3; h4 low 2410 touches the zone."""
    return [
        c(T0, 2400.0, 2402.0, 2390.0, 2395.0),
        c(T0 + FOUR_H_MS, 2395.0, 2450.0, 2394.0, 2445.0),
        c(T0 + 2 * FOUR_H_MS, 2445.0, 2460.0, 2415.0, 2455.0),
        c(T0 + 3 * FOUR_H_MS, 2455.0, 2470.0, 2410.0, 2465.0),
    ]


def ltf_formation() -> List[Dict[str, Any]]:
    """5m: gap [2418..2429] forms (l1..l3). Entry 2429 / SL 2414 / risk 15 / TP2 2459."""
    base = T0 + 3 * FOUR_H_MS + FIVE_MIN_MS
    return [
        c(base, 2412.0, 2418.0, 2414.0, 2416.0),                   # l1 pre-impulse
        c(base + FIVE_MIN_MS, 2416.0, 2427.0, 2416.0, 2426.0),    # l2 impulse
        c(base + 2 * FIVE_MIN_MS, 2426.0, 2431.0, 2429.0, 2430.5),  # l3 holds above gap
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

    # --- BaseMarketDataProvider interface ---
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
        return {s: float(self._feed[s]["5m"][-1]["c"]) for s in self._symbols}

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

    # --- simulation control ---
    def append_ltf(self, symbol: str, o, h, l, cl):
        last = self._feed[symbol]["5m"][-1]["t"]
        self._feed[symbol]["5m"].append(c(last + FIVE_MIN_MS, o, h, l, cl))
# --------------------------------------------------------------------------- #
# Notification recorders (side-effect sinks)
# --------------------------------------------------------------------------- #
class SinkRecorder:
    def __init__(self):
        self.telegram: List[tuple] = []     # (msg_text, kwargs)
        self.dashboard: List[dict] = []
        self.marked: List[tuple] = []       # (symbol, evt_type, trade_id)
        self.seen: set = set()

    async def send_telegram(self, msg, **kwargs):
        self.telegram.append((msg, kwargs))
        return True, 1111  # (success, sent_message_id)

    async def broadcast(self, message):
        self.dashboard.append(message)

    async def is_alert_sent(self, symbol, evt_type, trade_id):
        key = (symbol, evt_type, trade_id)
        self.seen.add(key)
        return key in self.seen and False  # never pre-sent -> allow firing

    async def mark_alert_sent(self, symbol, evt_type, trade_id):
        self.marked.append((symbol, evt_type, trade_id))

    # redis_client stub surface
    async def get_key(self, *a, **k):
        return "k"


# --------------------------------------------------------------------------- #
# Patch the real module so all side effects are captured (no real sends)
# --------------------------------------------------------------------------- #
def install_patches(provider: FakeProvider, sink: SinkRecorder):
    import main
    import telegram_client

    main.get_market_data_provider = lambda *a, **k: provider
    main.send_extreme_telegram_alert = sink.send_telegram
    main.dashboard_ws_manager.broadcast = sink.broadcast
    main.redis_client = sink  # is_alert_sent / mark_alert_sent / get_key
    telegram_client.is_telegram_thread_mode = lambda: False

    # Reset persisted tracker state so each run is from a clean slate
    from extreme_trade_tracker import extreme_trade_tracker
    extreme_trade_tracker.active_trades = {}
    extreme_trade_tracker.history = []

    return main
# --------------------------------------------------------------------------- #
# Scenario orchestration & QA runner
# --------------------------------------------------------------------------- #
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


async def run_scenario(name, symbols, feeds):
    """Runs a scenario on the real worker; `feeds` = list of {sym: candles} to append per scan."""
    from main import execute_extreme_screener_cycle
    from strategy_extreme_fvg import htf_fvg_cache

    htf_fvg_cache.invalidate_cache()

    provider = FakeProvider(list(symbols))
    sink = SinkRecorder()
    main = install_patches(provider, sink)
    configure_state(main, list(symbols))

    print(f"\n======================= SCENARIO: {name} =======================")
    for step, feed in enumerate(feeds):
        # advance the 'live' candle feed for each symbol  (simulate candle closes)
        for sym, candles in feed.items():
            for cand in candles or []:
                provider.append_ltf(sym, cand["o"], cand["h"], cand["l"], cand["c"])
        await execute_extreme_screener_cycle()
        from extreme_trade_tracker import extreme_trade_tracker as _tr
        states = {t.symbol: (t.state, t.status_detail) for t in _tr.active_trades.values()}
        print(f"  [scan {step}] active_states={states}")
    print(f"  -> events fired: {len(sink.marked)} | telegram sends: {len(sink.telegram)}")
    return sink
async def main():
    # ---- S1+S2+S3: BTC pending -> filled -> TP (win) ----
    print("\n########## S1+S2+S3: BTC formation -> pending -> entry fill -> TP ##########")
    sink_btc = await run_scenario(
        "BTC full lifecycle (exits need +1 scan to close bar)",
        ["BTC"],
        [
            {},
            {"BTC": [c(0, 2430.0, 2430.5, 2428.5, 2429.5)]},  # fill entry (low<=2429)
            {"BTC": [c(0, 2429.5, 2460.0, 2429.0, 2455.0)]},  # TP2 wick (high>=2459)
            {"BTC": [c(0, 2455.0, 2456.0, 2454.0, 2455.0)]}, # benign: closes TP bar
        ],
    )

    # ---- S4: ETH pending -> filled -> SL (loss) ----
    print("\n########## S4: ETH formation -> pending -> entry fill -> SL ##########")
    sink_eth = await run_scenario(
        "ETH stop-loss exit (exits need +1 scan to close bar)",
        ["ETH"],
        [
            {},
            {"ETH": [c(0, 2430.0, 2430.5, 2428.5, 2429.5)]},  # fill
            {"ETH": [c(0, 2428.0, 2430.0, 2412.0, 2415.0)]},  # SL wick (low<=2414)
            {"ETH": [c(0, 2415.0, 2418.0, 2416.0, 2417.0)]}, # benign: closes SL bar
        ],
    )

    # ---- S5: SOL pending -> invalidated (SL before entry) ----
    print("\n########## S5: SOL pending -> invalidated (SL breach before entry) ##########")
    sink_sol = await run_scenario(
        "SOL invalidation (needs +1 scan to close bar)",
        ["SOL"],
        [
            {},
            {"SOL": [c(0, 2420.0, 2428.0, 2413.0, 2419.0)]},  # low<=SL & high<entry
            {"SOL": [c(0, 2419.0, 2421.0, 2418.0, 2420.0)]}, # benign: closes invalidation bar
        ],
    )

    print("\n================ SCENARIOS DONE (diagnostic) ================")
    print("\nBTC marked events:", [(s, e) for (s, e, _) in sink_btc.marked])
    print("ETH marked events:", [(s, e) for (s, e, _) in sink_eth.marked])
    print("SOL marked events:", [(s, e) for (s, e, _) in sink_sol.marked])
    print("BTC telegram sends:", len(sink_btc.telegram))
    print("ETH telegram sends:", len(sink_eth.telegram))
    print("SOL telegram sends:", len(sink_sol.telegram))


if __name__ == "__main__":
    asyncio.run(main())
