"""
Hyperliquid API Client for fetching universe metadata, live mid prices, and historical OHLCV candle data.
Features a Token-Bucket Async Rate Limiter, Global 429 Cooldown Coordinator,
HTTP 429 / Retry-After handling, exponential backoff with jitter, and universe caching.
"""

import asyncio
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

HYPERLIQUID_API_URL = os.getenv("HYPERLIQUID_API_URL", "https://api.hyperliquid.xyz/info")
MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "5"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20.0"))
RATE_LIMIT_RPS = float(os.getenv("RATE_LIMIT_RPS", "5.0"))  # Sustained requests per second

# Symbol aliases for commodities and alternative naming
SYMBOL_ALIASES: Dict[str, str] = {
    "GOLD": "PAXG",
    "XAU": "PAXG",
    "XAUUSD": "PAXG",
    "PAXGOLD": "PAXG",
    "SILVER": "SILVER",
    "XAG": "SILVER",
    "XAGUSD": "SILVER",
    "OIL": "WTIOIL",
    "CRUDE": "WTIOIL",
}


def resolve_symbol(symbol: str) -> str:
    """Normalizes symbol by stripping PERP/USDT and applying commodity aliases."""
    clean = symbol.strip().upper().replace("-PERP", "").replace("USDT", "").replace("USD", "")
    return SYMBOL_ALIASES.get(symbol.strip().upper(), SYMBOL_ALIASES.get(clean, clean))


class AsyncRateLimiter:
    """
    Token-bucket async rate limiter with global 429 cooldown coordination.
    Smoothly paces outgoing requests and coordinates full client pause upon 429 events.
    """

    def __init__(self, rate_per_sec: float = 5.0, max_burst: Optional[float] = None):
        self.rate_per_sec = max(1.0, float(rate_per_sec))
        self.max_tokens = float(max_burst or self.rate_per_sec)
        self.tokens = self.max_tokens
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()
        self._cooldown_until = 0.0

    async def acquire(self) -> None:
        """Wait until token is available and cooldown has expired."""
        while True:
            # Check global 429 cooldown
            now = time.monotonic()
            if now < self._cooldown_until:
                wait_needed = self._cooldown_until - now
                logger.debug("RateLimiter pausing for global cooldown: %.2fs remaining", wait_needed)
                await asyncio.sleep(wait_needed)

            async with self._lock:
                now = time.monotonic()
                if now < self._cooldown_until:
                    continue

                elapsed = now - self.last_update
                self.last_update = now

                # Refill tokens
                self.tokens = min(self.max_tokens, self.tokens + (elapsed * self.rate_per_sec))

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return

                # Calculate sleep time for next token
                needed = 1.0 - self.tokens
                sleep_time = needed / self.rate_per_sec

            await asyncio.sleep(sleep_time)

    def trigger_cooldown(self, seconds: float) -> None:
        """Coordinate a global pause across all concurrent requests when a 429 occurs."""
        now = time.monotonic()
        target = now + max(2.0, seconds)
        if target > self._cooldown_until:
            self._cooldown_until = target
            logger.warning("Global rate limit cooldown triggered for %.1f seconds.", seconds)


