"""
Strategy module for Fair Value Gap (FVG) multi-timeframe cryptocurrency screener.
Implements:
1. 4H Higher Timeframe (HTF) FVG Cache with dynamic invalidation (removes FVGs when price shoots past).
2. Configurable 4H FVG Selection: "ANY_VALID" vs "MOST_RECENT".
3. Lower Timeframe (LTF: 1m, 5m, 15m) FVG Formation detection.
4. Two-Stage Lifecycle:
   - PENDING_RETRACE: New LTF FVG formed inside 4H zone; waiting for pullback.
   - ACTIVATED: Price retraces back into the LTF FVG (Trade Entry with 1R, 1.5R, 2R, 3R TPs).
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
import os
import time
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from hyperliquid_client import HyperliquidClient, hyperliquid_client

load_dotenv()

logger = logging.getLogger(__name__)

# IST Timezone (UTC + 5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# ==============================================================================
# STRATEGY CONFIGURATION
# ==============================================================================
HTF_TIMEFRAME: str = os.getenv("HTF_TIMEFRAME", "4h")
LTF_TIMEFRAME: str = os.getenv("LTF_TIMEFRAME", "5m")
LOOKBACK_CANDLES: int = int(os.getenv("LOOKBACK_CANDLES", "50"))
TOP_N_ALERTS: int = int(os.getenv("TOP_N_ALERTS", "10"))
COINS_WHITELIST: str = os.getenv("COINS_WHITELIST", "BTC,ETH,SOL").strip()
HTF_SELECTION_MODE: str = os.getenv("HTF_SELECTION_MODE", "ANY_VALID").strip().upper()  # "ANY_VALID" or "MOST_RECENT"
USE_CLOSE_BASED_INVALIDATION: bool = os.getenv("USE_CLOSE_BASED_INVALIDATION", "false").strip().lower() in ("true", "1", "yes")
MAX_HTF_RETRACE_CANDLES: int = int(os.getenv("MAX_HTF_RETRACE_CANDLES", "18"))  # 18 * 4h = 72 hours / 3 days
SESSION_FILTER_ENABLED: bool = os.getenv("SESSION_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes")

# Scoring Weights
WEIGHT_HTF_TIGHTNESS: float = 0.35
WEIGHT_LTF_TIGHTNESS: float = 0.35
WEIGHT_CENTER_PROXIMITY: float = 0.30


# ==============================================================================
# DATA STRUCTURES
# ==============================================================================
@dataclass
class Candle:
    """Standard OHLCV candle representation."""
    timestamp: int  # Open time in milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Candle":
        """Parse raw candle dictionary from Hyperliquid."""
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
    formed_at: int   # Timestamp (ms) of the newest candle (c3)
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
        duration_ms = TIMEFRAME_MS.get(self.timeframe, 4 * 3600 * 1000)
        return self.formed_at + duration_ms


@dataclass
class TPLevels:
    """Take-Profit price levels based on Risk-to-Reward multiples."""
    r1: float
    r1_5: float
    r2: float
    r3: float
    risk_points: float
    risk_pct: float
    r1_points: float = 0.0
    r1_5_points: float = 0.0
    r2_points: float = 0.0
    r3_points: float = 0.0
    sl_points: float = 0.0

    def __post_init__(self):
        if self.sl_points == 0.0:
            self.sl_points = self.risk_points
        if self.r1_points == 0.0:
            self.r1_points = self.risk_points * 1.0
        if self.r1_5_points == 0.0:
            self.r1_5_points = self.risk_points * 1.5
        if self.r2_points == 0.0:
            self.r2_points = self.risk_points * 2.0
        if self.r3_points == 0.0:
            self.r3_points = self.risk_points * 3.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "r1": round(self.r1, 4),
            "r1_5": round(self.r1_5, 4),
            "r2": round(self.r2, 4),
            "r3": round(self.r3, 4),
            "risk_points": round(self.risk_points, 4),
            "sl_points": round(self.sl_points, 4),
            "r1_points": round(self.r1_points, 4),
            "r1_5_points": round(self.r1_5_points, 4),
            "r2_points": round(self.r2_points, 4),
            "r3_points": round(self.r3_points, 4),
            "risk_pct": round(self.risk_pct, 2),
        }


@dataclass
class Phase1Result:
    """Intermediate result for coins passing Phase 1 (4H FVG)."""
    symbol: str
    direction: Literal["Bullish", "Bearish"]
    htf_fvg: FVG
    current_price: float
    active_4h_fvgs: List[FVG]
    htf_touch_ts: Optional[int] = None  # Timestamp (ms) of first candle that tapped the 4H FVG


@dataclass
class SetupResult:
    """Final qualified setup passing Phase 1 and Phase 2."""
    symbol: str
    direction: Literal["Bullish", "Bearish"]
    stage: Literal["PENDING_RETRACE", "ACTIVATED"]
    htf_fvg: FVG
    ltf_fvg: FVG
    current_price: float
    entry_price: float
    sl_ref: float
    tp_levels: TPLevels
    score: float
    ltf_timeframe: str = "5m"
    htf_mode: str = "ANY_VALID"
    formed_time_ist: str = ""
    fvg_formation_time_ist: str = ""
    entry_time_ist: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "stage": self.stage,
            "price": round(self.current_price, 4),
            "entry_price": round(self.entry_price, 4),
            "sl_ref": round(self.sl_ref, 4),
            "score": round(self.score, 4),
            "ltf_timeframe": self.ltf_timeframe,
            "htf_mode": self.htf_mode,
            "formed_time_ist": self.formed_time_ist,
            "fvg_formation_time_ist": self.fvg_formation_time_ist,
            "entry_time_ist": self.entry_time_ist,
            "tp_levels": self.tp_levels.to_dict(),
            "htf_fvg": {
                "top": round(self.htf_fvg.top, 4),
                "bottom": round(self.htf_fvg.bottom, 4),
                "width": round(self.htf_fvg.width, 4),
                "midpoint": round(self.htf_fvg.midpoint, 4),
            },
            "ltf_fvg": {
                "top": round(self.ltf_fvg.top, 4),
                "bottom": round(self.ltf_fvg.bottom, 4),
                "width": round(self.ltf_fvg.width, 4),
                "midpoint": round(self.ltf_fvg.midpoint, 4),
            },
        }


# ==============================================================================
# FVG COMPUTATION & ACTIVE CACHE WITH INVALIDATION
# ==============================================================================
# Timeframe duration in milliseconds
TIMEFRAME_MS: Dict[str, int] = {
    "1m": 60 * 1000,
    "3m": 3 * 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 3600 * 1000,
    "2h": 2 * 3600 * 1000,
    "4h": 4 * 3600 * 1000,
    "1d": 24 * 3600 * 1000,
}


def compute_fvg(
    candles: List[Candle],
    direction: Optional[Literal["Bullish", "Bearish"]] = None,
    timeframe: str = "5m",
) -> Optional[FVG]:
    """
    Identifies the most recent Fair Value Gap (FVG) from a sequence of candles.
    A valid FVG requires a 3-candle pattern:
    - Bullish FVG: Candle 3 Low > Candle 1 High (Gap between C1 High and C3 Low)
    - Bearish FVG: Candle 3 High < Candle 1 Low (Gap between C3 High and C1 Low)
    """
    if len(candles) < 3:
        return None

    duration_ms = TIMEFRAME_MS.get(timeframe, 5 * 60 * 1000)

    # Search backwards for the most recent completed FVG
    for i in range(len(candles) - 1, 1, -1):
        c1 = candles[i - 2]
        c2 = candles[i - 1]
        c3 = candles[i]

        # Bullish FVG: c3.low > c1.high
        if c3.low > c1.high:
            if direction is None or direction == "Bullish":
                return FVG(
                    direction="Bullish",
                    top=c3.low,
                    bottom=c1.high,
                    c1=c1,
                    c2=c2,
                    c3=c3,
                    formed_at=c3.timestamp,
                    timeframe=timeframe,
                )

        # Bearish FVG: c3.high < c1.low
        if c3.high < c1.low:
            if direction is None or direction == "Bearish":
                return FVG(
                    direction="Bearish",
                    top=c1.low,
                    bottom=c3.high,
                    c1=c1,
                    c2=c2,
                    c3=c3,
                    formed_at=c3.timestamp,
                    timeframe=timeframe,
                )

    return None


def compute_all_active_4h_fvgs(
    candles_4h: List[Candle],
    use_close_invalidation: Optional[bool] = None,
) -> List[FVG]:
    """
    Calculates all historical 4H FVGs on finished candles and tracks their lifecycle.
    
    Invalidation Rule:
    - Bullish 4H FVG [bottom=c1.high, top=c3.low]:
      Invalidated if subsequent price falls completely below `bottom` (c1.high).
    - Bearish 4H FVG [bottom=c3.high, top=c1.low]:
      Invalidated if subsequent price rises completely above `top` (c1.low).
    """
    valid_fvgs: List[FVG] = []
    close_inval = USE_CLOSE_BASED_INVALIDATION if use_close_invalidation is None else use_close_invalidation

    for i in range(len(candles_4h) - 2):
        c1 = candles_4h[i]
        c2 = candles_4h[i + 1]
        c3 = candles_4h[i + 2]

        candidate = None

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

        # Invalidation check on subsequent candles (k > i)
        is_invalidated = False
        for k in range(i + 3, len(candles_4h)):
            sub_candle = candles_4h[k]
            if candidate.direction == "Bullish":
                # Price broke below the gap bottom
                breach = sub_candle.close if close_inval else sub_candle.low
                if breach < candidate.bottom:
                    is_invalidated = True
                    break
            else:
                # Price broke above the gap top
                breach = sub_candle.close if close_inval else sub_candle.high
                if breach > candidate.top:
                    is_invalidated = True
                    break

        if not is_invalidated:
            valid_fvgs.append(candidate)

    # Sort newest first
    valid_fvgs.sort(key=lambda f: f.formed_at, reverse=True)
    return valid_fvgs


def price_in_fvg(price: float, fvg: FVG) -> bool:
    """Checks if price is within the FVG price boundaries [bottom, top]."""
    if fvg is None or price <= 0:
        return False
    return fvg.bottom <= price <= fvg.top


def calculate_tp_levels(direction: Literal["Bullish", "Bearish"], entry_price: float, sl_price: float) -> TPLevels:
    """Calculates 1.0R, 1.5R, 2.0R, and 3.0R Take Profit targets."""
    risk_points = abs(entry_price - sl_price)
    if risk_points <= 0:
        risk_points = entry_price * 0.005  # fallback 0.5% default risk
    risk_pct = (risk_points / entry_price) * 100.0

    if direction == "Bullish":
        r1 = entry_price + (1.0 * risk_points)
        r1_5 = entry_price + (1.5 * risk_points)
        r2 = entry_price + (2.0 * risk_points)
        r3 = entry_price + (3.0 * risk_points)
    else:
        r1 = entry_price - (1.0 * risk_points)
        r1_5 = entry_price - (1.5 * risk_points)
        r2 = entry_price - (2.0 * risk_points)
        r3 = entry_price - (3.0 * risk_points)

    return TPLevels(
        r1=r1,
        r1_5=r1_5,
        r2=r2,
        r3=r3,
        risk_points=risk_points,
        risk_pct=risk_pct,
    )


def score_coin(htf_fvg: FVG, ltf_fvg: FVG, current_price: float, is_pending: bool = False) -> float:
    """Calculates quality score (0.0 to 1.0)."""
    if current_price <= 0:
        return 0.0

    htf_rel_width = htf_fvg.width / current_price
    ltf_rel_width = ltf_fvg.width / current_price

    htf_tightness = max(0.0, min(1.0, 1.0 - (htf_rel_width / 0.05)))
    ltf_tightness = max(0.0, min(1.0, 1.0 - (ltf_rel_width / 0.02)))

    if is_pending:
        # Pending setups awaiting limit fill are scored with neutral/full proximity
        center_proximity = 1.0
    elif ltf_fvg.width > 0:
        dist_to_center = abs(current_price - ltf_fvg.midpoint)
        center_proximity = max(0.0, min(1.0, 1.0 - (dist_to_center / (ltf_fvg.width / 2.0))))
    else:
        center_proximity = 1.0

    score = (
        (WEIGHT_HTF_TIGHTNESS * htf_tightness)
        + (WEIGHT_LTF_TIGHTNESS * ltf_tightness)
        + (WEIGHT_CENTER_PROXIMITY * center_proximity)
    )
    return max(0.0, min(1.0, score))


def is_major_session(ts_ms: Optional[int] = None, session_filter_enabled: Optional[bool] = None) -> bool:
    """
    Checks if current time falls within major trading session hours (London, NY, Asian).
    Controlled by SESSION_FILTER_ENABLED (default False, allowing 24/7 crypto screening).
    """
    enabled = SESSION_FILTER_ENABLED if session_filter_enabled is None else session_filter_enabled
    if not enabled:
        return True

    now_utc = datetime.now(timezone.utc) if ts_ms is None else datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    hour = now_utc.hour
    # Asian (Tokyo: 00-09 UTC), London (07-16 UTC), NY (13-22 UTC) -> Active 00:00 to 22:00 UTC
    return 0 <= hour <= 22


# ==============================================================================
# PIPELINE STAGES
# ==============================================================================
async def get_last_n_candles(
    symbol: str,
    timeframe: str,
    n: int = LOOKBACK_CANDLES,
    client: Optional[HyperliquidClient] = None,
) -> List[Candle]:
    """Fetches finished candles for a symbol and timeframe."""
    cli = client or hyperliquid_client
    raw = await cli.get_last_n_candles(symbol=symbol, timeframe=timeframe, n=n + 1)
    if not raw:
        return []

    # Exclude the currently open (unfinished) candle
    finished_raw = raw[:-1] if len(raw) > 1 else raw
    return [Candle.from_dict(c) for c in finished_raw]


def is_4h_fvg_retraced_after_creation(
    candles_4h: List[Candle],
    fvg: FVG,
    current_price: float,
    max_candles_since_test: Optional[int] = None,
) -> bool:
    """
    Verifies that price has actually RETRACED / TAPPED into the 4H FVG
    STRICTLY AFTER the FVG was formed (on candles strictly after c3, or live price).
    
    If price has never pulled back / retraced into the 4H FVG after creation,
    this FVG remains untested and cannot trigger LTF trade setups.
    """
    # 1. Check if current price is directly inside the 4H FVG right now
    if price_in_fvg(current_price, fvg):
        return True

    # 2. Get candles formed strictly after the FVG creation candle (c3)
    fvg_creation_ts = fvg.formed_at
    subsequent_candles = [c for c in candles_4h if c.timestamp > fvg_creation_ts]
    if not subsequent_candles:
        return False

    lookback_window = MAX_HTF_RETRACE_CANDLES if max_candles_since_test is None else max_candles_since_test
    # 3. Check if any subsequent candle tapped the 4H FVG zone within the retrace lookback window
    recent_subsequent = subsequent_candles[-lookback_window:] if lookback_window > 0 else subsequent_candles
    for c in recent_subsequent:
        if fvg.direction == "Bullish":
            # For Bullish FVG [bottom, top], subsequent price must have dipped into it
            if c.low <= fvg.top and c.high >= fvg.bottom:
                return True
        else:
            # For Bearish FVG [bottom, top], subsequent price must have rallied into it
            if c.high >= fvg.bottom and c.low <= fvg.top:
                return True

    return False


def get_4h_fvg_touch_timestamp(
    candles_4h: List[Candle],
    fvg: FVG,
    current_price: float,
    max_candles_since_test: Optional[int] = None,
    candles_ltf: Optional[List[Candle]] = None,
) -> Optional[int]:
    """
    Returns the exact timestamp (ms) of the true FIRST candle (or live price event) that tapped
    into the 4H FVG zone strictly after the FVG was formed (after c3 closed).
    The candle that formed the FVG (c3) can NEVER count as its own touch!
    """
    # 1. If LTF candles are provided, find the exact first LTF candle strictly after FVG closed
    if candles_ltf:
        subsequent_ltf = [c for c in candles_ltf if c.timestamp >= fvg.close_timestamp]
        for c in subsequent_ltf:
            if fvg.direction == "Bullish":
                if c.low <= fvg.top and c.high >= fvg.bottom:
                    return c.timestamp
            else:
                if c.high >= fvg.bottom and c.low <= fvg.top:
                    return c.timestamp

    # 2. Scan 4H candles formed strictly after creation (c.timestamp > c3.timestamp)
    fvg_creation_ts = fvg.formed_at
    subsequent_candles = [c for c in candles_4h if c.timestamp > fvg_creation_ts]
    if not subsequent_candles:
        if price_in_fvg(current_price, fvg):
            return fvg.close_timestamp
        return None

    # Scan chronologically forward from formation to find the earliest touch
    first_touch_ts = None
    for c in subsequent_candles:
        if fvg.direction == "Bullish":
            if c.low <= fvg.top and c.high >= fvg.bottom:
                first_touch_ts = c.timestamp
                break
        else:
            if c.high >= fvg.bottom and c.low <= fvg.top:
                first_touch_ts = c.timestamp
                break

    if first_touch_ts is None and price_in_fvg(current_price, fvg):
        first_touch_ts = subsequent_candles[-1].timestamp

    if first_touch_ts is None:
        return None

    # Check if the touch happened within the max lookback window (default 18 4H candles = 72h)
    lookback_window = MAX_HTF_RETRACE_CANDLES if max_candles_since_test is None else max_candles_since_test
    if lookback_window > 0 and len(subsequent_candles) > lookback_window:
        cutoff_ts = subsequent_candles[-lookback_window].timestamp
        if first_touch_ts < cutoff_ts:
            # Check if there was a subsequent re-touch inside the active lookback window
            recent_subsequent = subsequent_candles[-lookback_window:]
            for c in recent_subsequent:
                if fvg.direction == "Bullish":
                    if c.low <= fvg.top and c.high >= fvg.bottom:
                        return c.timestamp
                else:
                    if c.high >= fvg.bottom and c.low <= fvg.top:
                        return c.timestamp
            return None

    return first_touch_ts


async def phase1_filter(
    universe: List[str],
    all_mids: Optional[Dict[str, float]] = None,
    htf_mode: str = "ANY_VALID",
    use_close_invalidation: Optional[bool] = None,
    max_htf_retrace_candles: Optional[int] = None,
    client: Optional[HyperliquidClient] = None,
) -> List[Phase1Result]:
    """
    Phase 1: Calculates active 4H FVGs (with invalidation) and checks if price is currently inside
    OR has confirmed a retrace/tap into the 4H FVG zone strictly after creation.
    """
    cli = client or hyperliquid_client
    passed: List[Phase1Result] = []

    for symbol in universe:
        try:
            candles_4h = await get_last_n_candles(symbol=symbol, timeframe=HTF_TIMEFRAME, n=200, client=cli)
            if len(candles_4h) < 3:
                continue

            active_fvgs = compute_all_active_4h_fvgs(candles_4h, use_close_invalidation=use_close_invalidation)
            if not active_fvgs:
                continue

            current_price = 0.0
            if all_mids and symbol in all_mids:
                current_price = all_mids[symbol]
            else:
                current_price = candles_4h[-1].close

            logger.info("[Phase 1 Scan] %s (Price: $%.2f) — %d Active 4H FVG(s)", symbol, current_price, len(active_fvgs))

            # Selection mode: ANY_VALID vs MOST_RECENT
            fvgs_to_check = [active_fvgs[0]] if htf_mode == "MOST_RECENT" else active_fvgs

            for htf_fvg in fvgs_to_check:
                f_dt = datetime.fromtimestamp(htf_fvg.close_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")
                touch_ts = get_4h_fvg_touch_timestamp(candles_4h, htf_fvg, current_price=current_price, max_candles_since_test=max_htf_retrace_candles)
                if touch_ts is not None:
                    t_start = datetime.fromtimestamp(touch_ts / 1000.0, tz=IST).strftime("%d-%b %I:%M %p")
                    t_end = datetime.fromtimestamp((touch_ts + 4 * 3600 * 1000) / 1000.0, tz=IST).strftime("%I:%M %p IST")
                    logger.info("  ✅ 4H %s FVG [$%.2f - $%.2f] formed %s (4H close) -> TOUCHED on 4H bar (%s - %s)", htf_fvg.direction, htf_fvg.bottom, htf_fvg.top, f_dt, t_start, t_end)
                    passed.append(
                        Phase1Result(
                            symbol=symbol,
                            direction=htf_fvg.direction,
                            htf_fvg=htf_fvg,
                            current_price=current_price,
                            active_4h_fvgs=active_fvgs,
                            htf_touch_ts=touch_ts,
                        )
                    )
                else:
                    logger.info("  ⏳ 4H %s FVG [$%.2f - $%.2f] formed %s -> UNTOUCHED (Waiting for retrace)", htf_fvg.direction, htf_fvg.bottom, htf_fvg.top, f_dt)
        except Exception as exc:
            logger.warning("Error checking Phase 1 for %s: %s", symbol, exc)

    logger.info("Phase 1 Complete: %d active touch setup(s) passed for Phase 2 check.", len(passed))
    return passed


async def phase2_check(
    phase1_coin: Phase1Result,
    client: Optional[HyperliquidClient] = None,
    ltf_timeframe: Optional[str] = None,
    htf_mode: str = "ANY_VALID",
) -> Optional[SetupResult]:
    """
    Phase 2: Detects new LTF FVG formation (1m/5m/15m) and checks retrace activation.
    """
    cli = client or hyperliquid_client
    symbol = phase1_coin.symbol
    direction = phase1_coin.direction
    current_price = phase1_coin.current_price
    ltf = ltf_timeframe or os.getenv("LTF_TIMEFRAME", LTF_TIMEFRAME)
    ltf_duration_ms = TIMEFRAME_MS.get(ltf, 5 * 60 * 1000)

    try:
        # Determine sufficient lookback to cover back to the 4H touch timestamp
        needed_candles = 200
        if phase1_coin.htf_touch_ts:
            time_diff_ms = int(time.time() * 1000) - phase1_coin.htf_touch_ts
            needed_candles = max(200, min(1000, int(time_diff_ms / ltf_duration_ms) + 50))

        candles_ltf = await get_last_n_candles(
            symbol=symbol,
            timeframe=ltf,
            n=needed_candles,
            client=cli,
        )
        if len(candles_ltf) < 3:
            return None

        # Refine touch timestamp to exact LTF candle entry strictly after FVG close
        effective_touch_ts = phase1_coin.htf_touch_ts
        refined_to_ltf = False
        if effective_touch_ts is not None and candles_ltf:
            c_start = max(effective_touch_ts, phase1_coin.htf_fvg.close_timestamp)
            c_end = effective_touch_ts + (4 * 3600 * 1000)
            for ltf_c in candles_ltf:
                if c_start <= ltf_c.timestamp <= c_end:
                    if direction == "Bullish":
                        if ltf_c.low <= phase1_coin.htf_fvg.top and ltf_c.high >= phase1_coin.htf_fvg.bottom:
                            effective_touch_ts = ltf_c.timestamp
                            refined_to_ltf = True
                            break
                    else:
                        if ltf_c.high >= phase1_coin.htf_fvg.bottom and ltf_c.low <= phase1_coin.htf_fvg.top:
                            effective_touch_ts = ltf_c.timestamp
                            refined_to_ltf = True
                            break

        if effective_touch_ts:
            if refined_to_ltf:
                t_open = datetime.fromtimestamp(effective_touch_ts / 1000.0, tz=IST).strftime("%d-%b %I:%M %p")
                t_close = datetime.fromtimestamp((effective_touch_ts + ltf_duration_ms) / 1000.0, tz=IST).strftime("%I:%M %p IST")
                touch_str = f"exact {ltf} bar ({t_open} - {t_close})"
            else:
                touch_str = datetime.fromtimestamp(effective_touch_ts / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")
        else:
            touch_str = "None"

        logger.info("[Phase 2 Scan] %s (%s %s) — 4H FVG touched on %s. Searching for nearest LTF FVG post-touch...", symbol, direction, ltf, touch_str)

        # If effective_touch_ts is specified, scan chronologically forward from touch timestamp
        # to find the FIRST LTF FVG formed post 4H touch. Otherwise fallback to backwards search.
        if effective_touch_ts is not None:
            candidate_indices = [
                i for i in range(2, len(candles_ltf))
                if candles_ltf[i].timestamp > effective_touch_ts
            ]
        else:
            candidate_indices = list(range(len(candles_ltf) - 1, 1, -1))

        for i in candidate_indices:
            c1 = candles_ltf[i - 2]
            c2 = candles_ltf[i - 1]
            c3 = candles_ltf[i]

            # Bullish FVG: c3.low > c1.high | Bearish FVG: c3.high < c1.low
            is_fvg = (c3.low > c1.high) if direction == "Bullish" else (c3.high < c1.low)
            if not is_fvg:
                continue

            ltf_fvg = FVG(
                direction=direction,
                top=c3.low if direction == "Bullish" else c1.low,
                bottom=c1.high if direction == "Bullish" else c3.high,
                c1=c1,
                c2=c2,
                c3=c3,
                formed_at=c3.timestamp,
                timeframe=ltf,
            )

            ltf_open_dt = datetime.fromtimestamp(c3.timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p")
            ltf_close_dt = datetime.fromtimestamp(ltf_fvg.close_timestamp / 1000.0, tz=IST).strftime("%I:%M %p IST")
            logger.info("  🎯 Found Nearest LTF %s FVG [%.2f - %.2f] formed %s (bar %s - %s)", direction, ltf_fvg.bottom, ltf_fvg.top, ltf_close_dt, ltf_open_dt, ltf_close_dt)

            # Determine Stop Loss reference
            if direction == "Bullish":
                sl_ref = min(c1.low, c2.low, c3.low)
                if sl_ref >= current_price:
                    continue
            else:
                sl_ref = max(c1.high, c2.high, c3.high)
                if sl_ref <= current_price:
                    continue

            fvg_formation_time_ist = datetime.fromtimestamp(ltf_fvg.close_timestamp / 1000.0, tz=IST).strftime("%d-%b-%Y %I:%M %p IST")

            # Stage classification & Retrace Detection:
            # Retrace MUST happen on a candle or tick AFTER the FVG formation candle
            retrace_candle = None
            retrace_idx = None
            if i < len(candles_ltf) - 1:
                for idx in range(i + 1, len(candles_ltf)):
                    chk_c = candles_ltf[idx]
                    if price_in_fvg(chk_c.low, ltf_fvg) or price_in_fvg(chk_c.high, ltf_fvg) or price_in_fvg(chk_c.open, ltf_fvg) or price_in_fvg(chk_c.close, ltf_fvg):
                        retrace_candle = chk_c
                        retrace_idx = idx
                        break

            if retrace_candle is not None:
                stage: Literal["PENDING_RETRACE", "ACTIVATED"] = "ACTIVATED"
                if direction == "Bullish":
                    entry_price = retrace_candle.open if retrace_candle.open <= ltf_fvg.top else ltf_fvg.top
                else:
                    entry_price = retrace_candle.open if retrace_candle.open >= ltf_fvg.bottom else ltf_fvg.bottom
                entry_time_ist = datetime.fromtimestamp(retrace_candle.timestamp / 1000.0, tz=IST).strftime("%d-%b-%Y %I:%M %p IST")
            elif price_in_fvg(current_price, ltf_fvg):
                stage = "ACTIVATED"
                entry_price = current_price
                entry_time_ist = datetime.now(IST).strftime("%d-%b-%Y %I:%M %p IST") + " (Live)"
            else:
                stage = "PENDING_RETRACE"
                # For pending setups, realistic limit fill is expected at the FVG boundary
                entry_price = ltf_fvg.top if direction == "Bullish" else ltf_fvg.bottom
                entry_time_ist = "⏳ Awaiting Retrace"

            # Calculate TP levels (1.0R, 1.5R, 2.0R, 3.0R)
            tp_levels = calculate_tp_levels(direction=direction, entry_price=entry_price, sl_price=sl_ref)

            # Invalidation Check (SL Hit or 2R Target TP Hit):
            # If trade is ACTIVATED, check candles from retrace_idx onwards.
            # If trade is PENDING_RETRACE, check candles from formation candle i + 1 onwards.
            is_closed = False
            start_inval_idx = retrace_idx if retrace_idx is not None else i + 1
            for k in range(start_inval_idx, len(candles_ltf)):
                chk_c = candles_ltf[k]
                if direction == "Bullish":
                    if chk_c.low <= sl_ref or (stage == "ACTIVATED" and chk_c.high >= tp_levels.r2):
                        is_closed = True
                        break
                else:
                    if chk_c.high >= sl_ref or (stage == "ACTIVATED" and chk_c.low <= tp_levels.r2):
                        is_closed = True
                        break

            # Also check live tick price
            if direction == "Bullish" and (current_price <= sl_ref or (stage == "ACTIVATED" and current_price >= tp_levels.r2)):
                is_closed = True
            elif direction == "Bearish" and (current_price >= sl_ref or (stage == "ACTIVATED" and current_price <= tp_levels.r2)):
                is_closed = True

            if is_closed:
                continue

            # Found a valid active open or pending setup!
            score = score_coin(
                htf_fvg=phase1_coin.htf_fvg,
                ltf_fvg=ltf_fvg,
                current_price=current_price,
                is_pending=(stage == "PENDING_RETRACE"),
            )

            logger.info(
                "Phase 2 [%s] for %s [%s] (%s): Price=%.4f, Entry=%.4f, SL=%.4f, 2R=%.4f (FVG Formed=%s, Entry=%s)",
                stage,
                symbol,
                direction,
                ltf,
                current_price,
                entry_price,
                sl_ref,
                tp_levels.r2,
                fvg_formation_time_ist,
                entry_time_ist,
            )

            return SetupResult(
                symbol=symbol,
                direction=direction,
                stage=stage,
                htf_fvg=phase1_coin.htf_fvg,
                ltf_fvg=ltf_fvg,
                current_price=current_price,
                entry_price=entry_price,
                sl_ref=sl_ref,
                tp_levels=tp_levels,
                score=score,
                ltf_timeframe=ltf,
                htf_mode=htf_mode,
                formed_time_ist=datetime.now(IST).strftime("%d-%b %I:%M %p IST"),
                fvg_formation_time_ist=fvg_formation_time_ist,
                entry_time_ist=entry_time_ist,
            )

        return None
    except Exception as exc:
        logger.warning("Error in Phase 2 check for %s (%s): %s", symbol, ltf, exc)
        return None


async def run_screener(
    top_n: int = TOP_N_ALERTS,
    ltf_timeframe: Optional[str] = None,
    htf_mode: Optional[str] = None,
    use_close_invalidation: Optional[bool] = None,
    max_htf_retrace_candles: Optional[int] = None,
    session_filter_enabled: Optional[bool] = None,
    client: Optional[HyperliquidClient] = None,
) -> List[SetupResult]:
    """
    Executes the full 2-stage screener workflow across the target universe.
    """
    cli = client or hyperliquid_client
    ltf = ltf_timeframe or os.getenv("LTF_TIMEFRAME", LTF_TIMEFRAME)
    h_mode = htf_mode or os.getenv("HTF_SELECTION_MODE", HTF_SELECTION_MODE)

    if not is_major_session(session_filter_enabled=session_filter_enabled):
        logger.info("Outside major trading sessions and session filter is enabled. Skipping scan.")
        return []

    universe = await cli.get_universe()
    if not universe:
        return []

    whitelist_raw = os.getenv("COINS_WHITELIST", COINS_WHITELIST).strip()
    if whitelist_raw and whitelist_raw.upper() != "ALL":
        from hyperliquid_client import SYMBOL_ALIASES
        raw_allowed = [c.strip().upper() for c in whitelist_raw.split(",") if c.strip()]
        allowed = set(raw_allowed)
        for raw_sym in raw_allowed:
            if raw_sym in SYMBOL_ALIASES:
                allowed.add(SYMBOL_ALIASES[raw_sym])
        universe = [c for c in universe if c.upper() in allowed]

    all_mids = await cli.get_all_mids()
    logger.info(
        "Scanning universe of %d coins (HTF=%s, Mode=%s, LTF=%s, CloseInval=%s, RetraceWin=%s)...",
        len(universe),
        HTF_TIMEFRAME,
        h_mode,
        ltf,
        use_close_invalidation,
        max_htf_retrace_candles,
    )

    # 1. Phase 1 Filter
    phase1_candidates = await phase1_filter(
        universe,
        all_mids=all_mids,
        htf_mode=h_mode,
        use_close_invalidation=use_close_invalidation,
        max_htf_retrace_candles=max_htf_retrace_candles,
        client=cli,
    )
    if not phase1_candidates:
        return []

    # 2. Phase 2 Checks
    qualified_setups: List[SetupResult] = []
    batch_size = 15
    for i in range(0, len(phase1_candidates), batch_size):
        batch = phase1_candidates[i : i + batch_size]
        phase2_tasks = [phase2_check(cand, client=cli, ltf_timeframe=ltf, htf_mode=h_mode) for cand in batch]
        phase2_raw = await asyncio.gather(*phase2_tasks, return_exceptions=True)

        for res in phase2_raw:
            if isinstance(res, SetupResult):
                qualified_setups.append(res)
            elif isinstance(res, Exception):
                logger.warning("Phase 2 exception: %s", res)

    # Sort: ACTIVATED setups first, then sorted by quality score descending
    qualified_setups.sort(key=lambda s: (1 if s.stage == "ACTIVATED" else 0, s.score), reverse=True)
    return qualified_setups[:top_n]
