"""
Unified Market Data Provider Architecture (market_data_provider.py)

Supports configurable, pluggable market data providers:
1. BinanceProvider: Binance USDT-M Futures & Spot (Zero Auth, Deep Klines, Ticker prices)
2. OandaProvider: OANDA v20 REST API (Multi-Asset CFDs: Gold, Silver, Crude Oil, Forex, Crypto)
3. HyperliquidProvider: Hyperliquid Perpetual L1 DEX API
"""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx
from dotenv import load_dotenv

from hyperliquid_client import HyperliquidClient, hyperliquid_client

load_dotenv()
logger = logging.getLogger("market_data_provider")

IST = timezone(timedelta(hours=5, minutes=30))

TIMEFRAME_MS: Dict[str, int] = {
    "1m": 60 * 1000,
    "3m": 180 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 3600 * 1000,
    "1d": 24 * 3600 * 1000,
}


class BaseMarketDataProvider(ABC):
    """Abstract Base Class for all market data providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Returns provider identifier name."""
        pass

    @abstractmethod
    async def get_all_mids(self) -> Dict[str, float]:
        """Returns a map of normalized symbols to their latest mid prices."""
        pass

    @abstractmethod
    async def get_last_n_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        n: int = 200,
    ) -> List[Dict[str, Any]]:
        """Fetches the latest N closed/in-progress OHLCV candles."""
        pass

    @abstractmethod
    async def get_historical_candles_range(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Dict[str, Any]]:
        """Fetches historical candles over an explicit epoch milliseconds range."""
        pass

    @abstractmethod
    async def get_universe_coins(self, min_volume: float = 0.0) -> List[str]:
        """Returns the active tradable coin universe sorted by volume."""
        pass

    @abstractmethod
    def resolve_symbol(self, raw_symbol: str) -> str:
        """Normalizes symbol to provider format."""
        pass

    @abstractmethod
    async def close(self):
        """Releases underlying HTTP client connections."""
        pass


# ==============================================================================
# 1. BINANCE PROVIDER (Futures & Spot)
# ==============================================================================
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
    ):
        self.use_futures = use_futures
        default_url = "https://fapi.binance.com" if use_futures else "https://api.binance.com"
        self.api_url = (api_url or os.getenv("BINANCE_API_URL", default_url)).rstrip("/")
        self.timeout = timeout
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
        """Fetches all ticker prices in a single bulk request."""
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
                return mids
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
        """Fetches latest N candles for symbol and timeframe."""
        binance_sym = self.resolve_symbol(symbol)
        client = self._get_http()
        endpoint = "/fapi/v1/klines" if self.use_futures else "/api/v3/klines"
        url = f"{self.api_url}{endpoint}"

        limit = min(1000, max(1, n))
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
                    return candles
            elif resp.status_code == 400 and self.use_futures:
                # If symbol not found on Futures (e.g. PAXG on spot), fallback to spot
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
                    return candles
        except Exception as exc:
            logger.warning("Binance get_last_n_candles failed for %s (%s): %s", symbol, timeframe, exc)
        return []

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