class HyperliquidClient:
    """Async client for interacting with Hyperliquid public Info API with robust rate limiting."""

    def __init__(
        self,
        base_url: str = HYPERLIQUID_API_URL,
        max_concurrent: int = MAX_CONCURRENT_REQUESTS,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        rate_per_sec: float = RATE_LIMIT_RPS,
    ):
        self.base_url = base_url
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = AsyncRateLimiter(rate_per_sec=rate_per_sec, max_burst=rate_per_sec)
        self.timeout = httpx.Timeout(timeout, connect=6.0)
        self._client: Optional[httpx.AsyncClient] = None
        self._cached_universe: List[str] = []
        self._cached_universe_time: float = 0.0
        self._cache_ttl_seconds: float = 900.0  # 15 minutes cache for static meta

    def get_hl_symbol(self, symbol: str) -> str:
        """Returns the canonical Hyperliquid perp coin name for any alias or user symbol."""
        return resolve_symbol(symbol)

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily initialize and reuse the httpx.AsyncClient session."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                },
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=30),
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _post_info(self, payload: Dict[str, Any], retries: int = 5) -> Any:
        """
        Execute an HTTP POST request to Hyperliquid Info endpoint with:
        - Token-bucket rate limiting
        - Global 429 cooldown coordination
        - Semaphore concurrency bounding
        - Exponential backoff with random jitter
        """
        client = await self._get_client()

        for attempt in range(1, retries + 1):
            # 1. Paced token acquisition
            await self.rate_limiter.acquire()

            # 2. Concurrency bound
            async with self.semaphore:
                try:
                    response = await client.post(self.base_url, json=payload)

                    # Handle 429 Too Many Requests specifically
                    if response.status_code == 429:
                        retry_after_hdr = response.headers.get("Retry-After")
                        if retry_after_hdr and retry_after_hdr.isdigit():
                            wait_time = float(retry_after_hdr) + random.uniform(0.5, 1.5)
                        else:
                            # 429 cooldown: 4s, 8s, 16s...
                            wait_time = (4.0 * (2 ** (attempt - 1))) + random.uniform(0.5, 2.0)

                        logger.warning(
                            "Hyperliquid 429 Rate Limit hit (attempt %d/%d). Cooling down for %.2fs. Payload=%s",
                            attempt,
                            retries,
                            wait_time,
                            payload.get("type"),
                        )
                        # Notify rate limiter to pause all other concurrent tasks
                        self.rate_limiter.trigger_cooldown(wait_time)

                        if attempt == retries:
                            response.raise_for_status()
                        await asyncio.sleep(wait_time)
                        continue

                    # Raise for other HTTP error codes (e.g. 500, 502, 503)
                    response.raise_for_status()
                    return response.json()

                except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                    status_code = getattr(getattr(exc, "response", None), "status_code", None)
                    logger.warning(
                        "Hyperliquid request error (attempt %d/%d, status=%s): payload=%s error=%s",
                        attempt,
                        retries,
                        status_code,
                        payload.get("type"),
                        str(exc),
                    )

                    if attempt == retries:
                        logger.error(
                            "Hyperliquid request permanently failed after %d retries. Payload: %s",
                            retries,
                            payload,
                        )
                        raise

                    backoff = (1.0 * (2 ** (attempt - 1))) + random.uniform(0.1, 0.5)
                    await asyncio.sleep(backoff)

    async def get_universe(self, use_cache: bool = True) -> List[str]:
        """
        Fetch the list of active perpetual coin symbols (excluding delisted coins).
        Caches the universe for 15 minutes to eliminate redundant requests.

        Returns:
            List[str]: List of active symbols, e.g. ["BTC", "ETH", "SOL", ...]
        """
        now = time.time()
        if use_cache and self._cached_universe and (now - self._cached_universe_time < self._cache_ttl_seconds):
            return list(self._cached_universe)

        try:
            data = await self._post_info({"type": "meta"})
            universe_meta = data.get("universe", [])
            # Filter out delisted coins
            active_coins = [
                item["name"]
                for item in universe_meta
                if "name" in item and not item.get("isDelisted", False)
            ]
            self._cached_universe = active_coins
            self._cached_universe_time = now
            logger.info("Fetched %d active coins from Hyperliquid universe (%d total perpetuals).", len(active_coins), len(universe_meta))
            return list(self._cached_universe)
        except Exception as exc:
            logger.error("Failed to fetch universe from Hyperliquid: %s", exc)
            return list(self._cached_universe)

    async def get_all_mids(self) -> Dict[str, float]:
        """
        Fetch current mid prices for all coins in a single call.

        Returns:
            Dict[str, float]: Mapping of symbol -> current mid price.
        """
        try:
            data = await self._post_info({"type": "allMids"})
            if isinstance(data, dict):
                return {k: float(v) for k, v in data.items() if v is not None}
            return {}
        except Exception as exc:
            logger.error("Failed to fetch allMids from Hyperliquid: %s", exc)
            return {}

    async def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get the current price for a specific symbol.

        Args:
            symbol (str): Coin symbol e.g. "BTC"

        Returns:
            Optional[float]: Price or None if unavailable.
        """
        resolved_sym = SYMBOL_ALIASES.get(symbol.upper(), symbol)
        all_mids = await self.get_all_mids()
        return all_mids.get(resolved_sym) or all_mids.get(symbol)

    async def fetch_fallback_historical_klines(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Dict[str, Any]]:
        """
        Fallback fetcher using Binance public Kline API for extended historical lookbacks
        when Hyperliquid rolling node snapshot cache is exhausted (e.g. pre-Aug 13 for 5m).
        """
        if coin.upper() == "WTIOIL":
            # Binance does not offer WTI Crude Oil; avoid mapping to unrelated stablecoins
            return []

        sym_map = {
            "BTC": "BTCUSDT",
            "ETH": "ETHUSDT",
            "SOL": "SOLUSDT",
            "PAXG": "PAXGUSDT",
            "GOLD": "PAXGUSDT",
            "SILVER": "XAGUSDT",
        }
        binance_symbol = sym_map.get(coin.upper(), f"{coin.upper()}USDT")
        interval_ms_map = {
            "1m": 60 * 1000,
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
            "1d": 24 * 60 * 60 * 1000,
        }
        step_ms = interval_ms_map.get(interval, 5 * 60 * 1000)
        all_candles: List[Dict[str, Any]] = []
        curr_start = start_time_ms

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                while curr_start < end_time_ms:
                    curr_end = min(curr_start + (1000 * step_ms), end_time_ms)
                    url = f"https://api.binance.com/api/v3/klines?symbol={binance_symbol}&interval={interval}&startTime={curr_start}&endTime={curr_end}&limit=1000"
                    res = await client.get(url)
                    if res.status_code != 200:
                        break
                    raw = res.json()
                    if not raw or not isinstance(raw, list):
                        break
                    for row in raw:
                        all_candles.append({
                            "t": int(row[0]),
                            "T": int(row[6]),
                            "s": coin,
                            "i": interval,
                            "o": float(row[1]),
                            "h": float(row[2]),
                            "l": float(row[3]),
                            "c": float(row[4]),
                            "v": float(row[5]),
                            "n": int(row[8]),
                        })
                    last_open = int(raw[-1][0])
                    if last_open <= curr_start:
                        break
                    curr_start = last_open + step_ms
                    await asyncio.sleep(0.04)
        except Exception as exc:
            logger.warning("Historical Kline fallback fetch error for %s (%s): %s", coin, interval, exc)

        return all_candles

    async def get_candle_snapshot(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Dict[str, Any]]:
        """
        Fetch OHLCV candle snapshots for a coin and interval.
        Automatically chunks long timeframes and falls back to deep history if Hyperliquid's
        5,000-candle live cache does not span the requested start_time.

        Args:
            coin (str): Coin symbol, e.g. "BTC"
            interval (str): Timeframe, e.g. "1m", "5m", "15m", "1h", "4h", "1d"
            start_time_ms (int): Start timestamp in milliseconds
            end_time_ms (int): End timestamp in milliseconds

        Returns:
            List[Dict[str, Any]]: List of candle dicts sorted chronologically
        """
        resolved_coin = SYMBOL_ALIASES.get(coin.upper(), coin)
        interval_ms_map = {
            "1m": 60 * 1000,
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
            "1d": 24 * 60 * 60 * 1000,
        }
        step_ms = interval_ms_map.get(interval, 5 * 60 * 1000)
        chunk_candle_limit = 3500
        chunk_duration_ms = chunk_candle_limit * step_ms

        all_candles_map: Dict[int, Dict[str, Any]] = {}

        # 1. Single chunk fast path
        if end_time_ms - start_time_ms <= chunk_duration_ms:
            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": resolved_coin,
                    "interval": interval,
                    "startTime": start_time_ms,
                    "endTime": end_time_ms,
                },
            }
            try:
                candles = await self._post_info(payload)
                if isinstance(candles, list) and len(candles) > 0:
                    for c in candles:
                        all_candles_map[c.get("t", 0)] = c
            except Exception as exc:
                logger.warning("Failed to fetch candles for %s (%s): %s", coin, interval, exc)
        else:
            # 2. Iterate in chunks across Hyperliquid
            curr_start = start_time_ms
            while curr_start < end_time_ms:
                curr_end = min(curr_start + chunk_duration_ms, end_time_ms)
                payload = {
                    "type": "candleSnapshot",
                    "req": {
                        "coin": resolved_coin,
                        "interval": interval,
                        "startTime": curr_start,
                        "endTime": curr_end,
                    },
                }
                try:
                    chunk = await self._post_info(payload)
                    if isinstance(chunk, list):
                        for c in chunk:
                            t = c.get("t", 0)
                            if t not in all_candles_map:
                                all_candles_map[t] = c
                except Exception as exc:
                    logger.warning("Failed to fetch chunk for %s (%s) [%d - %d]: %s", coin, interval, curr_start, curr_end, exc)

                curr_start = curr_end + 1
                await asyncio.sleep(0.05)

        # 3. Check if we need historical fallback for older period (e.g. pre-Aug 13th for 5m)
        earliest_hl_time = min(all_candles_map.keys()) if all_candles_map else end_time_ms
        if earliest_hl_time - start_time_ms > 2 * step_ms:
            logger.info(
                "Hyperliquid missing older %s candles for %s between %d and %d. Querying deep history archive...",
                interval,
                coin,
                start_time_ms,
                earliest_hl_time,
            )
            fallback_candles = await self.fetch_fallback_historical_klines(
                coin=coin,
                interval=interval,
                start_time_ms=start_time_ms,
                end_time_ms=earliest_hl_time,
            )
            for c in fallback_candles:
                t = c.get("t", 0)
                if t not in all_candles_map:
                    all_candles_map[t] = c

        sorted_candles = sorted(all_candles_map.values(), key=lambda x: x.get("t", 0))
        return sorted_candles

    async def get_last_n_candles(
        self,
        symbol: str,
        timeframe: str,
        n: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Fetch the last n candles for a symbol on a given timeframe.

        Args:
            symbol (str): Coin symbol e.g. "BTC"
            timeframe (str): e.g. "15m", "4h"
            n (int): Number of candles to retrieve

        Returns:
            List[Dict[str, Any]]: List of candle dictionaries sorted chronologically.
        """
        timeframe_ms_map = {
            "1m": 60 * 1000,
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
            "1d": 24 * 60 * 60 * 1000,
        }

        candle_duration = timeframe_ms_map.get(timeframe.lower(), 15 * 60 * 1000)
        end_time_ms = int(time.time() * 1000)
        start_time_ms = end_time_ms - int(candle_duration * (n + 10) * 1.5)

        raw_candles = await self.get_candle_snapshot(
            coin=symbol,
            interval=timeframe,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if not raw_candles:
            return []

        sorted_candles = sorted(raw_candles, key=lambda c: c.get("t", 0))
        return sorted_candles[-n:]


# Singleton instance
hyperliquid_client = HyperliquidClient()
hl_client = hyperliquid_client
