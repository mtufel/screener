"""
Hyperliquid WebSocket Client (market_data/hyperliquid_ws.py)

Streams real-time market data directly from Hyperliquid WebSocket feed (wss://api.hyperliquid.xyz/ws):
- Subscribes to 'allMids' for instant ticker midpoints across all coins with zero HTTP latency.
- Subscribes to 'candle' streams for configured coins & intervals (e.g. 5m, 15m, 1h).
- Streams updates directly into CandleStore.
- Features auto-reconnect with exponential backoff and keepalive ping/pong heartbeats.
"""

import asyncio
import json
import logging
import os
import random
import time
from typing import Any, Callable, Dict, List, Optional, Set
import websockets

from candle_store import CandleStore, candle_store
from hyperliquid_client import resolve_symbol

logger = logging.getLogger("market_data.hyperliquid_ws")
HYPERLIQUID_WS_URL = os.getenv("HYPERLIQUID_WS_URL", "wss://api.hyperliquid.xyz/ws")


REVERSE_ALIASES: Dict[str, List[str]] = {
    "PAXG": ["GOLD", "XAU", "XAUUSD", "PAXGOLD"],
    "SILVER": ["XAG", "XAGUSD"],
    "WTIOIL": ["OIL", "CRUDE"],
}


class HyperliquidWSClient:
    """Resilient async WebSocket client for streaming Hyperliquid market data into CandleStore."""

    def __init__(
        self,
        url: str = HYPERLIQUID_WS_URL,
        store: Optional[CandleStore] = None,
        symbols: Optional[List[str]] = None,
        timeframes: Optional[List[str]] = None,
        on_price_update: Optional[Callable[[Dict[str, float]], Any]] = None,
    ):
        self.url = url
        self.store = store or candle_store
        self.symbols: Set[str] = {s.strip().upper() for s in (symbols or []) if s.strip()}
        self.timeframes: Set[str] = {tf.strip().lower() for tf in (timeframes or ["5m"]) if tf.strip()}
        self.on_price_update = on_price_update

        self._running = False
        self._connected = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
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
        """Starts the persistent background WebSocket connection loop."""
        async with self._lock:
            if self._running:
                return
            self._running = True
            self._task = asyncio.create_task(self._reconnect_loop())
            logger.info("HyperliquidWSClient background connection task started.")

    async def stop(self):
        """Gracefully disconnects and cancels background tasks."""
        async with self._lock:
            self._running = False
            self._connected = False
            if self._ping_task and not self._ping_task.done():
                self._ping_task.cancel()
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None
            if self._task and not self._task.done():
                self._task.cancel()
            logger.info("HyperliquidWSClient stopped.")

    async def _send_subscriptions(self):
        """Dispatches subscription JSON messages to Hyperliquid WebSocket."""
        if not self.is_connected:
            return

        try:
            # 1. Subscribe to allMids
            await self._ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "allMids"}
            }))
            logger.debug("HyperliquidWS: Subscribed to allMids")

            # 2. Subscribe to candle streams for configured symbols and timeframes
            for sym in self.symbols:
                raw_sym = resolve_symbol(sym)
                for tf in self.timeframes:
                    sub_msg = {
                        "method": "subscribe",
                        "subscription": {
                            "type": "candle",
                            "coin": raw_sym,
                            "interval": tf,
                        }
                    }
                    await self._ws.send(json.dumps(sub_msg))
                    logger.debug("HyperliquidWS: Subscribed to candle %s %s", raw_sym, tf)
        except Exception as exc:
            logger.warning("Failed sending subscriptions to Hyperliquid WS: %s", exc)

    async def _ping_loop(self):
        """Periodic keepalive ping to Hyperliquid."""
        while self._running and self.is_connected:
            try:
                await asyncio.sleep(30)
                if self.is_connected:
                    await self._ws.send(json.dumps({"method": "ping"}))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Error sending keepalive ping: %s", exc)
                break

    def handle_message(self, message: str):
        """Parses and ingests incoming WebSocket messages from Hyperliquid."""
        try:
            data = json.loads(message)
            if not isinstance(data, dict):
                return

            channel = data.get("channel")
            if channel == "allMids":
                mids_dict = data.get("data", {}).get("mids", {})
                if mids_dict:
                    normalized_mids = {}
                    for k, v in mids_dict.items():
                        try:
                            price = float(v)
                            normalized_mids[k] = price
                            norm_k = resolve_symbol(k)
                            if norm_k != k:
                                normalized_mids[norm_k] = price
                            for alias in REVERSE_ALIASES.get(k, []):
                                normalized_mids[alias] = price
                            for alias in REVERSE_ALIASES.get(norm_k, []):
                                normalized_mids[alias] = price
                        except (ValueError, TypeError):
                            continue

                    self.store.set_cached_mids("hyperliquid", normalized_mids)
                    if self.on_price_update:
                        try:
                            self.on_price_update(normalized_mids)
                        except Exception as cb_exc:
                            logger.debug("Error in on_price_update callback: %s", cb_exc)

            elif channel == "candle":
                c_data = data.get("data", {})
                coin = c_data.get("s")
                interval = c_data.get("i")
                if coin and interval and "t" in c_data:
                    candle = {
                        "t": int(c_data["t"]),
                        "o": float(c_data["o"]),
                        "h": float(c_data["h"]),
                        "l": float(c_data["l"]),
                        "c": float(c_data["c"]),
                        "v": float(c_data.get("v", 0.0)),
                    }
                    self.store.merge_candles("hyperliquid", coin, interval, [candle])
                    norm_coin = resolve_symbol(coin)
                    if norm_coin != coin:
                        self.store.merge_candles("hyperliquid", norm_coin, interval, [candle])
                    for alias in REVERSE_ALIASES.get(coin, []) + REVERSE_ALIASES.get(norm_coin, []):
                        self.store.merge_candles("hyperliquid", alias, interval, [candle])

            elif channel == "pong":
                pass
        except Exception as exc:
            logger.debug("Error parsing Hyperliquid WS message: %s", exc)

    async def _reconnect_loop(self):
        """Main connection and auto-reconnect loop."""
        backoff = 1.0
        while self._running:
            try:
                logger.info("Connecting to Hyperliquid WebSocket: %s", self.url)
                async with websockets.connect(self.url, ping_interval=None, ping_timeout=None) as ws:
                    self._ws = ws
                    self._connected = True
                    backoff = 1.0
                    logger.info("Hyperliquid WebSocket connected successfully.")

                    await self._send_subscriptions()

                    if self._ping_task and not self._ping_task.done():
                        self._ping_task.cancel()
                    self._ping_task = asyncio.create_task(self._ping_loop())

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
                    logger.warning("Hyperliquid WS connection lost (%s). Reconnecting in %.2fs...", exc, wait_time)
                    await asyncio.sleep(wait_time)
                    backoff = min(backoff * 2, 30.0)
            finally:
                self._connected = False
                self._ws = None
