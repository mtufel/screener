"""
Extreme LTF FVG Strategy Engine (strategy_extreme_fvg.py).
Focuses on:
1. Identifying active 4H Fair Value Gaps strictly on fully closed candles.
2. Dynamic invalidation tracking (wick or close breach).
3. Incremental caching (HTFFVGCache) with bootstrap and delta scans for high-performance execution.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import logging
import os
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from dotenv import load_dotenv
from market_data_provider import market_data_provider, BaseMarketDataProvider
from hyperliquid_client import HyperliquidClient, hyperliquid_client

load_dotenv()

logger = logging.getLogger(__name__)

# IST Timezone (UTC + 5:30)
IST = timezone(timedelta(hours=5, minutes=30))

HTF_TIMEFRAME = "4h"
HTF_CANDLE_DURATION_MS = 4 * 3600 * 1000

TIMEFRAME_MS: Dict[str, int] = {
    "1m": 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 3600 * 1000,
}
DEFAULT_LTF_TIMEFRAME: str = os.getenv("EXTREME_LTF_TIMEFRAME", "5m")


def is_in_ny_session(timestamp_ms: int) -> bool:
    """Return True if the UTC hour of timestamp_ms is within NY session 13:00-22:00 UTC."""
    try:
        dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
        return 13 <= dt.hour < 22
    except (ValueError, OSError):
        return False


def is_weekday(timestamp_ms: int) -> bool:
    """Return True if timestamp_ms falls on Monday-Friday (UTC)."""
    try:
        dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
        return dt.weekday() < 5
    except (ValueError, OSError):
        return False


@dataclass
class Candle:
    """Standard OHLCV Candle."""
    timestamp: int  # Open timestamp in milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Candle":
        return cls(
            timestamp=int(data.get("t", 0)),
            open=float(data.get("o", 0.0)),
            high=float(data.get("h", 0.0)),
            low=float(data.get("l", 0.0)),
            close=float(data.get("c", 0.0)),
            volume=float(data.get("v", 0.0)),
        )


@dataclass
class FVG:
    """Fair Value Gap representation."""
    direction: Literal["Bullish", "Bearish"]
    top: float       # Upper boundary of the gap
    bottom: float    # Lower boundary of the gap
    c1: Candle       # Oldest candle in 3-candle sequence
    c2: Candle       # Middle candle (gap impulse)
    c3: Candle       # Newest candle in 3-candle sequence
    formed_at: int   # Open timestamp (ms) of c3
    is_valid: bool = True
    timeframe: str = "4h"
    lifecycle_state: str = "PENDING_RETRACE"
    entry_timestamp: Optional[int] = None
    floating_r: float = 0.0

    @property
    def width(self) -> float:
        """Absolute price width of the gap."""
        return max(0.0, self.top - self.bottom)

    @property
    def midpoint(self) -> float:
        """Midpoint price of the gap."""
        return (self.top + self.bottom) / 2.0

    @property
    def gap_pct(self) -> float:
        """Gap width as a percentage of midpoint price."""
        mid = self.midpoint
        return (self.width / mid) * 100.0 if mid > 0 else 0.0

    @property
    def close_timestamp(self) -> int:
        """Timestamp (ms) when candle 3 closed (when FVG fully completed)."""
        duration_ms = TIMEFRAME_MS.get(self.timeframe, HTF_CANDLE_DURATION_MS)
        return self.formed_at + duration_ms

    @property
    def formed_time_ist(self) -> str:
        """Formatted formation time (C3 close) in IST."""
        return datetime.fromtimestamp(self.close_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")


def filter_closed_candles(
    candles: List[Candle],
    duration_ms: int = HTF_CANDLE_DURATION_MS,
    current_time_ms: Optional[int] = None,
) -> List[Candle]:
    """
    Strictly filters candles to include only those that have FULLY CLOSED.
    A candle is fully closed if: candle.timestamp + duration_ms <= current_time_ms.
    If current_time_ms is not provided, defaults to system current time.
    """
    now_ms = int(time.time() * 1000) if current_time_ms is None else current_time_ms
    return [c for c in candles if (c.timestamp + duration_ms) <= now_ms]


def compute_all_active_4h_fvgs(
    candles_4h: List[Candle],
    current_time_ms: Optional[int] = None,
    use_close_invalidation: bool = False,
    enforce_closed_filter: bool = True,
) -> List[FVG]:
    """
    Identifies all historical 4H FVGs strictly on fully closed candles and tracks invalidation.

    Formation Rules:
    - Bullish FVG: c3.low > c1.high -> [bottom=c1.high, top=c3.low]
    - Bearish FVG: c3.high < c1.low -> [bottom=c3.high, top=c1.low]

    Invalidation Rules:
    - Bullish FVG: subsequent price falls below `bottom`
    - Bearish FVG: subsequent price rises above `top`
    """
    closed_candles = (
        filter_closed_candles(candles_4h, HTF_CANDLE_DURATION_MS, current_time_ms)
        if enforce_closed_filter
        else candles_4h
    )

    if len(closed_candles) < 3:
        return []

    valid_fvgs: List[FVG] = []

    for i in range(len(closed_candles) - 2):
        c1 = closed_candles[i]
        c2 = closed_candles[i + 1]
        c3 = closed_candles[i + 2]

        candidate: Optional[FVG] = None

        if c3.low > c1.high:
            candidate = FVG(
                direction="Bullish",
                top=c3.low,
                bottom=c1.high,
                c1=c1,
                c2=c2,
                c3=c3,
                formed_at=c3.timestamp,
                timeframe="4h",
            )
        elif c3.high < c1.low:
            candidate = FVG(
                direction="Bearish",
                top=c1.low,
                bottom=c3.high,
                c1=c1,
                c2=c2,
                c3=c3,
                formed_at=c3.timestamp,
                timeframe="4h",
            )

        if candidate is None:
            continue

        # Check invalidation across subsequent candles (from index i + 3 onwards)
        is_invalidated = False
        for k in range(i + 3, len(closed_candles)):
            sub_c = closed_candles[k]
            if candidate.direction == "Bullish":
                breach = sub_c.close if use_close_invalidation else sub_c.low
                if breach < candidate.bottom:
                    is_invalidated = True
                    break
            else:
                breach = sub_c.close if use_close_invalidation else sub_c.high
                if breach > candidate.top:
                    is_invalidated = True
                    break

        if not is_invalidated:
            valid_fvgs.append(candidate)

    # Sort newest first
    valid_fvgs.sort(key=lambda f: f.formed_at, reverse=True)
    return valid_fvgs


class HTFFVGCache:
    """
    Incremental 4H FVG Cache.
    - Initial bootstrap scan calculates all active non-invalidated 4H FVGs.
    - Subsequent delta scans only evaluate new fully closed candles and live price
      for new FVG detection and invalidation, operating in O(1) time.
    """

    def __init__(self):
        # Maps symbol -> list of active, non-invalidated 4H FVGs (newest first)
        self.active_fvgs: Dict[str, List[FVG]] = {}
        # Maps symbol -> timestamp of the latest closed 4H candle processed
        self.last_processed_candle_ts: Dict[str, int] = {}
        # Maps symbol -> last 2 closed 4H candles to detect transitions across deltas
        self.last_closed_candles: Dict[str, List[Candle]] = {}

    def _key(self, symbol: str, use_close_invalidation: bool) -> str:
        mode = "close" if use_close_invalidation else "wick"
        return f"{symbol}:{mode}"

    def is_bootstrapped(self, symbol: str, use_close_invalidation: bool = False) -> bool:
        """Checks if a symbol with specified invalidation mode has been initialized in the cache."""
        return self._key(symbol, use_close_invalidation) in self.last_processed_candle_ts

    def bootstrap(
        self,
        symbol: str,
        candles_4h: List[Candle],
        current_time_ms: Optional[int] = None,
        use_close_invalidation: bool = False,
        enforce_closed_filter: bool = True,
    ) -> List[FVG]:
        """
        Initializes the cache for a symbol using historical 4H candles.
        Strictly filters for closed candles only.
        """
        key = self._key(symbol, use_close_invalidation)
        closed_candles = (
            filter_closed_candles(candles_4h, HTF_CANDLE_DURATION_MS, current_time_ms)
            if enforce_closed_filter
            else candles_4h
        )

        if not closed_candles:
            self.active_fvgs[key] = []
            self.last_processed_candle_ts[key] = 0
            self.last_closed_candles[key] = []
            return []

        # Compute all active non-invalidated 4H FVGs
        active = compute_all_active_4h_fvgs(
            closed_candles,
            current_time_ms=current_time_ms,
            use_close_invalidation=use_close_invalidation,
            enforce_closed_filter=False,  # Already filtered above
        )

        self.active_fvgs[key] = active
        self.last_processed_candle_ts[key] = closed_candles[-1].timestamp
        self.last_closed_candles[key] = closed_candles[-2:] if len(closed_candles) >= 2 else closed_candles[:]

        # Also populate bare symbol for backward compatibility
        self.last_processed_candle_ts[symbol] = self.last_processed_candle_ts[key]
        self.last_closed_candles[symbol] = self.last_closed_candles[key]

        logger.info(
            "[HTF Cache Bootstrap] %s (%s): Initialized with %d active 4H FVG(s). Latest closed bar: %s",
            symbol,
            "close" if use_close_invalidation else "wick",
            len(active),
            datetime.fromtimestamp(self.last_processed_candle_ts[key] / 1000.0, tz=IST).strftime("%d-%b %I:%M %p"),
        )
        return active

    def update_delta(
        self,
        symbol: str,
        recent_candles_4h: List[Candle],
        current_price: float,
        current_time_ms: Optional[int] = None,
        use_close_invalidation: bool = False,
        enforce_closed_filter: bool = True,
    ) -> List[FVG]:
        """
        Incrementally updates active 4H FVGs using delta candles and live price.
        If the symbol is not yet bootstrapped, runs bootstrap automatically.
        """
        key = self._key(symbol, use_close_invalidation)
        if not self.is_bootstrapped(symbol, use_close_invalidation=use_close_invalidation):
            return self.bootstrap(
                symbol=symbol,
                candles_4h=recent_candles_4h,
                current_time_ms=current_time_ms,
                use_close_invalidation=use_close_invalidation,
                enforce_closed_filter=enforce_closed_filter,
            )

        last_ts = self.last_processed_candle_ts.get(key, 0)

        # 1. Filter for newly closed candles only (ts > last_ts AND fully closed)
        now_ms = int(time.time() * 1000) if current_time_ms is None else current_time_ms
        new_closed_candles = [
            c for c in recent_candles_4h
            if c.timestamp > last_ts and (not enforce_closed_filter or (c.timestamp + HTF_CANDLE_DURATION_MS) <= now_ms)
        ]
        new_closed_candles.sort(key=lambda c: c.timestamp)

        active = list(self.active_fvgs.get(key, []))

        # 2. Invalidation Step: Evaluate existing active FVGs against new delta candles + current price
        surviving_fvgs: List[FVG] = []
        for fvg in active:
            is_invalidated = False

            # Check against each new delta candle
            for c in new_closed_candles:
                if fvg.direction == "Bullish":
                    breach = c.close if use_close_invalidation else c.low
                    if breach < fvg.bottom:
                        is_invalidated = True
                        break
                else:
                    breach = c.close if use_close_invalidation else c.high
                    if breach > fvg.top:
                        is_invalidated = True
                        break

            # Also check live price against boundary
            if not is_invalidated and current_price > 0:
                if fvg.direction == "Bullish" and current_price < fvg.bottom:
                    is_invalidated = True
                elif fvg.direction == "Bearish" and current_price > fvg.top:
                    is_invalidated = True

            if not is_invalidated:
                surviving_fvgs.append(fvg)
            else:
                logger.info(
                    "[HTF Cache Invalidation] %s: 4H %s FVG [%.2f - %.2f] formed %s invalidated by price breach.",
                    symbol,
                    fvg.direction,
                    fvg.bottom,
                    fvg.top,
                    fvg.formed_time_ist,
                )

        active = surviving_fvgs

        # 3. New Formation Step: If there are new closed candles, detect newly formed 4H FVGs
        if new_closed_candles:
            prior_candles = self.last_closed_candles.get(key, [])
            combined = prior_candles + new_closed_candles

            # Scan any 3-candle window that includes at least one newly closed candle
            for i in range(len(combined) - 2):
                c1 = combined[i]
                c2 = combined[i + 1]
                c3 = combined[i + 2]

                # Only evaluate if c3 is a newly closed candle
                if c3.timestamp <= last_ts:
                    continue

                new_candidate: Optional[FVG] = None
                if c3.low > c1.high:
                    new_candidate = FVG(
                        direction="Bullish",
                        top=c3.low,
                        bottom=c1.high,
                        c1=c1,
                        c2=c2,
                        c3=c3,
                        formed_at=c3.timestamp,
                        timeframe="4h",
                    )
                elif c3.high < c1.low:
                    new_candidate = FVG(
                        direction="Bearish",
                        top=c1.low,
                        bottom=c3.high,
                        c1=c1,
                        c2=c2,
                        c3=c3,
                        formed_at=c3.timestamp,
                        timeframe="4h",
                    )

                if new_candidate is not None:
                    # Check if the new FVG was already invalidated by subsequent candles in combined or live price
                    cand_invalidated = False
                    for rem_idx in range(i + 3, len(combined)):
                        sub_c = combined[rem_idx]
                        if new_candidate.direction == "Bullish":
                            b = sub_c.close if use_close_invalidation else sub_c.low
                            if b < new_candidate.bottom:
                                cand_invalidated = True
                                break
                        else:
                            b = sub_c.close if use_close_invalidation else sub_c.high
                            if b > new_candidate.top:
                                cand_invalidated = True
                                break

                    if not cand_invalidated and current_price > 0:
                        if new_candidate.direction == "Bullish" and current_price < new_candidate.bottom:
                            cand_invalidated = True
                        elif new_candidate.direction == "Bearish" and current_price > new_candidate.top:
                            cand_invalidated = True

                    if not cand_invalidated:
                        active.insert(0, new_candidate)
                        logger.info(
                            "[HTF Cache New FVG] %s: Detected new 4H %s FVG [%.2f - %.2f] formed %s",
                            symbol,
                            new_candidate.direction,
                            new_candidate.bottom,
                            new_candidate.top,
                            new_candidate.formed_time_ist,
                        )

            # Update cache bookkeeping
            self.last_processed_candle_ts[key] = new_closed_candles[-1].timestamp
            self.last_closed_candles[key] = combined[-2:]
            self.last_processed_candle_ts[symbol] = self.last_processed_candle_ts[key]
            self.last_closed_candles[symbol] = self.last_closed_candles[key]

        # Sort newest first
        active.sort(key=lambda f: f.formed_at, reverse=True)
        self.active_fvgs[key] = active
        return active

    def get_active_fvgs(self, symbol: str, use_close_invalidation: bool = False) -> List[FVG]:
        """Returns the currently active, non-invalidated 4H FVGs for symbol and mode."""
        key = self._key(symbol, use_close_invalidation)
        return list(self.active_fvgs.get(key, []))

    def invalidate_cache(self, symbol: Optional[str] = None):
        """Clears cache for a single symbol or all symbols."""
        if symbol:
            for mode in ["wick", "close"]:
                k = f"{symbol}:{mode}"
                self.active_fvgs.pop(k, None)
                self.last_processed_candle_ts.pop(k, None)
                self.last_closed_candles.pop(k, None)
        else:
            self.active_fvgs.clear()
            self.last_processed_candle_ts.clear()
            self.last_closed_candles.clear()

    async def load_from_redis(self, symbol: str, use_close_invalidation: bool = False) -> bool:
        """Attempts to restore cached 4H FVGs from Redis to eliminate startup bootstrap lag."""
        try:
            from redis_client import redis_client
            if not redis_client.is_configured():
                return False
            key = self._key(symbol, use_close_invalidation)
            redis_key = redis_client.get_key(f"htf_cache:{key}")
            data = await redis_client.get_json(redis_key)
            if not data or not isinstance(data, dict):
                return False

            fvgs_raw = data.get("active_fvgs", [])
            self.active_fvgs[key] = [fvg_from_dict(d) for d in fvgs_raw]
            self.last_processed_candle_ts[key] = int(data.get("last_processed_candle_ts", 0))
            last_c = data.get("last_closed_candles", [])
            self.last_closed_candles[key] = [Candle.from_dict(c) for c in last_c]

            self.last_processed_candle_ts[symbol] = self.last_processed_candle_ts[key]
            self.last_closed_candles[symbol] = self.last_closed_candles[key]

            logger.info(
                "[HTF Cache Redis Restore] %s: Restored %d active 4H FVGs from Redis ('%s').",
                key,
                len(self.active_fvgs[key]),
                redis_key,
            )
            return True
        except Exception as exc:
            logger.debug("Failed to load HTF cache from Redis for %s: %s", symbol, exc)
            return False

    async def save_to_redis(self, symbol: str, use_close_invalidation: bool = False, ttl_seconds: int = 86400) -> bool:
        """Saves current active 4H FVGs to Redis with a 24-hour TTL."""
        try:
            from redis_client import redis_client
            if not redis_client.is_configured():
                return False
            key = self._key(symbol, use_close_invalidation)
            redis_key = redis_client.get_key(f"htf_cache:{key}")
            data = {
                "symbol": symbol,
                "mode": "close" if use_close_invalidation else "wick",
                "last_processed_candle_ts": self.last_processed_candle_ts.get(key, 0),
                "last_closed_candles": [
                    {"t": c.timestamp, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume}
                    for c in self.last_closed_candles.get(key, [])
                ],
                "active_fvgs": [fvg_to_dict(f) for f in self.active_fvgs.get(key, [])],
            }
            return await redis_client.set_json(redis_key, data, ex=ttl_seconds)
        except Exception as exc:
            logger.debug("Failed to save HTF cache to Redis for %s: %s", symbol, exc)
            return False


def fvg_to_dict(fvg: FVG) -> Dict[str, Any]:
    """Serializes an FVG dataclass instance to a JSON-compatible dictionary."""
    return {
        "direction": fvg.direction,
        "top": fvg.top,
        "bottom": fvg.bottom,
        "c1": {
            "t": fvg.c1.timestamp, "o": fvg.c1.open, "h": fvg.c1.high,
            "l": fvg.c1.low, "c": fvg.c1.close, "v": fvg.c1.volume
        } if fvg.c1 else None,
        "c2": {
            "t": fvg.c2.timestamp, "o": fvg.c2.open, "h": fvg.c2.high,
            "l": fvg.c2.low, "c": fvg.c2.close, "v": fvg.c2.volume
        } if fvg.c2 else None,
        "c3": {
            "t": fvg.c3.timestamp, "o": fvg.c3.open, "h": fvg.c3.high,
            "l": fvg.c3.low, "c": fvg.c3.close, "v": fvg.c3.volume
        } if fvg.c3 else None,
        "formed_at": fvg.formed_at,
        "is_valid": fvg.is_valid,
        "timeframe": fvg.timeframe,
        "lifecycle_state": fvg.lifecycle_state,
        "entry_timestamp": fvg.entry_timestamp,
        "floating_r": fvg.floating_r,
    }


def fvg_from_dict(d: Dict[str, Any]) -> FVG:
    """Deserializes a dictionary into an FVG dataclass instance."""
    c1 = Candle.from_dict(d["c1"]) if d.get("c1") else Candle(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    c2 = Candle.from_dict(d["c2"]) if d.get("c2") else Candle(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    c3 = Candle.from_dict(d["c3"]) if d.get("c3") else Candle(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return FVG(
        direction=d["direction"],
        top=float(d["top"]),
        bottom=float(d["bottom"]),
        c1=c1,
        c2=c2,
        c3=c3,
        formed_at=int(d["formed_at"]),
        is_valid=d.get("is_valid", True),
        timeframe=d.get("timeframe", "4h"),
        lifecycle_state=d.get("lifecycle_state", "PENDING_RETRACE"),
        entry_timestamp=d.get("entry_timestamp"),
        floating_r=float(d.get("floating_r", 0.0)),
    )


# Global Singleton Cache Instance
htf_fvg_cache = HTFFVGCache()


async def get_active_4h_fvgs_for_symbol(
    symbol: str,
    client: Optional[Any] = None,
    use_close_invalidation: bool = False,
    force_bootstrap: bool = False,
    candles_4h: Optional[List[Candle]] = None,
) -> List[FVG]:
    """
    Fetches live 4H candles (or uses provided candles_4h) and returns active
    non-invalidated 4H FVGs using the incremental cache with Redis persistence.
    """
    # 1. Attempt restore from Redis if not currently in memory and not forcing a bootstrap
    if not force_bootstrap and not htf_fvg_cache.is_bootstrapped(symbol, use_close_invalidation=use_close_invalidation):
        await htf_fvg_cache.load_from_redis(symbol, use_close_invalidation=use_close_invalidation)

    candles = candles_4h
    if candles is None:
        cli = client or market_data_provider
        raw = await cli.get_last_n_candles(symbol=symbol, timeframe=HTF_TIMEFRAME, n=200)
        if not raw:
            return []
        candles = [Candle.from_dict(c) for c in raw]

    if not candles:
        return []

    current_price = candles[-1].close if candles else 0.0

    if force_bootstrap or not htf_fvg_cache.is_bootstrapped(symbol, use_close_invalidation=use_close_invalidation):
        fvgs = htf_fvg_cache.bootstrap(
            symbol=symbol,
            candles_4h=candles,
            use_close_invalidation=use_close_invalidation,
            enforce_closed_filter=True,
        )
    else:
        fvgs = htf_fvg_cache.update_delta(
            symbol=symbol,
            recent_candles_4h=candles,
            current_price=current_price,
            use_close_invalidation=use_close_invalidation,
            enforce_closed_filter=True,
        )

    # Sync updated cache state to Redis asynchronously
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        if loop and loop.is_running():
            loop.create_task(htf_fvg_cache.save_to_redis(symbol, use_close_invalidation=use_close_invalidation))
    except Exception as exc:
        logger.debug("Failed to schedule Redis save for HTF cache %s: %s", symbol, exc)

    return fvgs


# ==============================================================================
# STEP 2: 4H TOUCH DETECTION & MOST RECENT TOUCHED ANCHOR
# ==============================================================================
@dataclass
class TouchedAnchor:
    """
    Represents a 4H FVG that has been touched post-close and acts as the single active anchor.
    Separates:
    1. first_touch_timestamp: When the 4H FVG was FIRST touched post-close.
       (Scanning of LTF FVGs will ALWAYS start from this time).
    2. most_recent_touch_timestamp: When the 4H FVG was touched MOST RECENTLY.
       (Used for selecting which HTF FVG will be used for the LTF FVG scan).
    """
    fvg: FVG
    first_touch_timestamp: int          # When 4H FVG was FIRST touched (LTF FVG scan starts here!)
    most_recent_touch_timestamp: int   # When 4H FVG was touched MOST RECENTLY (used for anchor selection)
    is_currently_inside: bool = False
    touch_timeframe: str = "4h"

    @property
    def touch_timestamp(self) -> int:
        """Alias: Scanning of LTF FVG always starts from this First Touch Time."""
        return self.first_touch_timestamp

    @property
    def latest_touch_timestamp(self) -> int:
        """Alias for most_recent_touch_timestamp."""
        return self.most_recent_touch_timestamp

    @property
    def first_touch_time_ist(self) -> str:
        """Formatted First Touch Time in IST with candle interval."""
        duration_ms = TIMEFRAME_MS.get(self.touch_timeframe, HTF_CANDLE_DURATION_MS)
        t_open = datetime.fromtimestamp(self.first_touch_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p")
        t_close = datetime.fromtimestamp((self.first_touch_timestamp + duration_ms) / 1000.0, tz=IST).strftime("%I:%M %p IST")
        return f"{t_open} - {t_close}"

    @property
    def most_recent_touch_time_ist(self) -> str:
        """Formatted Most Recent Touch Time in IST."""
        if self.is_currently_inside:
            return "Currently Inside (Active Now)"
        duration_ms = TIMEFRAME_MS.get(self.touch_timeframe, HTF_CANDLE_DURATION_MS)
        t_open = datetime.fromtimestamp(self.most_recent_touch_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p")
        t_close = datetime.fromtimestamp((self.most_recent_touch_timestamp + duration_ms) / 1000.0, tz=IST).strftime("%I:%M %p IST")
        return f"{t_open} - {t_close}"

    @property
    def touch_time_ist(self) -> str:
        """Legacy alias pointing to first_touch_time_ist."""
        return self.first_touch_time_ist


def get_4h_fvg_first_touch_ts(
    candles_4h: List[Candle],
    fvg: FVG,
    current_price: float = 0.0,
    candles_ltf: Optional[List[Candle]] = None,
    ltf_timeframe: str = "5m",
) -> Optional[Tuple[int, str]]:
    """
    Returns (first_touch_timestamp, timeframe) of the true FIRST touch into the 4H FVG zone
    strictly after the FVG was formed (after c3 closed).
    The candle that formed the FVG (c3) can NEVER count as its own touch!
    """
    fvg_close_ts = fvg.close_timestamp

    # 1. If LTF candles are provided, scan for the earliest LTF candle strictly at or after 4H FVG close
    if candles_ltf:
        subsequent_ltf = [c for c in candles_ltf if c.timestamp >= fvg_close_ts]
        for c in subsequent_ltf:
            if fvg.direction == "Bullish":
                if c.low <= fvg.top and c.high >= fvg.bottom:
                    return (c.timestamp, ltf_timeframe)
            else:
                if c.high >= fvg.bottom and c.low <= fvg.top:
                    return (c.timestamp, ltf_timeframe)

    # 2. Otherwise scan 4H candles formed strictly after creation (c4 onwards, c.timestamp > c3.timestamp)
    subsequent_4h = [c for c in candles_4h if c.timestamp > fvg.formed_at]
    first_touch_ts = None
    for c in subsequent_4h:
        if fvg.direction == "Bullish":
            if c.low <= fvg.top and c.high >= fvg.bottom:
                first_touch_ts = c.timestamp
                break
        else:
            if c.high >= fvg.bottom and c.low <= fvg.top:
                first_touch_ts = c.timestamp
                break

    if first_touch_ts is not None:
        return (first_touch_ts, "4h")

    # 3. If live price is currently inside the 4H FVG zone post-close
    if current_price > 0 and fvg.bottom <= current_price <= fvg.top:
        now_ms = int(time.time() * 1000)
        if now_ms >= fvg_close_ts:
            return (now_ms, "live")

    return None


def get_4h_fvg_most_recent_touch_ts(
    candles_4h: List[Candle],
    fvg: FVG,
    current_price: float = 0.0,
    candles_ltf: Optional[List[Candle]] = None,
    ltf_timeframe: str = "5m",
) -> Optional[Tuple[int, bool, str]]:
    """
    Returns (most_recent_touch_timestamp, is_currently_inside, timeframe)
    for determining which 4H FVG is touched most recently.
    """
    fvg_close_ts = fvg.close_timestamp
    now_ms = int(time.time() * 1000)

    # 1. Price is currently inside the zone right now
    if current_price > 0 and fvg.bottom <= current_price <= fvg.top and now_ms >= fvg_close_ts:
        tf = ltf_timeframe if candles_ltf else "live"
        return (now_ms, True, tf)

    # 2. Otherwise scan from newest to oldest for the latest candle touch post-close
    if candles_ltf:
        for c in reversed(candles_ltf):
            if c.timestamp < fvg_close_ts:
                break
            inside = (c.low <= fvg.top and c.high >= fvg.bottom) if fvg.direction == "Bullish" else (c.high >= fvg.bottom and c.low <= fvg.top)
            if inside:
                return (c.timestamp, False, ltf_timeframe)

    subsequent_4h = [c for c in candles_4h if c.timestamp > fvg.formed_at]
    for c in reversed(subsequent_4h):
        inside = (c.low <= fvg.top and c.high >= fvg.bottom) if fvg.direction == "Bullish" else (c.high >= fvg.bottom and c.low <= fvg.top)
        if inside:
            return (c.timestamp, False, "4h")

    return None


def get_most_recent_touched_4h_fvg(
    candles_4h: List[Candle],
    active_fvgs: List[FVG],
    current_price: float = 0.0,
    candles_ltf: Optional[List[Candle]] = None,
    ltf_timeframe: str = "5m",
) -> Optional[TouchedAnchor]:
    """
    Evaluates all active, non-invalidated 4H FVGs and identifies the SINGLE
    most recent touched 4H FVG.
    - Uses Most Recent Touch Time to select which 4H FVG is the active anchor.
    - Records First Touch Time on the selected anchor so LTF FVG scanning begins from First Touch Time.
    Returns None if no 4H FVG has been touched.
    """
    touched_anchors: List[TouchedAnchor] = []

    for fvg in active_fvgs:
        # Check first touch
        first_info = get_4h_fvg_first_touch_ts(
            candles_4h=candles_4h,
            fvg=fvg,
            current_price=current_price,
            candles_ltf=candles_ltf,
            ltf_timeframe=ltf_timeframe,
        )
        if first_info is None:
            continue

        first_ts, first_tf = first_info

        # Check most recent touch
        rec_info = get_4h_fvg_most_recent_touch_ts(
            candles_4h=candles_4h,
            fvg=fvg,
            current_price=current_price,
            candles_ltf=candles_ltf,
            ltf_timeframe=ltf_timeframe,
        )
        rec_ts, is_inside, rec_tf = rec_info if rec_info else (first_ts, False, first_tf)

        touched_anchors.append(
            TouchedAnchor(
                fvg=fvg,
                first_touch_timestamp=first_ts,
                most_recent_touch_timestamp=rec_ts,
                is_currently_inside=is_inside,
                touch_timeframe=first_tf,
            )
        )

    if not touched_anchors:
        return None

    # Sort: currently inside first, then most_recent_touch_timestamp descending
    touched_anchors.sort(
        key=lambda a: (a.is_currently_inside, a.most_recent_touch_timestamp),
        reverse=True,
    )
    return touched_anchors[0]


async def get_most_recent_touched_anchor_for_symbol(
    symbol: str,
    ltf_timeframe: str = "5m",
    client: Optional[Any] = None,
    use_close_invalidation: bool = False,
    candles_4h: Optional[List[Candle]] = None,
    candles_ltf: Optional[List[Candle]] = None,
) -> Optional[TouchedAnchor]:
    """
    Fetches live 4H and LTF candles for symbol and returns the single
    most recent touched 4H FVG anchor. Reuses provided candle series to eliminate redundant API queries.
    """
    cli = client or market_data_provider
    if candles_4h is None:
        raw_4h = await cli.get_last_n_candles(symbol=symbol, timeframe=HTF_TIMEFRAME, n=200)
        if not raw_4h:
            return None
        candles_4h = [Candle.from_dict(c) for c in raw_4h]

    if not candles_4h:
        return None

    current_price = candles_4h[-1].close

    # Get active non-invalidated 4H FVGs via cache, passing already fetched 4H candles
    active_fvgs = await get_active_4h_fvgs_for_symbol(
        symbol=symbol,
        client=cli,
        use_close_invalidation=use_close_invalidation,
        candles_4h=candles_4h,
    )
    if not active_fvgs:
        logger.debug("[ExtremeStrategy] [%s] No active 4H FVGs in cache", symbol)
        return None

    # Fetch LTF candles to pinpoint exact touch timestamp if not already provided
    if candles_ltf is None:
        raw_ltf = await cli.get_last_n_candles(symbol=symbol, timeframe=ltf_timeframe, n=300)
        candles_ltf = [Candle.from_dict(c) for c in raw_ltf] if raw_ltf else None

    anchor = get_most_recent_touched_4h_fvg(
        candles_4h=candles_4h,
        active_fvgs=active_fvgs,
        current_price=current_price,
        candles_ltf=candles_ltf,
        ltf_timeframe=ltf_timeframe,
    )
    if anchor:
        logger.info(
            "[ExtremeStrategy] [%s] 4H ANCHOR TOUCHED: %s FVG [%.4f - %.4f] (Formed: %s, 1st Touch: %s, Price: %.4f)",
            symbol, anchor.fvg.direction, anchor.fvg.bottom, anchor.fvg.top,
            anchor.fvg.formed_time_ist, anchor.first_touch_time_ist, current_price
        )
    else:
        logger.debug("[ExtremeStrategy] [%s] Evaluated %d 4H FVGs -> None currently touched", symbol, len(active_fvgs))
    return anchor


# ==============================================================================
# STEP 3: UNMITIGATED LTF FVG DISCOVERY, EXTREME RANKING & TRADE SETUP
# ==============================================================================
@dataclass
class ExtremeTradeSetup:
    """Represents a validated trade setup ready for execution under strategy_extreme_fvg."""
    symbol: str
    direction: Literal["Bullish", "Bearish"]
    anchor: TouchedAnchor
    ltf_fvg: FVG
    entry_price: float
    stop_loss: float
    risk_r: float
    tp_1r: float
    tp_2r: float
    tp_3r: float
    state: Literal["PENDING_RETRACE", "TRADE_ACTIVE", "TP1_HIT", "TP2_HIT", "TP3_HIT", "STOPPED_OUT", "INVALIDATED"] = "PENDING_RETRACE"
    entry_timestamp: Optional[int] = None
    floating_r: float = 0.0
    completion_target: Literal["1R", "2R", "3R"] = "2R"
    ltf_timeframe: str = "5m"
    all_unmitigated_fvgs: List[FVG] = field(default_factory=list)

    @property
    def risk_pct(self) -> float:
        return (self.risk_r / self.entry_price) * 100 if self.entry_price > 0 else 0.0

    @property
    def is_valid_risk(self) -> bool:
        return self.risk_r > 0

    @property
    def entry_time_ist(self) -> Optional[str]:
        if not self.entry_timestamp:
            return None
        dur = TIMEFRAME_MS.get(self.ltf_timeframe, 5 * 60 * 1000)
        return datetime.fromtimestamp((self.entry_timestamp + dur) / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    @property
    def position_size(self) -> Any:
        """Returns calculated PositionSizeResult based on environment config."""
        try:
            from position_sizing import PositionSizingEngine
            return PositionSizingEngine.calculate(
                entry_price=self.entry_price,
                stop_loss=self.stop_loss,
                symbol=self.symbol,
            )
        except Exception:
            return None

    def calculate_position_size(self, config: Any = None) -> Any:
        """Returns calculated PositionSizeResult for a specific configuration."""
        try:
            from position_sizing import PositionSizingEngine
            return PositionSizingEngine.calculate(
                entry_price=self.entry_price,
                stop_loss=self.stop_loss,
                symbol=self.symbol,
                config=config,
            )
        except Exception:
            return None


def evaluate_ltf_setup_lifecycle(
    ltf_fvg: FVG,
    subsequent_candles: List[Candle],
    current_price: float = 0.0,
    completion_target: Literal["1R", "2R", "3R"] = "2R",
) -> Tuple[str, Optional[int], float]:
    """
    Evaluates the lifecycle state of a candidate LTF FVG from formation across subsequent candles up to current_price.
    Returns: (state, entry_timestamp, floating_r)
    - PENDING_RETRACE: Price has not touched entry yet.
    - TRADE_ACTIVE: Price touched entry, but neither SL nor completion_target has been hit.
    - STOPPED_OUT: Price hit SL after entry.
    - COMPLETED: Price hit completion_target (TP) after entry.
    - INVALIDATED: Price blew through SL before ever touching entry.
    """
    direction = ltf_fvg.direction
    c1, c2, c3 = ltf_fvg.c1, ltf_fvg.c2, ltf_fvg.c3

    if direction == "Bullish":
        entry_price = ltf_fvg.top
        stop_loss = min(c1.low, c2.low, c3.low)
        risk_r = max(0.0, entry_price - stop_loss)
        mult = 1.0 if completion_target == "1R" else (2.0 if completion_target == "2R" else 3.0)
        tp_target = entry_price + mult * risk_r
    else:
        entry_price = ltf_fvg.bottom
        stop_loss = max(c1.high, c2.high, c3.high)
        risk_r = max(0.0, stop_loss - entry_price)
        mult = 1.0 if completion_target == "1R" else (2.0 if completion_target == "2R" else 3.0)
        tp_target = entry_price - mult * risk_r

    if risk_r <= 0:
        return ("INVALIDATED", None, 0.0)

    state = "PENDING_RETRACE"
    entry_ts: Optional[int] = None

    for c in subsequent_candles:
        if state == "PENDING_RETRACE":
            # If candle breached SL before touching entry
            if direction == "Bullish":
                if c.low <= stop_loss and c.high < entry_price:
                    return ("INVALIDATED", None, 0.0)
                if c.low <= entry_price:
                    state = "TRADE_ACTIVE"
                    entry_ts = c.timestamp
            else:
                if c.high >= stop_loss and c.low > entry_price:
                    return ("INVALIDATED", None, 0.0)
                if c.high >= entry_price:
                    state = "TRADE_ACTIVE"
                    entry_ts = c.timestamp

        if state == "TRADE_ACTIVE":
            # Check SL
            if direction == "Bullish" and c.low <= stop_loss:
                return ("STOPPED_OUT", entry_ts, -1.0)
            elif direction == "Bearish" and c.high >= stop_loss:
                return ("STOPPED_OUT", entry_ts, -1.0)

            # Check TP
            if direction == "Bullish" and c.high >= tp_target:
                return ("COMPLETED", entry_ts, mult)
            elif direction == "Bearish" and c.low <= tp_target:
                return ("COMPLETED", entry_ts, mult)

    # Check live price
    if current_price > 0:
        if state == "PENDING_RETRACE":
            if direction == "Bullish":
                if current_price <= stop_loss:
                    return ("INVALIDATED", None, 0.0)
                elif current_price <= entry_price:
                    state = "TRADE_ACTIVE"
                    entry_ts = int(time.time() * 1000)
            else:
                if current_price >= stop_loss:
                    return ("INVALIDATED", None, 0.0)
                elif current_price >= entry_price:
                    state = "TRADE_ACTIVE"
                    entry_ts = int(time.time() * 1000)

        elif state == "TRADE_ACTIVE":
            if direction == "Bullish":
                if current_price <= stop_loss:
                    return ("STOPPED_OUT", entry_ts, -1.0)
                elif current_price >= tp_target:
                    return ("COMPLETED", entry_ts, mult)
            else:
                if current_price >= stop_loss:
                    return ("STOPPED_OUT", entry_ts, -1.0)
                elif current_price <= tp_target:
                    return ("COMPLETED", entry_ts, mult)

    # Calculate floating R if active
    floating_r = 0.0
    if state == "TRADE_ACTIVE" and current_price > 0 and risk_r > 0:
        if direction == "Bullish":
            floating_r = (current_price - entry_price) / risk_r
        else:
            floating_r = (entry_price - current_price) / risk_r

    return (state, entry_ts, floating_r)


def find_unmitigated_ltf_fvgs(
    candles_ltf: List[Candle],
    after_timestamp: int,
    direction: Literal["Bullish", "Bearish"],
    current_price: float = 0.0,
    current_time_ms: Optional[int] = None,
    ltf_timeframe: str = "5m",
    min_gap_pct: float = 0.05,
    completion_target: Literal["1R", "2R", "3R"] = "2R",
) -> List[FVG]:
    """
    Scans candles_ltf for FVGs matching direction that formed strictly AFTER after_timestamp,
    and runs the Trade State Machine:
    - Retains PENDING_RETRACE (waiting for entry)
    - Retains TRADE_ACTIVE (touched entry, floating between Entry and TP/SL)
    - Discards STOPPED_OUT, COMPLETED, and INVALIDATED.
    """
    duration_ms = TIMEFRAME_MS.get(ltf_timeframe, 15 * 60 * 1000)
    now_ms = int(time.time() * 1000) if current_time_ms is None else current_time_ms

    # Only closed LTF candles
    closed_ltf = filter_closed_candles(candles_ltf, duration_ms, current_time_ms=now_ms)
    if len(closed_ltf) < 3:
        return []

    unmitigated: List[FVG] = []

    for i in range(len(closed_ltf) - 2):
        c1 = closed_ltf[i]
        c2 = closed_ltf[i + 1]
        c3 = closed_ltf[i + 2]

        c3_close_ts = c3.timestamp + duration_ms
        # Must be formed strictly at or after the 4H anchor first touch timestamp
        if c3_close_ts < after_timestamp:
            continue

        cand: Optional[FVG] = None
        if direction == "Bullish" and c3.low > c1.high:
            cand = FVG(
                direction="Bullish",
                top=c3.low,
                bottom=c1.high,
                c1=c1,
                c2=c2,
                c3=c3,
                formed_at=c3.timestamp,
                timeframe=ltf_timeframe,
            )
        elif direction == "Bearish" and c3.high < c1.low:
            cand = FVG(
                direction="Bearish",
                top=c1.low,
                bottom=c3.high,
                c1=c1,
                c2=c2,
                c3=c3,
                formed_at=c3.timestamp,
                timeframe=ltf_timeframe,
            )

        if cand is None:
            continue

        # Check minimum gap size filter
        if min_gap_pct > 0 and cand.gap_pct < min_gap_pct:
            continue

        # Evaluate trade state machine across subsequent candles
        subsequent = closed_ltf[i + 3:]
        state, entry_ts, floating_r = evaluate_ltf_setup_lifecycle(
            ltf_fvg=cand,
            subsequent_candles=subsequent,
            current_price=current_price,
            completion_target=completion_target,
        )

        # Only retain setups that are PENDING_RETRACE or TRADE_ACTIVE
        if state in ("PENDING_RETRACE", "TRADE_ACTIVE"):
            cand.lifecycle_state = state
            cand.entry_timestamp = entry_ts
            cand.floating_r = floating_r
            unmitigated.append(cand)

    return unmitigated


def select_extreme_ltf_fvg(
    unmitigated_fvgs: List[FVG],
    direction: Literal["Bullish", "Bearish"],
) -> Optional[FVG]:
    """
    Selects the #1 Extreme FVG from the unmitigated pool:
    - Bullish: Lowest price FVG (deepest/closest to 4H zone) -> min by bottom/midpoint.
    - Bearish: Highest price FVG (deepest/closest to 4H zone) -> max by top/midpoint.
    """
    if not unmitigated_fvgs:
        return None

    if direction == "Bullish":
        # Lowest price has highest probability
        return min(unmitigated_fvgs, key=lambda f: f.bottom)
    else:
        # Highest price has highest probability
        return max(unmitigated_fvgs, key=lambda f: f.top)


def build_extreme_trade_setup(
    symbol: str,
    anchor: TouchedAnchor,
    ltf_fvg: FVG,
    ltf_timeframe: str = "5m",
    completion_target: Literal["1R", "2R", "3R"] = "2R",
    all_unmitigated_fvgs: Optional[List[FVG]] = None,
) -> ExtremeTradeSetup:
    """
    Calculates exact trade setup parameters:
    - Entry: Outer boundary (Bullish: top, Bearish: bottom).
    - Stop Loss: Exact extreme wick across [c1, c2, c3] forming the LTF FVG.
    - 1R, 2R, 3R targets.
    - Preserves state machine fields: state, entry_timestamp, floating_r.
    """
    direction = anchor.fvg.direction
    c1, c2, c3 = ltf_fvg.c1, ltf_fvg.c2, ltf_fvg.c3

    if direction == "Bullish":
        entry_price = ltf_fvg.top
        stop_loss = min(c1.low, c2.low, c3.low)
        risk_r = max(0.0, entry_price - stop_loss)
        tp_1r = entry_price + 1.0 * risk_r
        tp_2r = entry_price + 2.0 * risk_r
        tp_3r = entry_price + 3.0 * risk_r
    else:
        entry_price = ltf_fvg.bottom
        stop_loss = max(c1.high, c2.high, c3.high)
        risk_r = max(0.0, stop_loss - entry_price)
        tp_1r = entry_price - 1.0 * risk_r
        tp_2r = entry_price - 2.0 * risk_r
        tp_3r = entry_price - 3.0 * risk_r

    return ExtremeTradeSetup(
        symbol=symbol,
        direction=direction,
        anchor=anchor,
        ltf_fvg=ltf_fvg,
        entry_price=entry_price,
        stop_loss=stop_loss,
        risk_r=risk_r,
        tp_1r=tp_1r,
        tp_2r=tp_2r,
        tp_3r=tp_3r,
        state=ltf_fvg.lifecycle_state,
        entry_timestamp=ltf_fvg.entry_timestamp,
        floating_r=ltf_fvg.floating_r,
        completion_target=completion_target,
        ltf_timeframe=ltf_timeframe,
        all_unmitigated_fvgs=all_unmitigated_fvgs or [ltf_fvg],
    )


async def get_extreme_setup_for_symbol(
    symbol: str,
    ltf_timeframe: str = "5m",
    client: Optional[Any] = None,
    use_close_invalidation: bool = False,
    min_gap_pct: float = 0.05,
    completion_target: Literal["1R", "2R", "3R"] = "2R",
    session_filter: bool = False,
    weekday_filter: bool = False,
    candles_4h: Optional[List[Candle]] = None,
    candles_ltf: Optional[List[Candle]] = None,
) -> Optional[ExtremeTradeSetup]:
    """
    End-to-end pipeline:
    1. Finds the most recent touched 4H FVG anchor (reusing candle data).
    2. Scans for unmitigated LTF FVGs formed post-touch (with min_gap_pct filter and state machine).
    3. Selects the #1 Extreme FVG (lowest for Bullish, highest for Bearish).
    4. Applies optional formation session/weekday filters on candidate FVG completion time.
    5. Computes Entry, SL, and 1R/2R/3R targets.
    """
    cli = client or market_data_provider

    # Fetch LTF candles once if not provided
    if candles_ltf is None:
        raw_ltf = await cli.get_last_n_candles(symbol=symbol, timeframe=ltf_timeframe, n=300)
        if not raw_ltf:
            return None
        candles_ltf = [Candle.from_dict(c) for c in raw_ltf]

    if not candles_ltf:
        return None

    anchor = await get_most_recent_touched_anchor_for_symbol(
        symbol=symbol,
        ltf_timeframe=ltf_timeframe,
        client=cli,
        use_close_invalidation=use_close_invalidation,
        candles_4h=candles_4h,
        candles_ltf=candles_ltf,
    )
    if not anchor:
        return None

    current_price = candles_ltf[-1].close

    unmitigated = find_unmitigated_ltf_fvgs(
        candles_ltf=candles_ltf,
        after_timestamp=anchor.first_touch_timestamp,
        direction=anchor.fvg.direction,
        current_price=current_price,
        ltf_timeframe=ltf_timeframe,
        min_gap_pct=min_gap_pct,
        completion_target=completion_target,
    )
    if not unmitigated:
        logger.debug("[ExtremeStrategy] [%s] 0 unmitigated LTF FVGs found post-touch (threshold: %.3f%%)", symbol, min_gap_pct)
        return None

    logger.info(
        "[ExtremeStrategy] [%s] Post-touch scan (after %s): Found %d unmitigated %s FVG(s) >= %.3f%% gap",
        symbol, anchor.first_touch_time_ist, len(unmitigated), anchor.fvg.direction, min_gap_pct
    )

    best_ltf = select_extreme_ltf_fvg(unmitigated, anchor.fvg.direction)
    if not best_ltf:
        return None

    logger.info(
        "[ExtremeStrategy] [%s] Selected #1 EXTREME LTF FVG: %s [%.4f - %.4f] (Width: %.4f, Gap: %.3f%%, Formed: %s)",
        symbol, best_ltf.direction, best_ltf.bottom, best_ltf.top, best_ltf.width, best_ltf.gap_pct, best_ltf.formed_time_ist
    )

    if session_filter and not is_in_ny_session(best_ltf.close_timestamp):
        logger.info("[ExtremeStrategy] [%s] Setup rejected by NY Session filter (formed at %s)", symbol, best_ltf.formed_time_ist)
        return None

    if weekday_filter and not is_weekday(best_ltf.close_timestamp):
        logger.info("[ExtremeStrategy] [%s] Setup rejected by Weekday filter (formed at %s)", symbol, best_ltf.formed_time_ist)
        return None

    setup = build_extreme_trade_setup(
        symbol=symbol,
        anchor=anchor,
        ltf_fvg=best_ltf,
        ltf_timeframe=ltf_timeframe,
        completion_target=completion_target,
        all_unmitigated_fvgs=unmitigated,
    )
    logger.info(
        "[ExtremeStrategy] [%s] TRADE SETUP READY: %s | Entry: %.4f | SL: %.4f | 1R: %.4f | 2R: %.4f | 3R: %.4f (Risk: %.2f%%)",
        setup.symbol, setup.direction, setup.entry_price, setup.stop_loss,
        setup.tp_1r, setup.tp_2r, setup.tp_3r, setup.risk_pct
    )
    return setup