# ==============================================================================
# 2. OANDA PROVIDER (Multi-Asset CFDs: Gold, Silver, Oil, Forex, Crypto)
# ==============================================================================
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
    ):
        self.api_key = (api_key or os.getenv("OANDA_API_KEY", os.getenv("OANDA_ACCESS_TOKEN", ""))).strip()
        self.account_id = (account_id or os.getenv("OANDA_ACCOUNT_ID", "")).strip()
        env_mode = (environment or os.getenv("OANDA_ENVIRONMENT", "practice")).strip().lower()
        if env_mode in ("live", "production", "prod"):
            self.base_url = "https://api-fxtrade.oanda.com/v3"
        else:
            self.base_url = "https://api-fxpractice.oanda.com/v3"

        self.timeout = timeout
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
        """Fetches pricing for known OANDA commodities and major forex/crypto pairs."""
        if not self.is_configured():
            logger.debug("OANDA API key not configured. get_all_mids returning empty dict.")
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
                    return mids
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
        return mids

    async def get_last_n_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        n: int = 200,
    ) -> List[Dict[str, Any]]:
        """Fetches last N candles from OANDA v20 REST API."""
        if not self.is_configured():
            logger.debug("OANDA API key not configured. Skipping candle fetch for %s.", symbol)
            return []

        instrument = self.resolve_symbol(symbol)
        granularity = self.OANDA_TIMEFRAME_MAP.get(timeframe, "M5")
        client = self._get_http()
        url = f"{self.base_url}/instruments/{instrument}/candles"

        params = {
            "granularity": granularity,
            "count": min(5000, max(1, n)),
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
                return candles
            else:
                logger.warning("OANDA candles for %s returned HTTP %d: %s", instrument, resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Failed to fetch OANDA candles for %s: %s", instrument, exc)

        return []

    async def get_historical_candles_range(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Dict[str, Any]]:
        """Fetches historical candles over an explicit epoch ms range."""
        if not self.is_configured():
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

        return []

    async def get_universe_coins(self, min_volume: float = 0.0) -> List[str]:
        return ["XAU", "XAG", "WTIOIL", "BRENT", "EURUSD", "GBPUSD", "USDJPY", "BTC", "ETH"]

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None


# ==============================================================================
# 3. HYPERLIQUID PROVIDER (Adapter wrapping existing HyperliquidClient)
# ==============================================================================
class HyperliquidProvider(BaseMarketDataProvider):
    """Adapter wrapping HyperliquidClient into BaseMarketDataProvider interface."""

    def __init__(self, client: Optional[HyperliquidClient] = None):
        self._client = client or hyperliquid_client

    @property
    def name(self) -> str:
        return "hyperliquid"

    def resolve_symbol(self, raw_symbol: str) -> str:
        from hyperliquid_client import resolve_symbol
        return resolve_symbol(raw_symbol)

    async def get_all_mids(self) -> Dict[str, float]:
        return await self._client.get_all_mids()

    async def get_last_n_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        n: int = 200,
    ) -> List[Dict[str, Any]]:
        return await self._client.get_last_n_candles(symbol=symbol, timeframe=timeframe, n=n)

    async def get_historical_candles_range(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Dict[str, Any]]:
        return await self._client.get_historical_candles_range(
            coin=coin, interval=interval, start_time_ms=start_time_ms, end_time_ms=end_time_ms
        )

    async def get_universe_coins(self, min_volume: float = 0.0) -> List[str]:
        return await self._client.get_universe_coins(min_volume=min_volume)

    async def close(self):
        await self._client.close()


# ==============================================================================
# 4. PROVIDER FACTORY & SINGLETON MANAGEMENT
# ==============================================================================
_PROVIDERS: Dict[str, BaseMarketDataProvider] = {}


def get_market_data_provider(provider_name: Optional[str] = None) -> BaseMarketDataProvider:
    """
    Returns the singleton instance of the requested market data provider.
    Defaults to DATA_PROVIDER env var, or 'binance'.
    Options: 'binance', 'binance_futures', 'binance_spot', 'oanda', 'hyperliquid'.
    """
    selected = (provider_name or os.getenv("DATA_PROVIDER", "binance")).strip().lower()

    if selected in _PROVIDERS:
        return _PROVIDERS[selected]

    if selected in ("binance", "binance_futures", "binance-futures"):
        provider = BinanceProvider(use_futures=True)
    elif selected in ("binance_spot", "binance-spot"):
        provider = BinanceProvider(use_futures=False)
    elif selected in ("oanda", "onda"):
        provider = OandaProvider()
    elif selected in ("hyperliquid", "hl"):
        provider = HyperliquidProvider()
    else:
        logger.warning("Unknown DATA_PROVIDER '%s'. Defaulting to Binance Futures.", selected)
        provider = BinanceProvider(use_futures=True)

    _PROVIDERS[selected] = provider
    return provider


# Active default singleton
market_data_provider = get_market_data_provider()


async def close_all_providers():
    """Closes all initialized market data provider instances."""
    for name, prov in list(_PROVIDERS.items()):
        try:
            await prov.close()
        except Exception as exc:
            logger.debug("Error closing provider %s: %s", name, exc)
    _PROVIDERS.clear()

