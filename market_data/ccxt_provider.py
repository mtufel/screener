"""
CCXT & CCXT Pro Unified Market Data Provider (market_data/ccxt_provider.py)

Leverages CCXT and CCXT Pro to support 100+ exchanges (Binance, Bybit, OKX, Hyperliquid, Gate.io, etc.)
under the unified BaseMarketDataProvider interface, streaming ticks and candles directly into CandleStore.
"""

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Set

import ccxt.async_support as ccxt_async
try:
    import ccxt.pro as ccxt_pro
    _HAS_CCXT_PRO = True
except ImportError:
    ccxt_pro = None
    _HAS_CCXT_PRO = False

from market_data.base import BaseMarketDataProvider
from candle_store import CandleStore, candle_store, TIMEFRAME_MS

logger = logging.getLogger("market_data.ccxt")


EXCHANGE_ALIASES: Dict[str, str] = {
    "gateio": "gate",
    "huobi": "htx",
}


class CcxtProvider(BaseMarketDataProvider):
    """
    Unified multi-exchange Market Data Provider backed by CCXT & CCXT Pro.
    Supports asynchronous REST operations and real-time WebSocket streaming.
    """

    def __init__(
        self,
        exchange_id: Optional[str] = None,
        use_pro: bool = True,
        enable_rate_limit: bool = True,
        store: Optional[CandleStore] = None,
        fallback_provider: Optional[BaseMarketDataProvider] = None,
        exchange_config: Optional[Dict[str, Any]] = None,
    ):
        raw_id = (exchange_id or os.getenv("CCXT_EXCHANGE", "binance")).strip().lower()
        self.exchange_id = EXCHANGE_ALIASES.get(raw_id, raw_id)
        self.use_pro = use_pro and _HAS_CCXT_PRO
        self.enable_rate_limit = enable_rate_limit
        self._store = store or candle_store
        self.fallback_provider = fallback_provider

        # Base configuration dictionary
        cfg = {
            "enableRateLimit": self.enable_rate_limit,
            "timeout": 15000,
        }
        if exchange_config:
            cfg.update(exchange_config)

        # 1. Initialize REST exchange instance
        if not hasattr(ccxt_async, self.exchange_id):
            raise ValueError(f"Exchange '{self.exchange_id}' is not supported by ccxt.async_support")
        exchange_cls = getattr(ccxt_async, self.exchange_id)
        self._rest_exchange = exchange_cls(cfg)

        # 2. Initialize CCXT Pro instance if requested and available
        self._pro_exchange = None
        if self.use_pro and hasattr(ccxt_pro, self.exchange_id):
            pro_cls = getattr(ccxt_pro, self.exchange_id)
            self._pro_exchange = pro_cls(cfg)

        # WebSocket lifecycle & subscription state
        self._ws_running = False
        self._ws_connected = False
        self._ws_tasks: List[asyncio.Task] = []
        self._subscribed_symbols: Set[str] = set()
        self._subscribed_timeframes: Set[str] = set()

        # Universe cache
        self._cached_universe: List[str] = []
        self._universe_cache_time: float = 0.0

    @property
    def name(self) -> str:
        return f"ccxt_{self.exchange_id}"

    @property
    def supports_websocket(self) -> bool:
        return self._pro_exchange is not None

    @property
    def is_websocket_connected(self) -> bool:
        return self._ws_running and self._ws_connected

    def resolve_symbol(self, raw_symbol: str) -> str:
        """
        Maps generic or exchange-agnostic symbols into CCXT slash format.
        e.g., 'BTC' -> 'BTC/USDT', 'GOLD' -> 'PAXG/USDT', 'BTCUSDT' -> 'BTC/USDT'
        """
        sym = raw_symbol.strip().upper().replace("-PERP", "")
        if "/" in sym:
            return sym

        if sym.endswith("USDT"):
            sym = sym[:-4]
        elif sym.endswith("USD"):
            sym = sym[:-3]

        aliases = {
            "XAU": "PAXG",
            "GOLD": "PAXG",
            "XAUUSD": "PAXG",
            "XAUT": "XAUT",
            "PAXG": "PAXG",
            "XAG": "XAG",
            "SILVER": "XAG",
            "XAGUSD": "XAG",
            "KPEPE": "1000PEPE",
            "1000PEPE": "1000PEPE",
            "KBONK": "1000BONK",
            "1000BONK": "1000BONK",
            "KFLOKI": "1000FLOKI",
            "1000FLOKI": "1000FLOKI",
            "KSHIB": "1000SHIB",
            "SHIB": "1000SHIB",
            "1000SHIB": "1000SHIB",
        }
        clean = aliases.get(sym, sym)
        return f"{clean}/USDT"

    def normalize_symbol_to_base(self, ccxt_symbol: str) -> str:
        """
        Extracts clean base symbol from CCXT notation.
        e.g., 'BTC/USDT' -> 'BTC', 'BTC/USDT:USDT' -> 'BTC', 'BTCUSDT' -> 'BTC'
        """
        s = ccxt_symbol.upper()
        if ":" in s:
            s = s.split(":")[0]
        if "/" in s:
            return s.split("/")[0]
        if s.endswith("USDT"):
            return s[:-4]
        if s.endswith("USDC"):
            return s[:-4]
        if s.endswith("USD"):
            return s[:-3]
        return s

    async def get_all_mids(self) -> Dict[str, float]:
        """
        Fetches all mid/last prices using CCXT fetch_tickers with local CandleStore caching.
        """
        cached = self._store.get_cached_mids(self.name, ignore_ttl=self.is_websocket_connected)
        if cached is not None:
            return cached

        if self._store.is_rate_limited(self.name):
            logger.warning("[RateLimit] Serving empty mids for %s due to active rate limit cooldown", self.name)
            return {}

        try:
            tickers = await self._rest_exchange.fetch_tickers()
            mids: Dict[str, float] = {}
            for sym, ticker in tickers.items():
                px = ticker.get("last") or ticker.get("close") or ticker.get("bid")
                if px and px > 0:
                    base = self.normalize_symbol_to_base(sym)
                    mids[base] = float(px)
                    mids[sym] = float(px)
                    # Also map standard aliases
                    if base in ("PAXG", "XAU"):
                        mids["GOLD"] = float(px)
                        mids["XAU"] = float(px)
                    elif base == "XAG":
                        mids["SILVER"] = float(px)

            self._store.set_cached_mids(self.name, mids)
            return mids
        except Exception as exc:
            is_rate_limit = "ratelimit" in str(exc).lower() or "429" in str(exc) or "418" in str(exc)
            if is_rate_limit:
                self._store.set_rate_limited(self.name, 60.0)
                logger.warning("[CcxtProvider] Rate limit hit on fetch_tickers for %s: %s", self.name, exc)
            else:
                logger.warning("[CcxtProvider] Failed to fetch tickers for %s: %s", self.name, exc)

            if self.fallback_provider:
                logger.warning(
                    "[ProviderFallback] %s get_all_mids failed -> Delegating to fallback %s",
                    self.name,
                    self.fallback_provider.name,
                )
                fb_mids = await self.fallback_provider.get_all_mids()
                if fb_mids:
                    self._store.set_cached_mids(self.name, fb_mids)
                    return fb_mids

        return {}

    async def get_last_n_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        n: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        Fetches the latest N OHLCV candles, utilizing CandleStore cache when fresh
        and delta fetch sizing when historical data is already loaded.
        """
        # 1. Instant Cache Hit Check
        cached = self._store.get_candles(self.name, symbol, timeframe, n=n)
        if cached and len(cached) >= min(n, 50) and (self.is_websocket_connected or self._store.is_fresh(self.name, symbol, timeframe)):
            logger.info(
                "[CcxtProvider] [CACHE HIT] %s %s -> Serving %d bars from CandleStore memory (0 network calls)",
                symbol,
                timeframe,
                len(cached),
            )
            return cached

        # 2. Rate-Limit Guard
        if self._store.is_rate_limited(self.name):
            if cached:
                logger.warning("[CcxtProvider] [RATE LIMITED] Serving %d cached bars for %s %s", len(cached), symbol, timeframe)
                return cached
            if self.fallback_provider:
                logger.warning(
                    "[ProviderFallback] %s rate limited -> Delegating %s %s to %s",
                    self.name,
                    symbol,
                    timeframe,
                    self.fallback_provider.name,
                )
                fb_candles = await self.fallback_provider.get_last_n_candles(symbol=symbol, timeframe=timeframe, n=n)
                if fb_candles:
                    self._store.merge_candles(self.name, symbol, timeframe, fb_candles)
                    return fb_candles
            return []

        ccxt_sym = self.resolve_symbol(symbol)
        has_bootstrapped = self._store.has_sufficient_candles(self.name, symbol, timeframe, min_count=min(n, 50))
        limit = 5 if has_bootstrapped else min(1000, max(50, n))

        try:
            ohlcv = await self._rest_exchange.fetch_ohlcv(ccxt_sym, timeframe=timeframe, limit=limit)
            if ohlcv and isinstance(ohlcv, list):
                candles: List[Dict[str, Any]] = []
                dur_ms = TIMEFRAME_MS.get(timeframe, 5 * 60 * 1000)
                base_sym = self.normalize_symbol_to_base(ccxt_sym)
                for row in ohlcv:
                    if len(row) < 6 or row[0] is None:
                        continue
                    t_open = int(row[0])
                    t_close = t_open + dur_ms
                    candles.append({
                        "t": t_open,
                        "T": t_close,
                        "s": base_sym,
                        "i": timeframe,
                        "o": float(row[1]),
                        "h": float(row[2]),
                        "l": float(row[3]),
                        "c": float(row[4]),
                        "v": float(row[5]) if row[5] is not None else 0.0,
                        "n": 0,
                    })
                self._store.merge_candles(self.name, symbol, timeframe, candles)
                return self._store.get_candles(self.name, symbol, timeframe, n=n) or candles[-n:]
        except Exception as exc:
            is_rate_limit = "ratelimit" in str(exc).lower() or "429" in str(exc) or "418" in str(exc)
            if is_rate_limit:
                self._store.set_rate_limited(self.name, 60.0)
                logger.warning("[CcxtProvider] Rate limit hit on fetch_ohlcv for %s %s: %s", symbol, timeframe, exc)
            else:
                logger.warning("[CcxtProvider] Failed to fetch OHLCV for %s %s: %s", symbol, timeframe, exc)

            if cached:
                return cached
            if self.fallback_provider:
                logger.warning(
                    "[ProviderFallback] %s fetch_ohlcv failed -> Delegating %s %s to %s",
                    self.name,
                    symbol,
                    timeframe,
                    self.fallback_provider.name,
                )
                fb_candles = await self.fallback_provider.get_last_n_candles(symbol=symbol, timeframe=timeframe, n=n)
                if fb_candles:
                    self._store.merge_candles(self.name, symbol, timeframe, fb_candles)
                    return fb_candles

        return cached or []

    async def get_historical_candles_range(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Dict[str, Any]]:
        """
        Fetches historical candles over an explicit milliseconds range with CCXT pagination.
        """
        ccxt_sym = self.resolve_symbol(coin)
        base_sym = self.normalize_symbol_to_base(ccxt_sym)
        dur_ms = TIMEFRAME_MS.get(interval, 5 * 60 * 1000)

        all_candles: List[Dict[str, Any]] = []
        current_since = start_time_ms
        limit = 1000

        while current_since < end_time_ms:
            try:
                batch = await self._rest_exchange.fetch_ohlcv(
                    ccxt_sym,
                    timeframe=interval,
                    since=current_since,
                    limit=limit,
                )
                if not batch:
                    break

                new_count = 0
                for row in batch:
                    if len(row) < 6 or row[0] is None:
                        continue
                    t_open = int(row[0])
                    if t_open > end_time_ms:
                        break
                    if t_open >= start_time_ms:
                        t_close = t_open + dur_ms
                        all_candles.append({
                            "t": t_open,
                            "T": t_close,
                            "s": base_sym,
                            "i": interval,
                            "o": float(row[1]),
                            "h": float(row[2]),
                            "l": float(row[3]),
                            "c": float(row[4]),
                            "v": float(row[5]) if row[5] is not None else 0.0,
                            "n": 0,
                        })
                        new_count += 1
                    current_since = max(current_since, t_open + dur_ms)

                if new_count == 0 or len(batch) < limit:
                    break
                await asyncio.sleep(0.05)
            except Exception as exc:
                logger.warning("[CcxtProvider] Error in historical range fetch for %s %s: %s", coin, interval, exc)
                break

        return all_candles

    async def get_universe_coins(self, min_volume: float = 0.0) -> List[str]:
        """
        Returns active tradable coin universe sorted by 24h quote volume.
        """
        now = time.time()
        if self._cached_universe and (now - self._universe_cache_time) < 3600.0:
            return self._cached_universe

        try:
            tickers = await self._rest_exchange.fetch_tickers()
            volume_by_base: Dict[str, float] = {}
            for sym, data in tickers.items():
                if "/USDT" in sym or sym.endswith("USDT"):
                    base = self.normalize_symbol_to_base(sym)
                    vol = float(data.get("quoteVolume") or data.get("baseVolume") or 0.0)
                    if vol >= min_volume:
                        volume_by_base[base] = max(volume_by_base.get(base, 0.0), vol)

            sorted_coins = sorted(volume_by_base.keys(), key=lambda c: volume_by_base[c], reverse=True)
            if sorted_coins:
                self._cached_universe = sorted_coins
                self._universe_cache_time = now
                return sorted_coins
        except Exception as exc:
            logger.warning("[CcxtProvider] Failed to fetch universe coins for %s: %s", self.name, exc)

        return self._cached_universe or ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "PAXG"]

    async def start_websocket(
        self,
        symbols: Optional[List[str]] = None,
        timeframes: Optional[List[str]] = None,
    ) -> bool:
        """
        Starts real-time WebSocket streaming via CCXT Pro into CandleStore.
        """
        if not self.supports_websocket:
            logger.warning("[CcxtProvider] CCXT Pro is not supported or available for %s", self.exchange_id)
            return False

        target_symbols = symbols or ["BTC", "ETH", "SOL", "GOLD"]
        target_timeframes = timeframes or ["5m", "15m", "1h", "4h"]

        self._subscribed_symbols.update(target_symbols)
        self._subscribed_timeframes.update(target_timeframes)

        if self._ws_running:
            return True

        self._ws_running = True
        self._ws_connected = True

        # Launch ticker watching task(s)
        has_watch_tickers = bool(getattr(self._pro_exchange, "has", {}).get("watchTickers"))
        if has_watch_tickers:
            t_task = asyncio.create_task(self._watch_tickers_loop())
            self._ws_tasks.append(t_task)
        else:
            for sym in list(self._subscribed_symbols):
                t_task = asyncio.create_task(self._watch_single_ticker_loop(sym))
                self._ws_tasks.append(t_task)

        # Launch OHLCV watching tasks for each symbol & timeframe
        for sym in list(self._subscribed_symbols):
            for tf in list(self._subscribed_timeframes):
                task = asyncio.create_task(self._watch_ohlcv_loop(sym, tf))
                self._ws_tasks.append(task)

        logger.info(
            "[CcxtProvider] CCXT Pro WebSocket streaming started for %s with %d tasks",
            self.name,
            len(self._ws_tasks),
        )
        return True

    async def _watch_tickers_loop(self):
        """Background loop streaming ticker updates into CandleStore for exchanges supporting batch watchTickers."""
        while self._ws_running and self._pro_exchange:
            try:
                resolved_symbols = [self.resolve_symbol(s) for s in self._subscribed_symbols]
                tickers = await self._pro_exchange.watch_tickers(resolved_symbols)
                mids: Dict[str, float] = {}
                for sym, ticker in tickers.items():
                    px = ticker.get("last") or ticker.get("close") or ticker.get("bid")
                    if px and px > 0:
                        base = self.normalize_symbol_to_base(sym)
                        mids[base] = float(px)
                        mids[sym] = float(px)
                        if base in ("PAXG", "XAU"):
                            mids["GOLD"] = float(px)
                            mids["XAU"] = float(px)
                        elif base == "XAG":
                            mids["SILVER"] = float(px)
                if mids:
                    self._store.set_cached_mids(self.name, mids)
                self._ws_connected = True
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._ws_connected = False
                logger.warning("[CcxtProvider] Error in watch_tickers loop: %s. Reconnecting in 2s...", exc)
                await asyncio.sleep(2.0)

    async def _watch_single_ticker_loop(self, symbol: str):
        """Background loop streaming ticker updates for a single symbol (for exchanges like BingX without batch watchTickers)."""
        ccxt_sym = self.resolve_symbol(symbol)
        base_sym = self.normalize_symbol_to_base(ccxt_sym)
        while self._ws_running and self._pro_exchange:
            try:
                ticker = await self._pro_exchange.watch_ticker(ccxt_sym)
                px = ticker.get("last") or ticker.get("close") or ticker.get("bid")
                if px and px > 0:
                    mids = self._store.get_cached_mids(self.name, ignore_ttl=True) or {}
                    mids[base_sym] = float(px)
                    mids[ccxt_sym] = float(px)
                    if base_sym in ("PAXG", "XAU"):
                        mids["GOLD"] = float(px)
                        mids["XAU"] = float(px)
                    elif base_sym == "XAG":
                        mids["SILVER"] = float(px)
                    self._store.set_cached_mids(self.name, mids)
                self._ws_connected = True
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._ws_connected = False
                logger.debug("[CcxtProvider] Error in watch_ticker loop for %s: %s. Reconnecting in 2s...", ccxt_sym, exc)
                await asyncio.sleep(2.0)

    async def _watch_ohlcv_loop(self, symbol: str, timeframe: str):
        """Background loop streaming OHLCV candle updates into CandleStore."""
        ccxt_sym = self.resolve_symbol(symbol)
        base_sym = self.normalize_symbol_to_base(ccxt_sym)
        dur_ms = TIMEFRAME_MS.get(timeframe, 5 * 60 * 1000)

        while self._ws_running and self._pro_exchange:
            try:
                ohlcv = await self._pro_exchange.watch_ohlcv(ccxt_sym, timeframe)
                if ohlcv and isinstance(ohlcv, list):
                    candles: List[Dict[str, Any]] = []
                    for row in ohlcv:
                        if len(row) < 6 or row[0] is None:
                            continue
                        t_open = int(row[0])
                        t_close = t_open + dur_ms
                        candles.append({
                            "t": t_open,
                            "T": t_close,
                            "s": base_sym,
                            "i": timeframe,
                            "o": float(row[1]),
                            "h": float(row[2]),
                            "l": float(row[3]),
                            "c": float(row[4]),
                            "v": float(row[5]) if row[5] is not None else 0.0,
                            "n": 0,
                        })
                    self._store.merge_candles(self.name, symbol, timeframe, candles)
                self._ws_connected = True
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[CcxtProvider] Error watching OHLCV for %s %s: %s. Reconnecting in 2s...", symbol, timeframe, exc)
                await asyncio.sleep(2.0)

    async def stop_websocket(self):
        """Gracefully cancels all active CCXT Pro streaming tasks."""
        self._ws_running = False
        self._ws_connected = False

        for task in self._ws_tasks:
            if not task.done():
                task.cancel()

        if self._ws_tasks:
            await asyncio.gather(*self._ws_tasks, return_exceptions=True)
            self._ws_tasks.clear()

        if self._pro_exchange:
            try:
                await self._pro_exchange.close()
            except Exception as exc:
                logger.debug("[CcxtProvider] Error closing pro exchange: %s", exc)

    async def close(self):
        """Closes all underlying CCXT REST and Pro connections."""
        await self.stop_websocket()
        if self._rest_exchange:
            try:
                await self._rest_exchange.close()
            except Exception as exc:
                logger.debug("[CcxtProvider] Error closing rest exchange: %s", exc)
