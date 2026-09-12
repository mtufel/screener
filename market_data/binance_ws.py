"""
Binance WebSocket Client (market_data/binance_ws.py)

Streams real-time market data from Binance Futures/Spot WebSocket feeds:
- Subscribes to '!miniTicker@arr' for real-time midpoint prices across all trading pairs.
- Subscribes to '<symbol>@kline_<interval>' streams for active watchlist coins.
- Feeds data directly into CandleStore.
- Automatic reconnect with exponential backoff and connection state tracking.
"""

import asyncio
import json
import logging
import os
import random
from typing import Any, Callable, Dict, List, Optional, Set
import websockets

from candle_store import CandleStore, candle_store

logger = logging.getLogger("market_data.binance_ws")


class BinanceWSClient:
    """Resilient async WebSocket client for Binance Futures & Spot market data."""

    def __init__(
        self,
        use_futures: bool = True,
        store: Optional[CandleStore] = None,
        symbols: Optional[List[str]] = None,
        timeframes: Optional[List[str]] = None,
        resolve_symbol_func: Optional[Callable[[str], str]] = None,
        on_price_update: Optional[Callable[[Dict[str, float]], Any]] = None,
    ):
        self.use_futures = use_futures
        self.provider_name = "binance_futures" if use_futures else "binance_spot"
        self.url = (
            "wss://fstream.binance.com/ws"
            if use_futures
            else "wss://stream.binance.com:9443/ws"
        )
        self.store = store or candle_store
        self.symbols: Set[str] = {s.strip().upper() for s in (symbols or []) if s.strip()}
        self.timeframes: Set[str] = {tf.strip().lower() for tf in (timeframes or ["5m"]) if tf.strip()}
        self.resolve_symbol_func = resolve_symbol_func
        self.on_price_update = on_price_update

        self._running = False
        self._connected = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None and not self._ws.closed

    def update_subscriptions(self, symbols: List[str], timeframes: Optional[List[str]] = None):
        """Adds new symbols/timeframes to stream."""
        for s in symbols:
            self.symbols.add(s.strip().upper())
        if timeframes:
            for tf in timeframes:
                self.timeframes.add(tf.strip().lower())
        if self.is_connected:
            asyncio.create_task(self._send_subscriptions())

    async def start(self):
        """Starts the background WebSocket connection loop."""
        async with self._lock:
            if self._running:
                return
            self._running = True
            self._task = asyncio.create_task(self._reconnect_loop())
            logger.info("BinanceWSClient (%s) background connection started.", self.provider_name)

    async def stop(self):
        """Stops the WebSocket client cleanly."""
        async with self._lock:
            self._running = False
            self._connected = False
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None
            if self._task and not self._task.done():
                self._task.cancel()
            logger.info("BinanceWSClient (%s) stopped.", self.provider_name)

    async def _send_subscriptions(self):
        """Sends subscription requests to Binance WS."""
        if not self.is_connected:
            return

        params = []
        # 1. MiniTicker array for all mids
        if self.use_futures:
            params.append("!miniTicker@arr")

        # 2. Individual kline streams for watchlist
        for sym in self.symbols:
            pair = self.resolve_symbol_func(sym) if self.resolve_symbol_func else f"{sym}USDT"
            clean_pair = pair.lower().replace("-perp", "")
            for tf in self.timeframes:
                params.append(f"{clean_pair}@kline_{tf}")

        if not params:
            return

        # Binance allows up to 200 streams per subscribe call
        for i in range(0, len(params), 100):
            chunk = params[i:i + 100]
            sub_payload = {
                "method": "SUBSCRIBE",
                "params": chunk,
                "id": random.randint(1, 100000),
            }
            try:
                await self._ws.send(json.dumps(sub_payload))
                logger.debug("BinanceWS subscribed: %s", chunk[:3])
            except Exception as exc:
                logger.warning("Failed to subscribe to Binance WS chunk: %s", exc)

    def handle_message(self, message: str):
        """Parses and ingests Binance WebSocket frames."""
        try:
            data = json.loads(message)

            # Case 1: miniTicker array (!miniTicker@arr)
            if isinstance(data, list):
                mids = {}
                for item in data:
                    sym = item.get("s")
                    close_px = item.get("c")
                    if sym and close_px:
                        try:
                            price = float(close_px)
                            mids[sym] = price
                            # Also map base coin if ends in USDT
                            if sym.endswith("USDT"):
                                mids[sym[:-4]] = price
                        except (ValueError, TypeError):
                            continue

                if mids:
                    self.store.set_cached_mids(self.provider_name, mids)
                    if self.on_price_update:
                        try:
                            self.on_price_update(mids)
                        except Exception as cb_exc:
                            logger.debug("Error in Binance WS on_price_update: %s", cb_exc)

            # Case 2: kline stream frame
            elif isinstance(data, dict) and data.get("e") == "kline":
                k = data.get("k", {})
                sym = data.get("s") or k.get("s")
                interval = k.get("i")
                if sym and interval and "t" in k:
                    candle = {
                        "t": int(k["t"]),
                        "o": float(k["o"]),
                        "h": float(k["h"]),
                        "l": float(k["l"]),
                        "c": float(k["c"]),
                        "v": float(k.get("v", 0.0)),
                    }
                    self.store.merge_candles(self.provider_name, sym, interval, [candle])
                    if sym.endswith("USDT"):
                        self.store.merge_candles(self.provider_name, sym[:-4], interval, [candle])

        except Exception as exc:
            logger.debug("Error parsing Binance WS message: %s", exc)

    async def _reconnect_loop(self):
        backoff = 1.0
        while self._running:
            try:
                logger.info("Connecting to Binance WebSocket (%s): %s", self.provider_name, self.url)
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    self._connected = True
                    backoff = 1.0
                    logger.info("Binance WebSocket (%s) connected.", self.provider_name)

                    await self._send_subscriptions()

                    async for msg in ws:
                        if not self._running:
                            break
                        self.handle_message(msg)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._connected = False
                if self._running:
                    wait_time = min(backoff, 30.0) + random.uniform(0.1, 1.0)
                    logger.warning("Binance WS lost (%s). Reconnecting in %.2fs...", exc, wait_time)
                    await asyncio.sleep(wait_time)
                    backoff = min(backoff * 2, 30.0)
            finally:
                self._connected = False
                self._ws = None
