"""
OANDA v20 REST Market Data Provider (market_data/oanda.py)
Supports Commodities (XAU/USD Gold, XAG/USD Silver, WTICO/USD Oil), Forex Majors/Minors, and Crypto.
"""

from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List, Optional
import httpx

from market_data.base import BaseMarketDataProvider
from candle_store import CandleStore, candle_store, TIMEFRAME_MS

logger = logging.getLogger("market_data.oanda")


def _oanda_rfc3339_to_ms(time_str: str) -> int:
    """Parses an RFC3339 timestamp (e.g. '2026-09-10T12:00:00.000000000Z') to epoch milliseconds."""
    try:
        clean_str = time_str.replace("Z", "+00:00")
        if "." in clean_str:
            parts = clean_str.split(".")
            sec_frac = parts[1]
            tz_part = "+00:00"
            if "+" in sec_frac:
                frac, tz_suffix = sec_frac.split("+", 1)
                tz_part = "+" + tz_suffix
            elif "-" in sec_frac:
                frac, tz_suffix = sec_frac.split("-", 1)
                tz_part = "-" + tz_suffix
            else:
                frac = sec_frac
            clean_str = f"{parts[0]}.{frac[:6]}{tz_part}"
        dt = datetime.fromisoformat(clean_str)
        return int(dt.timestamp() * 1000)
    except Exception as exc:
        logger.debug("Failed to parse RFC3339 timestamp '%s': %s", time_str, exc)
        return 0


