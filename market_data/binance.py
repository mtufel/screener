"""
Binance Market Data Provider (market_data/binance.py)
Supports Binance USDT-M Futures and Binance Spot.
"""

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional
import httpx

from market_data.base import BaseMarketDataProvider
from candle_store import CandleStore, candle_store, TIMEFRAME_MS

logger = logging.getLogger("market_data.binance")


class BinanceProvider(BaseMarketDataProvider):
    """
    Binance Market Data Provider.
    Primary: Binance USDT-M Futures (https://fapi.binance.com)
    Fallback/Spot: Binance Spot (https://api.binance.com)
    """

    def __init__(
        self,
        use_futures: bool = True,
        api_url: Optional[str] = None,
        timeout: float = 15.0,
        store: Optional[CandleStore] = None,
    ):
        self.use_futures = use_futures
        default_url = "https://fapi.binance.com" if use_futures else "https://api.binance.com"
        self.api_url = (api_url or os.getenv("BINANCE_API_URL", default_url)).rstrip("/")
        self.timeout = timeout
        self._store = store or candle_store
        self._http_client: Optional[httpx.AsyncClient] = None
        self._cached_universe: List[str] = []
        self._universe_cache_time = 0.0

    @property
    def name(self) -> str:
        return "binance_futures" if self.use_futures else "binance_spot"

    def _get_http(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=5.0))
        return self._http_client

    @property
    def _http(self) -> httpx.AsyncClient:
        return self._get_http()

    def resolve_symbol(self, raw_symbol: str) -> str:
        """Maps user symbol to Binance USDT pair."""
        sym = raw_symbol.strip().upper().replace("-PERP", "")
        if sym.endswith("USDT"):
            sym = sym[:-4]
        elif sym.endswith("USD"):
            sym = sym[:-3]

        aliases = {
            "XAU": "XAU",
            "GOLD": "XAU",
            "XAUUSD": "XAU",
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
        return f"{clean}USDT"

    def normalize_symbol_to_base(self, binance_symbol: str) -> str:
        """Converts BTCUSDT -> BTC, XAUUSDT -> XAU, PAXGUSDT -> PAXG."""
        s = binance_symbol.upper()
        if s.endswith("USDT"):
            return s[:-4]
        if s.endswith("USDC"):
            return s[:-4]
        if s.endswith("BUSD"):
            return s[:-4]
        return s

    async def get_all_mids(self) -> Dict[str, float]:
        """Fetches all ticker prices in a single bulk request with local caching and rate-limit guard."""
        cached = self._store.get_cached_mids(self.name)
        if cached is not None:
            return cached

        if self._store.is_rate_limited(self.name):
            logger.warning("[RateLimit] Serving empty mids for %s due to active rate limit cooldown", self.name)
            return {}

        client = self._get_http()
        endpoint = "/fapi/v1/ticker/price" if self.use_futures else "/api/v3/ticker/price"
        url = f"{self.api_url}{endpoint}"

        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                mids: Dict[str, float] = {}
                for item in data:
                    sym = item.get("symbol", "")
                    if sym.endswith("USDT"):
                        base = self.normalize_symbol_to_base(sym)
                        try:
                            px = float(item.get("price", 0.0))
                            if px > 0:
                                mids[base] = px
                                mids[sym] = px
                                if base == "XAU":
                                    mids["GOLD"] = px
                                elif base == "XAG":
                                    mids["SILVER"] = px
                        except (ValueError, TypeError):
                            continue
                self._store.set_cached_mids(self.name, mids)
                return mids
            elif resp.status_code in (418, 429):
                self._store.set_rate_limited(self.name, 60.0)
                logger.warning("Binance ticker/price hit rate limit (HTTP %d): %s", resp.status_code, resp.text[:200])
            else:
                logger.warning("Binance ticker/price returned status %d: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Failed to fetch Binance mid prices: %s", exc)
        return {}

    async def get_last_n_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        n: int = 200,
    ) -> List[Dict[str, Any]]:
        """Fetches latest N candles for symbol and timeframe with delta updates and in-memory store."""
        # 1. Instant Cache Hit Check
        cached = self._store.get_candles(self.name, symbol, timeframe, n=n)
        if cached and len(cached) >= min(n, 50) and self._store.is_fresh(self.name, symbol, timeframe):
            logger.info("[BinanceProvider] [CACHE HIT] %s %s -> Serving %d bars from CandleStore memory (0 network calls)", symbol, timeframe, len(cached))
            return cached

        # 2. Rate-Limit Guard
        if self._store.is_rate_limited(self.name):
            if cached:
                logger.warning("[BinanceProvider] [RATE LIMITED] Serving %d cached bars for %s %s", len(cached), symbol, timeframe)
                return cached
            logger.warning("[BinanceProvider] [RATE LIMITED] Rate limited and no cache available for %s %s", symbol, timeframe)
            return []

        binance_sym = self.resolve_symbol(symbol)
        client = self._get_http()
        endpoint = "/fapi/v1/klines" if self.use_futures else "/api/v3/klines"
        url = f"{self.api_url}{endpoint}"

        # 3. Delta vs Bootstrap Query Sizing
        has_bootstrapped = self._store.has_sufficient_candles(self.name, symbol, timeframe, min_count=min(n, 50))
        limit = 5 if has_bootstrapped else min(1000, max(50, n))

        if has_bootstrapped:
            logger.info("[BinanceProvider] [DELTA FETCH] %s (%s) %s -> Querying limit=5 latest delta candles via REST", symbol, binance_sym, timeframe)
        else:
            logger.info("[BinanceProvider] [BOOTSTRAP FETCH] %s (%s) %s -> Bootstrapping history with limit=%d candles via REST", symbol, binance_sym, timeframe, limit)

        params = {
            "symbol": binance_sym,
            "interval": timeframe,
            "limit": limit,
        }

        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                raw = resp.json()
                if isinstance(raw, list):
                    candles = []
                    base_sym = self.normalize_symbol_to_base(binance_sym)
                    dur_ms = TIMEFRAME_MS.get(timeframe, 5 * 60 * 1000)
                    for row in raw:
                        t_open = int(row[0])
                        t_close = int(row[6]) if len(row) > 6 else (t_open + dur_ms)
                        trades_count = int(row[8]) if len(row) > 8 else 0
                        candles.append({
                            "t": t_open,
                            "T": t_close,
                            "s": base_sym,
                            "i": timeframe,
                            "o": float(row[1]),
                            "h": float(row[2]),
                            "l": float(row[3]),
                            "c": float(row[4]),
                            "v": float(row[5]),
                            "n": trades_count,
                        })
                    self._store.merge_candles(self.name, symbol, timeframe, candles)
                    return self._store.get_candles(self.name, symbol, timeframe, n=n) or candles[-n:]
            elif resp.status_code in (418, 429):
                self._store.set_rate_limited(self.name, 60.0)
                logger.warning("[BinanceProvider] [HTTP %d] Rate limit hit for %s (%s): %s", resp.status_code, symbol, timeframe, resp.text[:200])
                return self._store.get_candles(self.name, symbol, timeframe, n=n) or []
            elif resp.status_code == 400 and self.use_futures:
                logger.info("[BinanceProvider] Symbol %s not found on Futures, falling back to Binance Spot klines", binance_sym)
                spot_url = f"https://api.binance.com/api/v3/klines"
                resp_spot = await client.get(spot_url, params=params)
                if resp_spot.status_code == 200:
                    raw = resp_spot.json()
                    candles = []
                    base_sym = self.normalize_symbol_to_base(binance_sym)
                    dur_ms = TIMEFRAME_MS.get(timeframe, 5 * 60 * 1000)
                    for row in raw:
                        t_open = int(row[0])
                        t_close = int(row[6]) if len(row) > 6 else (t_open + dur_ms)
                        trades_count = int(row[8]) if len(row) > 8 else 0
                        candles.append({
                            "t": t_open,
                            "T": t_close,
                            "s": base_sym,
                            "i": timeframe,
                            "o": float(row[1]),
                            "h": float(row[2]),
                            "l": float(row[3]),
                            "c": float(row[4]),
                            "v": float(row[5]),
                            "n": trades_count,
                        })
                    self._store.merge_candles(self.name, symbol, timeframe, candles)
                    return self._store.get_candles(self.name, symbol, timeframe, n=n) or candles[-n:]
                elif resp_spot.status_code in (418, 429):
                    self._store.set_rate_limited(self.name, 60.0)
                    logger.warning("[BinanceProvider] Binance spot klines hit rate limit (HTTP %d) for %s (%s)", resp_spot.status_code, symbol, timeframe)
                    return self._store.get_candles(self.name, symbol, timeframe, n=n) or []
        except Exception as exc:
            logger.warning("[BinanceProvider] get_last_n_candles failed for %s (%s): %s", symbol, timeframe, exc)

        return self._store.get_candles(self.name, symbol, timeframe, n=n) or []

    async def get_historical_candles_range(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Dict[str, Any]]:
        """Deep historical kline fetching across multi-thousand candle ranges with pagination."""
        binance_sym = self.resolve_symbol(coin)
        client = self._get_http()
        endpoint = "/fapi/v1/klines" if self.use_futures else "/api/v3/klines"
        url = f"{self.api_url}{endpoint}"

        step_ms = TIMEFRAME_MS.get(interval, 5 * 60 * 1000)
        all_candles: List[Dict[str, Any]] = []
        curr_start = start_time_ms

        while curr_start < end_time_ms:
            curr_end = min(curr_start + (1000 * step_ms), end_time_ms)
            params = {
                "symbol": binance_sym,
                "interval": interval,
                "startTime": curr_start,
                "endTime": curr_end,
                "limit": 1000,
            }

            raw = None
            for attempt in range(1, 4):
                try:
                    resp = await client.get(url, params=params)
                    if resp.status_code == 200:
                        raw = resp.json()
                        break
                    elif resp.status_code == 400 and self.use_futures:
                        # Try Spot API fallback
                        spot_resp = await client.get("https://api.binance.com/api/v3/klines", params=params)
                        if spot_resp.status_code == 200:
                            raw = spot_resp.json()
                            break
                    elif resp.status_code == 429:
                        await asyncio.sleep(1.5 * attempt)
                    else:
                        await asyncio.sleep(0.5 * attempt)
                except Exception:
                    await asyncio.sleep(0.5 * attempt)

            if not raw or not isinstance(raw, list):
                break

            base_sym = self.normalize_symbol_to_base(binance_sym)
            for row in raw:
                t_open = int(row[0])
                t_close = int(row[6]) if len(row) > 6 else (t_open + step_ms)
                trades_count = int(row[8]) if len(row) > 8 else 0
                all_candles.append({
                    "t": t_open,
                    "T": t_close,
                    "s": base_sym,
                    "i": interval,
                    "o": float(row[1]),
                    "h": float(row[2]),
                    "l": float(row[3]),
                    "c": float(row[4]),
                    "v": float(row[5]),
                    "n": trades_count,
                })

            last_open = int(raw[-1][0])
            if last_open <= curr_start:
                break
            curr_start = last_open + step_ms
            await asyncio.sleep(0.03)

        return all_candles

    async def get_universe_coins(self, min_volume: float = 0.0) -> List[str]:
        """Fetches active USDT pairs ranked by 24h volume."""
        now = time.monotonic()
        if self._cached_universe and (now - self._universe_cache_time) < 300:
            return list(self._cached_universe)

        client = self._get_http()
        endpoint = "/fapi/v1/ticker/24hr" if self.use_futures else "/api/v3/ticker/24hr"
        url = f"{self.api_url}{endpoint}"

        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                pairs = []
                for item in data:
                    sym = item.get("symbol", "")
                    if sym.endswith("USDT"):
                        vol = float(item.get("quoteVolume", 0.0))
                        if vol >= min_volume:
                            base = self.normalize_symbol_to_base(sym)
                            pairs.append((base, vol))
                pairs.sort(key=lambda x: x[1], reverse=True)
                coins = [p[0] for p in pairs]
                self._cached_universe = coins
                self._universe_cache_time = now
                return coins
        except Exception as exc:
            logger.warning("Failed to fetch Binance universe: %s", exc)

        return ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "PAXG"]

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
