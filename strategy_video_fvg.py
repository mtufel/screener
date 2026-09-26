"""
Strategy 3 — Video FVG Engine (strategy_video_fvg.py).

Two-timeframe FVG system using a 4H FVG as directional anchor and an LTF
(1m / 5m / 15m) FVG as the entry trigger. Distinct from Strategy 2 (Extreme FVG):
  - Anchor: most recent closed 4H FVG (not most-recent-touched)
  - Confirmation: HTF respect + aggressive rejection required before LTF scanning
  - Selection: FIRST LTF FVG after HTF confirmation (not deepest/extreme)
  - Target: 3:1 minimum (vs Strategy 2's 2:1)

Pure setup-detection functions (``find_4h_fvgs``, ``is_htf_respected``,
``find_first_ltf_fvg``, ``calc_entry_params``) are side-effect free and reused
by ``backtest_video_fvg.py`` for historical forward-simulation.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple

from candle_store import TIMEFRAME_MS  # noqa: F401 — re-exported for backtest_video_fvg
from market_data_provider import market_data_provider
from session_filter import SessionFilterConfig

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
HTF_TIMEFRAME = "4h"
HTF_CANDLE_DURATION_MS = 4 * 3600 * 1000


# ==============================================================================
# Shared Candle type (mirrors strategy_extreme_fvg.Candle)
# ==============================================================================

@dataclass
class Candle:
    """Standard OHLCV Candle (open-timestamp in ms)."""
    timestamp: int
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


# ==============================================================================
# 4H FVG detection — D2: most recent closed FVG selected as anchor
# ==============================================================================

def _three_candle_fvg(c1: Candle, c2: Candle, c3: Candle, timeframe: str) -> Optional[Dict]:
    """Build a plain-dict FVG from a 3-candle imbalance window (mirrors Strategy 2)."""
    if c3.low > c1.high:
        return {
            "direction": "Bullish",
            "top": c3.low,
            "bottom": c1.high,
            "formed_at": c3.timestamp,
            "close_timestamp": c3.timestamp + TIMEFRAME_MS.get(timeframe, 5 * 60 * 1000),
            "c1": c1, "c2": c2, "c3": c3,
            "timeframe": timeframe,
        }
    if c3.high < c1.low:
        return {
            "direction": "Bearish",
            "top": c1.low,
            "bottom": c3.high,
            "formed_at": c3.timestamp,
            "close_timestamp": c3.timestamp + TIMEFRAME_MS.get(timeframe, 5 * 60 * 1000),
            "c1": c1, "c2": c2, "c3": c3,
            "timeframe": timeframe,
        }
    return None


def find_4h_fvgs(candles_4h: List[Candle]) -> List[Dict]:
    """Find all 4H FVGs, returned most-recent-first."""
    if len(candles_4h) < 3:
        return []
    fvgs = []
    for i in range(len(candles_4h) - 2):
        fvg = _three_candle_fvg(candles_4h[i], candles_4h[i + 1], candles_4h[i + 2], "4h")
        if fvg:
            fvgs.append(fvg)
    fvgs.sort(key=lambda f: f["formed_at"], reverse=True)
    return fvgs


def select_anchor_4h(fvgs: List[Dict]) -> Optional[Dict]:
    """Select the most recently closed 4H FVG as the directional anchor."""
    return fvgs[0] if fvgs else None


# ==============================================================================
# HTF Respect Confirmation — D3
# ==============================================================================

def _is_strong_body(
    candle: Candle,
    direction: Literal["Bullish", "Bearish"],
    threshold: float = 0.5,
) -> bool:
    """True when candle body >= threshold fraction of its range, closing in ``direction``."""
    span = candle.high - candle.low
    if span <= 0:
        return False
    body = abs(candle.close - candle.open)
    if body / span < threshold:
        return False
    if direction == "Bullish" and candle.close > candle.open:
        return True
    if direction == "Bearish" and candle.close < candle.open:
        return True
    return False


def _htf_respect_details(
    anchor: Dict,
    candles_4h: List[Candle],
    htf_confirm_body_pct: float = 0.5,
) -> Tuple[bool, int]:
    """Return ``(confirmed, confirm_close_ts)``.

    Confirmed when price returns to the anchor zone and the next closed 4H candle
    prints a strong directional body (>= htf_confirm_body_pct of range) away from it.
    ``confirm_close_ts`` is the close timestamp of that confirmation candle.
    """
    anchor_bottom = anchor["bottom"]
    anchor_top = anchor["top"]
    direction = anchor["direction"]
    anchor_close_ts = anchor["close_timestamp"]

    post_anchor = [c for c in candles_4h if c.timestamp >= anchor_close_ts]
    if len(post_anchor) < 2:
        return False, 0

    touch_idx = None
    for i, c in enumerate(post_anchor):
        if direction == "Bullish":
            if c.low <= anchor_top and c.high >= anchor_bottom:
                touch_idx = i
                break
        else:
            if c.high >= anchor_bottom and c.low <= anchor_top:
                touch_idx = i
                break
    if touch_idx is None:
        return False, 0

    confirm_idx = touch_idx + 1
    if confirm_idx >= len(post_anchor):
        return False, 0
    confirm = post_anchor[confirm_idx]
    if not _is_strong_body(confirm, direction, threshold=htf_confirm_body_pct):
        return False, 0
    return True, confirm.timestamp + HTF_CANDLE_DURATION_MS


def is_htf_respected(
    anchor: Dict,
    candles_4h: List[Candle],
    htf_confirm_body_pct: float = 0.5,
) -> bool:
    """True when HTF respect confirmation is satisfied (see ``_htf_respect_details``)."""
    confirmed, _ = _htf_respect_details(anchor, candles_4h, htf_confirm_body_pct)
    return confirmed


# ==============================================================================
# LTF FVG detection — FIRST selection (not deepest/extreme)
# ==============================================================================

def _ltf_three_candle_fvg(c1: Candle, c2: Candle, c3: Candle, ltf_tf: str) -> Optional[Dict]:
    """Like ``_three_candle_fvg`` for LTF bars."""
    return _three_candle_fvg(c1, c2, c3, ltf_tf)


def find_first_ltf_fvg(
    candles_ltf: List[Candle],
    direction: Literal["Bullish", "Bearish"],
    min_gap_pct: float,
    formed_after: int,
    ltf_tf: str = "5m",
) -> Optional[Dict]:
    """Return the FIRST LTF FVG in ``direction`` closing after ``formed_after`` (>= min_gap_pct)."""
    ltf_dur = TIMEFRAME_MS.get(ltf_tf, 5 * 60 * 1000)
    min_gap = min_gap_pct / 100.0  # convert from % to fraction

    for i in range(len(candles_ltf) - 2):
        c1, c2, c3 = candles_ltf[i], candles_ltf[i + 1], candles_ltf[i + 2]
        fvg = _ltf_three_candle_fvg(c1, c2, c3, ltf_tf)
        if fvg is None:
            continue
        if fvg["direction"] != direction:
            continue
        if fvg["close_timestamp"] <= formed_after:
            continue

        mid = (fvg["top"] + fvg["bottom"]) / 2.0
        gap_pct = ((fvg["top"] - fvg["bottom"]) / mid) * 100.0 if mid > 0 else 0.0
        if gap_pct < min_gap_pct:
            continue

        return fvg

    return None


# ==============================================================================
# Entry / SL / TP calculation
# ==============================================================================

def calc_entry_params(ltf_fvg: Dict, direction: Literal["Bullish", "Bearish"]) -> Dict:
    """
    Calculate entry, stop loss, and 1R/2R/3R targets.

    Entry: outer FVG boundary — Bullish at the bottom, Bearish at the top.
    SL: at the formation-candle wick extreme (not beyond it).
    Targets: 1R/2R/3R from entry.
    """
    c1, c2, c3 = ltf_fvg["c1"], ltf_fvg["c2"], ltf_fvg["c3"]

    if direction == "Bullish":
        entry_price = ltf_fvg["bottom"]
        stop_loss = min(c1.low, c2.low, c3.low)
        risk_r = max(0.0, entry_price - stop_loss)
        tp_1r = entry_price + 1.0 * risk_r
        tp_2r = entry_price + 2.0 * risk_r
        tp_3r = entry_price + 3.0 * risk_r
    else:
        entry_price = ltf_fvg["top"]
        stop_loss = max(c1.high, c2.high, c3.high)
        risk_r = max(0.0, stop_loss - entry_price)
        tp_1r = entry_price - 1.0 * risk_r
        tp_2r = entry_price - 2.0 * risk_r
        tp_3r = entry_price - 3.0 * risk_r

    return {
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "risk_r": risk_r,
        "tp_1r": tp_1r,
        "tp_2r": tp_2r,
        "tp_3r": tp_3r,
    }


# ==============================================================================
# VideoFVGSetup dataclass
# ==============================================================================

@dataclass
class VideoFVGSetup:
    """A validated Video FVG trade setup ready for execution.

    Mirrors ``ExtremeTradeSetup``'s field names so the ledger and dashboard accept it
    unchanged. Strategy-specific ``anchor`` is a plain Dict (JSON-serialisable) and is
    picked up by the existing ``getattr(setup, "anchor", None)`` guard in the daemon.
    """
    symbol: str
    direction: Literal["Bullish", "Bearish"]
    anchor: Dict                      # 4H FVG: {bottom, top, formed_at, close_timestamp, direction}
    ltf_fvg: Dict                     # LTF FVG: {bottom, top, formed_at, close_timestamp, ...}
    entry_price: float
    stop_loss: float
    risk_r: float
    tp_1r: float
    tp_2r: float
    tp_3r: float
    state: Literal[
        "PENDING_RETRACE", "TRADE_ACTIVE", "TP1_HIT",
        "TP2_HIT", "TP3_HIT", "STOPPED_OUT", "INVALIDATED",
    ] = "PENDING_RETRACE"
    entry_timestamp: Optional[int] = None
    floating_r: float = 0.0
    completion_target: Literal["1R", "2R", "3R"] = "3R"
    ltf_timeframe: str = "5m"
    htf_confirmed: bool = False

    @property
    def risk_pct(self) -> float:
        return (self.risk_r / self.entry_price) * 100.0 if self.entry_price > 0 else 0.0

    @property
    def is_valid_risk(self) -> bool:
        return self.risk_r > 0

    @property
    def entry_time_ist(self) -> Optional[str]:
        if not self.entry_timestamp:
            return None
        dur = TIMEFRAME_MS.get(self.ltf_timeframe, 5 * 60 * 1000)
        return datetime.fromtimestamp(
            (self.entry_timestamp + dur) / 1000.0, tz=IST
        ).strftime("%d-%b %I:%M %p IST")


# ==============================================================================
# Main entry point — mirrors get_extreme_setup_for_symbol
# ==============================================================================

async def get_video_setup_for_symbol(
    symbol: str,
    ltf_timeframe: str = "5m",
    client: Any = None,
    min_gap_pct: float = 0.03,
    htf_confirm_body_pct: float = 0.5,
    completion_target: Literal["1R", "2R", "3R"] = "3R",
    session_filter: bool = False,
    weekday_filter: bool = False,
    sessions: Optional[str] = None,
    session_config: Optional[SessionFilterConfig] = None,
    candles_4h: Optional[List[Candle]] = None,
    candles_ltf: Optional[List[Candle]] = None,
) -> Optional[VideoFVGSetup]:
    """
    End-to-end Video FVG pipeline:

    1. Fetch 4H candles, detect the most recent 4H FVG (anchor).
    2. Confirm HTF respect (strong body candle after zone touch/rejection).
    3. Scan LTF candles for the FIRST FVG after confirmation in anchor direction.
    4. Calculate entry / SL / 3R target.
    5. Apply optional session/weekday filter on LTF FVG formation time.
    """
    if session_config is None:
        session_config = SessionFilterConfig.from_legacy(
            session_filter=session_filter,
            weekday_filter=weekday_filter,
            sessions=sessions,
        )

    cli = client or market_data_provider

    # 1. Fetch + detect 4H anchor
    if candles_4h is None:
        raw_4h = await cli.get_last_n_candles(symbol=symbol, timeframe=HTF_TIMEFRAME, n=200)
        if not raw_4h:
            return None
        candles_4h = [Candle.from_dict(c) for c in raw_4h]
    if not candles_4h or len(candles_4h) < 3:
        return None

    fvgs_4h = find_4h_fvgs(candles_4h)
    anchor = select_anchor_4h(fvgs_4h)
    if anchor is None:
        logger.debug("[VideoFVG] [%s] No 4H FVG found", symbol)
        return None

    # 2. HTF respect confirmation
    confirmed, confirm_ts = _htf_respect_details(anchor, candles_4h, htf_confirm_body_pct)
    if not confirmed:
        logger.debug(
            "[VideoFVG] [%s] HTF respect not confirmed for %s FVG [%.4f - %.4f]",
            symbol, anchor["direction"], anchor["bottom"], anchor["top"],
        )
        return None

    logger.info(
        "[VideoFVG] [%s] HTF anchor confirmed: %s FVG [%.4f - %.4f]",
        symbol, anchor["direction"], anchor["bottom"], anchor["top"],
    )

    # 3. First LTF FVG after confirmation
    if candles_ltf is None:
        raw_ltf = await cli.get_last_n_candles(symbol=symbol, timeframe=ltf_timeframe, n=500)
        if not raw_ltf:
            return None
        candles_ltf = [Candle.from_dict(c) for c in raw_ltf]
    if not candles_ltf or len(candles_ltf) < 3:
        return None

    direction = anchor["direction"]
    ltf_dur = TIMEFRAME_MS.get(ltf_timeframe, 5 * 60 * 1000)
    ltf_scan_from = confirm_ts + ltf_dur  # scan from after confirmation candle closed

    first_ltf_fvg = find_first_ltf_fvg(
        candles_ltf=candles_ltf,
        direction=direction,
        min_gap_pct=min_gap_pct,
        formed_after=ltf_scan_from,
        ltf_tf=ltf_timeframe,
    )
    if first_ltf_fvg is None:
        logger.debug("[VideoFVG] [%s] No LTF FVG found after HTF confirmation", symbol)
        return None

    # 5. Optional session / weekday filter on LTF FVG formation time
    if session_config.is_fvg_valid(first_ltf_fvg["close_timestamp"]) is False and (
        session_config.fvg_sessions.strip().upper() != "ALL" or session_config.fvg_weekdays_only
    ):
        logger.debug("[VideoFVG] [%s] LTF FVG rejected by session filter", symbol)
        return None

    # 4. Entry params
    params = calc_entry_params(first_ltf_fvg, direction)

    logger.info(
        "[VideoFVG] [%s] Setup: %s entry=%.4f SL=%.4f risk=%.4f tp3R=%.4f",
        symbol, direction, params["entry_price"], params["stop_loss"],
        params["risk_r"], params["tp_3r"],
    )

    return VideoFVGSetup(
        symbol=symbol,
        direction=direction,
        anchor=anchor,
        ltf_fvg=first_ltf_fvg,
        entry_price=params["entry_price"],
        stop_loss=params["stop_loss"],
        risk_r=params["risk_r"],
        tp_1r=params["tp_1r"],
        tp_2r=params["tp_2r"],
        tp_3r=params["tp_3r"],
        completion_target=completion_target,
        ltf_timeframe=ltf_timeframe,
        htf_confirmed=True,
    )