class OandaProvider(BaseMarketDataProvider):
    """
    OANDA v20 REST Market Data Provider.
    Supports Commodities (XAU/USD Gold, XAG/USD Silver, WTICO/USD Oil),
    Forex Majors/Minors, and Crypto.
    """

    OANDA_TIMEFRAME_MAP = {
        "1m": "M1",
        "3m": "M3",
        "5m": "M5",
        "15m": "M15",
        "30m": "M30",
        "1h": "H1",
        "4h": "H4",
        "1d": "D",
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        account_id: Optional[str] = None,
        environment: Optional[str] = None,
        timeout: float = 15.0,
        store: Optional[CandleStore] = None,
        fallback_provider: Optional[BaseMarketDataProvider] = None,
    ):
        self.api_key = (api_key or os.getenv("OANDA_API_KEY", os.getenv("OANDA_ACCESS_TOKEN", ""))).strip()
        self.account_id = (account_id or os.getenv("OANDA_ACCOUNT_ID", "")).strip()
        env_mode = (environment or os.getenv("OANDA_ENVIRONMENT", "practice")).strip().lower()
        if env_mode in ("live", "production", "prod"):
            self.base_url = "https://api-fxtrade.oanda.com/v3"
        else:
            self.base_url = "https://api-fxpractice.oanda.com/v3"

        self.timeout = timeout
        self._store = store or candle_store
        self.fallback_provider = fallback_provider
        self._http_client: Optional[httpx.AsyncClient] = None

    @property
    def name(self) -> str:
        return "oanda"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def _http(self) -> httpx.AsyncClient:
        return self._get_http()

    def _get_http(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
                headers["Accept-Datetime-Format"] = "RFC3339"
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5.0),
                headers=headers,
            )
        return self._http_client

    def resolve_symbol(self, raw_symbol: str) -> str:
        """Converts user symbol (e.g. XAU, GOLD, OIL, BTC, EUR_USD) to OANDA instrument format (XAU_USD)."""
        raw_upper = raw_symbol.strip().upper()
        if "_" in raw_upper:
            return raw_upper
        sym = raw_upper.replace("-PERP", "").replace("USDT", "").replace("/", "_")
        aliases = {
            "XAU": "XAU_USD",
            "GOLD": "XAU_USD",
            "PAXG": "XAU_USD",
            "XAG": "XAG_USD",
            "SILVER": "XAG_USD",
            "OIL": "WTICO_USD",
            "WTIOIL": "WTICO_USD",
            "WTI": "WTICO_USD",
            "CRUDE": "WTICO_USD",
            "BRENT": "BCO_USD",
            "BCO": "BCO_USD",
            "EURUSD": "EUR_USD",
            "GBPUSD": "GBP_USD",
            "USDJPY": "USD_JPY",
            "AUDUSD": "AUD_USD",
            "BTC": "BTC_USD",
            "ETH": "ETH_USD",
            "SOL": "SOL_USD",
        }
        if sym in aliases:
            return aliases[sym]
        if sym.endswith("USD"):
            sym = sym[:-3]
        return f"{sym}_USD"

    def normalize_symbol_to_base(self, oanda_instrument: str) -> str:
        """Converts XAU_USD -> XAU, WTICO_USD -> WTIOIL, BTC_USD -> BTC."""
        s = oanda_instrument.upper()
        reverse_aliases = {
            "XAU_USD": "XAU",
            "XAG_USD": "XAG",
            "WTICO_USD": "WTIOIL",
            "BCO_USD": "BRENT",
        }
        if s in reverse_aliases:
            return reverse_aliases[s]
        if s.endswith("_USD"):
            return s[:-4]
        return s

    async def get_all_mids(self) -> Dict[str, float]:
        """Fetches pricing for known OANDA commodities and major forex/crypto pairs with caching."""
        cached = self._store.get_cached_mids(self.name)
        if cached is not None:
            return cached

        if not self.is_configured():
            logger.debug("OANDA API key not configured. get_all_mids checking fallback.")
            if self.fallback_provider:
                logger.warning("[ProviderFallback] %s not configured -> Delegating get_all_mids to %s", self.name, self.fallback_provider.name)
                fb_mids = await self.fallback_provider.get_all_mids()
                if fb_mids:
                    self._store.set_cached_mids(self.name, fb_mids)
                    return fb_mids
            return {}

        if self._store.is_rate_limited(self.name):
            logger.warning("[RateLimit] Serving empty mids for %s due to rate limit cooldown", self.name)
            if self.fallback_provider:
                logger.warning("[ProviderFallback] %s rate limited -> Delegating get_all_mids to %s", self.name, self.fallback_provider.name)
                fb_mids = await self.fallback_provider.get_all_mids()
                if fb_mids:
                    self._store.set_cached_mids(self.name, fb_mids)
                    return fb_mids
            return {}

        instruments = [
            "XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD",
            "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD",
            "BTC_USD", "ETH_USD", "SOL_USD",
        ]

        if self.account_id:
            try:
                client = self._get_http()
                url = f"{self.base_url}/accounts/{self.account_id}/pricing"
                resp = await client.get(url, params={"instruments": ",".join(instruments)})
                if resp.status_code == 200:
                    data = resp.json()
                    prices = data.get("prices", [])
                    mids: Dict[str, float] = {}
                    for item in prices:
                        inst = item.get("instrument", "")
                        closeout_bid = float(item.get("closeoutBid", 0.0))
                        closeout_ask = float(item.get("closeoutAsk", 0.0))
                        mid_px = (closeout_bid + closeout_ask) / 2.0 if (closeout_bid and closeout_ask) else closeout_bid
                        if mid_px > 0:
                            base = self.normalize_symbol_to_base(inst)
                            mids[base] = mid_px
                            mids[inst] = mid_px
                    self._store.set_cached_mids(self.name, mids)
                    return mids
                elif resp.status_code in (418, 429):
                    self._store.set_rate_limited(self.name, 60.0)
            except Exception as exc:
                logger.warning("OANDA pricing request failed: %s", exc)

        # Fallback: Query 1 candle for each instrument
        mids = {}
        for inst in instruments:
            try:
                c_list = await self.get_last_n_candles(inst, timeframe="5m", n=1)
                if c_list:
                    base = self.normalize_symbol_to_base(inst)
                    mids[base] = c_list[-1]["c"]
                    mids[inst] = c_list[-1]["c"]
            except Exception:
                pass
        if mids:
            self._store.set_cached_mids(self.name, mids)
            return mids

        if self.fallback_provider:
            logger.warning("[ProviderFallback] %s get_all_mids failed -> Delegating to %s", self.name, self.fallback_provider.name)
            fb_mids = await self.fallback_provider.get_all_mids()
            if fb_mids:
                self._store.set_cached_mids(self.name, fb_mids)
                return fb_mids

        return mids

    async def get_last_n_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        n: int = 200,
    ) -> List[Dict[str, Any]]:
        """Fetches last N candles from OANDA v20 REST API with delta updates and in-memory store."""
        cached = self._store.get_candles(self.name, symbol, timeframe, n=n)
        if cached and len(cached) >= min(n, 50) and self._store.is_fresh(self.name, symbol, timeframe):
            return cached

        if not self.is_configured():
            logger.debug("OANDA API key not configured. Skipping candle fetch for %s.", symbol)
            if cached:
                return cached
            if self.fallback_provider:
                logger.warning("[ProviderFallback] %s not configured for %s %s -> Delegating to %s", self.name, symbol, timeframe, self.fallback_provider.name)
                fb_candles = await self.fallback_provider.get_last_n_candles(symbol, timeframe, n)
                if fb_candles:
                    self._store.merge_candles(self.name, symbol, timeframe, fb_candles)
                    return fb_candles
            return []

        if self._store.is_rate_limited(self.name):
            if cached:
                return cached
            if self.fallback_provider:
                logger.warning("[ProviderFallback] %s rate limited for %s %s -> Delegating to %s", self.name, symbol, timeframe, self.fallback_provider.name)
                fb_candles = await self.fallback_provider.get_last_n_candles(symbol, timeframe, n)
                if fb_candles:
                    self._store.merge_candles(self.name, symbol, timeframe, fb_candles)
                    return fb_candles
            return []

        instrument = self.resolve_symbol(symbol)
        granularity = self.OANDA_TIMEFRAME_MAP.get(timeframe, "M5")
        client = self._get_http()
        url = f"{self.base_url}/instruments/{instrument}/candles"

        has_bootstrapped = self._store.has_sufficient_candles(self.name, symbol, timeframe, min_count=min(n, 50))
        count = 5 if has_bootstrapped else min(5000, max(50, n))

        params = {
            "granularity": granularity,
            "count": count,
            "price": "M",  # Midpoint candles
        }

        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                raw_candles = data.get("candles", [])
                candles = []
                dur_ms = TIMEFRAME_MS.get(timeframe, 5 * 60 * 1000)
                base_sym = self.normalize_symbol_to_base(instrument)

                for row in raw_candles:
                    mid = row.get("mid", {})
                    time_str = row.get("time", "")  # RFC3339 format
                    t_ms = _oanda_rfc3339_to_ms(time_str)
                    if t_ms <= 0:
                        continue

                    candles.append({
                        "t": t_ms,
                        "T": t_ms + dur_ms,
                        "s": base_sym,
                        "i": timeframe,
                        "o": float(mid.get("o", 0.0)),
                        "h": float(mid.get("h", 0.0)),
                        "l": float(mid.get("l", 0.0)),
                        "c": float(mid.get("c", 0.0)),
                        "v": float(row.get("volume", 0.0)),
                    })
                self._store.merge_candles(self.name, symbol, timeframe, candles)
                return self._store.get_candles(self.name, symbol, timeframe, n=n) or candles[-n:]
            elif resp.status_code in (418, 429):
                self._store.set_rate_limited(self.name, 60.0)
                logger.warning("OANDA candles hit rate limit (HTTP %d) for %s", resp.status_code, instrument)
                if cached:
                    return cached
                if self.fallback_provider:
                    logger.warning("[ProviderFallback] %s hit HTTP %d for %s (%s) -> Delegating to %s", self.name, resp.status_code, symbol, timeframe, self.fallback_provider.name)
                    fb_candles = await self.fallback_provider.get_last_n_candles(symbol, timeframe, n)
                    if fb_candles:
                        self._store.merge_candles(self.name, symbol, timeframe, fb_candles)
                        return fb_candles
                return self._store.get_candles(self.name, symbol, timeframe, n=n) or []
            else:
                logger.warning("OANDA candles for %s returned HTTP %d: %s", instrument, resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Failed to fetch OANDA candles for %s: %s", instrument, exc)

        if cached:
            return cached
        if self.fallback_provider:
            logger.warning("[ProviderFallback] %s get_last_n_candles failed for %s (%s) -> Delegating to %s", self.name, symbol, timeframe, self.fallback_provider.name)
            fb_candles = await self.fallback_provider.get_last_n_candles(symbol, timeframe, n)
            if fb_candles:
                self._store.merge_candles(self.name, symbol, timeframe, fb_candles)
                return fb_candles

        return self._store.get_candles(self.name, symbol, timeframe, n=n) or []

    async def get_historical_candles_range(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Dict[str, Any]]:
        """Fetches historical candles over an explicit epoch ms range."""
        if not self.is_configured():
            if self.fallback_provider:
                logger.warning("[ProviderFallback] %s not configured for historical range -> Delegating to %s", self.name, self.fallback_provider.name)
                return await self.fallback_provider.get_historical_candles_range(coin, interval, start_time_ms, end_time_ms)
            return []

        instrument = self.resolve_symbol(coin)
        granularity = self.OANDA_TIMEFRAME_MAP.get(interval, "M5")
        client = self._get_http()
        url = f"{self.base_url}/instruments/{instrument}/candles"

        from_dt = datetime.fromtimestamp(start_time_ms / 1000.0, tz=timezone.utc).isoformat()
        to_dt = datetime.fromtimestamp(end_time_ms / 1000.0, tz=timezone.utc).isoformat()

        params = {
            "granularity": granularity,
            "from": from_dt,
            "to": to_dt,
            "price": "M",
        }

        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                raw_candles = data.get("candles", [])
                candles = []
                dur_ms = TIMEFRAME_MS.get(interval, 5 * 60 * 1000)
                base_sym = self.normalize_symbol_to_base(instrument)

                for row in raw_candles:
                    mid = row.get("mid", {})
                    time_str = row.get("time", "")
                    t_ms = _oanda_rfc3339_to_ms(time_str)
                    if t_ms <= 0:
                        continue

                    candles.append({
                        "t": t_ms,
                        "T": t_ms + dur_ms,
                        "s": base_sym,
                        "i": interval,
                        "o": float(mid.get("o", 0.0)),
                        "h": float(mid.get("h", 0.0)),
                        "l": float(mid.get("l", 0.0)),
                        "c": float(mid.get("c", 0.0)),
                        "v": float(row.get("volume", 0.0)),
                    })
                return candles
        except Exception as exc:
            logger.warning("Failed to fetch historical OANDA candles for %s: %s", instrument, exc)

        if self.fallback_provider:
            logger.warning("[ProviderFallback] %s historical range failed for %s -> Delegating to %s", self.name, coin, self.fallback_provider.name)
            return await self.fallback_provider.get_historical_candles_range(coin, interval, start_time_ms, end_time_ms)

        return []

    async def get_universe_coins(self, min_volume: float = 0.0) -> List[str]:
        if not self.is_configured() and self.fallback_provider:
            return await self.fallback_provider.get_universe_coins(min_volume=min_volume)
        return ["XAU", "XAG", "WTIOIL", "BRENT", "EURUSD", "GBPUSD", "USDJPY", "BTC", "ETH"]

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
