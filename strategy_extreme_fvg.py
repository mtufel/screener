"""
Extreme LTF FVG Strategy Engine (strategy_extreme_fvg.py).
Focuses on:
1. Identifying active 4H Fair Value Gaps strictly on fully closed candles.
2. Dynamic invalidation tracking (wick or close breach).
3. Incremental caching (HTFFVGCache) with bootstrap and delta scans for high-performance execution.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
import os
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from dotenv import load_dotenv
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

    @property
    def width(self) -> float:
        """Absolute price width of the gap."""
        return max(0.0, self.top - self.bottom)

    @property
    def midpoint(self) -> float:
        """Midpoint price of the gap."""
        return (self.top + self.bottom) / 2.0

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

    def is_bootstrapped(self, symbol: str) -> bool:
        """Checks if a symbol has been initialized in the cache."""
        return symbol in self.last_processed_candle_ts

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
        closed_candles = (
            filter_closed_candles(candles_4h, HTF_CANDLE_DURATION_MS, current_time_ms)
            if enforce_closed_filter
            else candles_4h
        )

        if not closed_candles:
            self.active_fvgs[symbol] = []
            self.last_processed_candle_ts[symbol] = 0
            self.last_closed_candles[symbol] = []
            return []

        # Compute all active non-invalidated 4H FVGs
        active = compute_all_active_4h_fvgs(
            closed_candles,
            current_time_ms=current_time_ms,
            use_close_invalidation=use_close_invalidation,
            enforce_closed_filter=False,  # Already filtered above
        )

        self.active_fvgs[symbol] = active
        self.last_processed_candle_ts[symbol] = closed_candles[-1].timestamp
        self.last_closed_candles[symbol] = closed_candles[-2:] if len(closed_candles) >= 2 else closed_candles[:]

        logger.info(
            "[HTF Cache Bootstrap] %s: Initialized with %d active 4H FVG(s). Latest closed bar: %s",
            symbol,
            len(active),
            datetime.fromtimestamp(self.last_processed_candle_ts[symbol] / 1000.0, tz=IST).strftime("%d-%b %I:%M %p"),
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
        if not self.is_bootstrapped(symbol):
            return self.bootstrap(
                symbol=symbol,
                candles_4h=recent_candles_4h,
                current_time_ms=current_time_ms,
                use_close_invalidation=use_close_invalidation,
                enforce_closed_filter=enforce_closed_filter,
            )

        last_ts = self.last_processed_candle_ts.get(symbol, 0)

        # 1. Filter for newly closed candles only (ts > last_ts AND fully closed)
        now_ms = int(time.time() * 1000) if current_time_ms is None else current_time_ms
        new_closed_candles = [
            c for c in recent_candles_4h
            if c.timestamp > last_ts and (not enforce_closed_filter or (c.timestamp + HTF_CANDLE_DURATION_MS) <= now_ms)
        ]
        new_closed_candles.sort(key=lambda c: c.timestamp)

        active = list(self.active_fvgs.get(symbol, []))

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
            prior_candles = self.last_closed_candles.get(symbol, [])
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
            self.last_processed_candle_ts[symbol] = new_closed_candles[-1].timestamp
            self.last_closed_candles[symbol] = combined[-2:]

        # Sort newest first
        active.sort(key=lambda f: f.formed_at, reverse=True)
        self.active_fvgs[symbol] = active
        return active

    def get_active_fvgs(self, symbol: str) -> List[FVG]:
        """Returns the currently active, non-invalidated 4H FVGs for symbol."""
        return list(self.active_fvgs.get(symbol, []))

    def invalidate_cache(self, symbol: Optional[str] = None):
        """Clears cache for a single symbol or all symbols."""
        if symbol:
            self.active_fvgs.pop(symbol, None)
            self.last_processed_candle_ts.pop(symbol, None)
            self.last_closed_candles.pop(symbol, None)
        else:
            self.active_fvgs.clear()
            self.last_processed_candle_ts.clear()
            self.last_closed_candles.clear()


# Global Singleton Cache Instance
htf_fvg_cache = HTFFVGCache()


async def get_active_4h_fvgs_for_symbol(
    symbol: str,
    client: Optional[HyperliquidClient] = None,
    use_close_invalidation: bool = False,
    force_bootstrap: bool = False,
) -> List[FVG]:
    """
    Fetches live 4H candles and returns active non-invalidated 4H FVGs
    using the incremental cache.
    """
    cli = client or hyperliquid_client
    raw = await cli.get_last_n_candles(symbol=symbol, timeframe=HTF_TIMEFRAME, n=200)
    if not raw:
        return []

    candles = [Candle.from_dict(c) for c in raw]
    current_price = candles[-1].close if candles else 0.0

    if force_bootstrap or not htf_fvg_cache.is_bootstrapped(symbol):
        return htf_fvg_cache.bootstrap(
            symbol=symbol,
            candles_4h=candles,
            use_close_invalidation=use_close_invalidation,
            enforce_closed_filter=True,
        )

    return htf_fvg_cache.update_delta(
        symbol=symbol,
        recent_candles_4h=candles,
        current_price=current_price,
        use_close_invalidation=use_close_invalidation,
        enforce_closed_filter=True,
    )


# ==============================================================================
# STEP 2: 4H TOUCH DETECTION & MOST RECENT TOUCHED ANCHOR
# ==============================================================================
@dataclass
class TouchedAnchor:
    """Represents a 4H FVG that has been touched post-close and acts as the single active anchor."""
    fvg: FVG
    touch_timestamp: int          # Timestamp when price entered this touch wave (for post-touch LTF search)
    latest_touch_timestamp: int   # Timestamp of the most recent bar/tick touching the zone (for recency comparison)
    is_currently_inside: bool = False
    touch_timeframe: str = "4h"

    @property
    def touch_time_ist(self) -> str:
        """Formatted touch time in IST with candle interval."""
        duration_ms = TIMEFRAME_MS.get(self.touch_timeframe, HTF_CANDLE_DURATION_MS)
        t_open = datetime.fromtimestamp(self.touch_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p")
        t_close = datetime.fromtimestamp((self.touch_timestamp + duration_ms) / 1000.0, tz=IST).strftime("%I:%M %p IST")
        return f"{t_open} - {t_close}"

    @property
    def latest_touch_time_ist(self) -> str:
        """Formatted most recent touch time in IST."""
        return datetime.fromtimestamp(self.latest_touch_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")


def get_4h_fvg_most_recent_touch(
    candles_4h: List[Candle],
    fvg: FVG,
    current_price: float = 0.0,
    candles_ltf: Optional[List[Candle]] = None,
    ltf_timeframe: str = "15m",
) -> Optional[TouchedAnchor]:
    """
    Finds the MOST RECENT touch for a 4H FVG strictly post-close.
    - If price is currently inside the zone: touch is active NOW (and traces back to start of this entry wave).
    - Otherwise, finds the latest candle that entered the zone.
    The candle that formed the FVG (c3) can NEVER count as its own touch!
    """
    fvg_close_ts = fvg.close_timestamp
    now_ms = int(time.time() * 1000)

    # 1. Price is currently inside the zone right now
    if current_price > 0 and fvg.bottom <= current_price <= fvg.top and now_ms >= fvg_close_ts:
        entry_ts = now_ms
        tf = "live"
        if candles_ltf:
            tf = ltf_timeframe
            for c in reversed(candles_ltf):
                if c.timestamp < fvg_close_ts:
                    break
                inside = (c.low <= fvg.top and c.high >= fvg.bottom) if fvg.direction == "Bullish" else (c.high >= fvg.bottom and c.low <= fvg.top)
                if inside:
                    entry_ts = c.timestamp
                else:
                    break
        elif candles_4h:
            for c in reversed(candles_4h):
                if c.timestamp < fvg.formed_at:
                    break
                inside = (c.low <= fvg.top and c.high >= fvg.bottom) if fvg.direction == "Bullish" else (c.high >= fvg.bottom and c.low <= fvg.top)
                if inside:
                    entry_ts = c.timestamp
                else:
                    break

        return TouchedAnchor(
            fvg=fvg,
            touch_timestamp=entry_ts,
            latest_touch_timestamp=now_ms,
            is_currently_inside=True,
            touch_timeframe=tf,
        )

    # 2. Otherwise scan from newest to oldest for the latest candle touch post-close
    if candles_ltf:
        for c in reversed(candles_ltf):
            if c.timestamp < fvg_close_ts:
                break
            inside = (c.low <= fvg.top and c.high >= fvg.bottom) if fvg.direction == "Bullish" else (c.high >= fvg.bottom and c.low <= fvg.top)
            if inside:
                return TouchedAnchor(
                    fvg=fvg,
                    touch_timestamp=c.timestamp,
                    latest_touch_timestamp=c.timestamp,
                    is_currently_inside=False,
                    touch_timeframe=ltf_timeframe,
                )

    subsequent_4h = [c for c in candles_4h if c.timestamp > fvg.formed_at]
    for c in reversed(subsequent_4h):
        inside = (c.low <= fvg.top and c.high >= fvg.bottom) if fvg.direction == "Bullish" else (c.high >= fvg.bottom and c.low <= fvg.top)
        if inside:
            return TouchedAnchor(
                fvg=fvg,
                touch_timestamp=c.timestamp,
                latest_touch_timestamp=c.timestamp,
                is_currently_inside=False,
                touch_timeframe="4h",
            )

    return None


def get_most_recent_touched_4h_fvg(
    candles_4h: List[Candle],
    active_fvgs: List[FVG],
    current_price: float = 0.0,
    candles_ltf: Optional[List[Candle]] = None,
    ltf_timeframe: str = "15m",
) -> Optional[TouchedAnchor]:
    """
    Evaluates all active, non-invalidated 4H FVGs and identifies the SINGLE
    most recent touched 4H FVG.
    - Any 4H FVG price is currently inside takes highest precedence.
    - Otherwise, selects the one whose latest touch occurred most recently in time.
    Returns None if no 4H FVG has been touched.
    """
    touched_anchors: List[TouchedAnchor] = []

    for fvg in active_fvgs:
        anchor = get_4h_fvg_most_recent_touch(
            candles_4h=candles_4h,
            fvg=fvg,
            current_price=current_price,
            candles_ltf=candles_ltf,
            ltf_timeframe=ltf_timeframe,
        )
        if anchor is not None:
            touched_anchors.append(anchor)

    if not touched_anchors:
        return None

    # Sort: currently inside first, then latest_touch_timestamp descending
    touched_anchors.sort(key=lambda a: (a.is_currently_inside, a.latest_touch_timestamp), reverse=True)
    return touched_anchors[0]


async def get_most_recent_touched_anchor_for_symbol(
    symbol: str,
    ltf_timeframe: str = "15m",
    client: Optional[HyperliquidClient] = None,
    use_close_invalidation: bool = False,
) -> Optional[TouchedAnchor]:
    """
    Fetches live 4H and LTF candles for symbol and returns the single
    most recent touched 4H FVG anchor.
    """
    cli = client or hyperliquid_client
    raw_4h = await cli.get_last_n_candles(symbol=symbol, timeframe=HTF_TIMEFRAME, n=200)
    if not raw_4h:
        return None

    candles_4h = [Candle.from_dict(c) for c in raw_4h]
    current_price = candles_4h[-1].close if candles_4h else 0.0

    # Get active non-invalidated 4H FVGs via cache
    active_fvgs = await get_active_4h_fvgs_for_symbol(
        symbol=symbol,
        client=cli,
        use_close_invalidation=use_close_invalidation,
    )
    if not active_fvgs:
        return None

    # Fetch LTF candles to pinpoint exact touch timestamp
    raw_ltf = await cli.get_last_n_candles(symbol=symbol, timeframe=ltf_timeframe, n=300)
    candles_ltf = [Candle.from_dict(c) for c in raw_ltf] if raw_ltf else None

    return get_most_recent_touched_4h_fvg(
        candles_4h=candles_4h,
        active_fvgs=active_fvgs,
        current_price=current_price,
        candles_ltf=candles_ltf,
        ltf_timeframe=ltf_timeframe,
    )

