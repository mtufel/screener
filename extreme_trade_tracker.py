"""
Live Trade History & State Tracking Manager for Extreme LTF Strategy (Strategy 2).

Tracks every live trade setup identified by the background daemon from discovery to closure:
- PENDING_RETRACE: Order placed, waiting for touch.
- TRADE_ACTIVE: Entry filled, tracking floating R and Max MFE in real-time.
- COMPLETED_TP: Target (1R/2R/3R) achieved (+2.0R / +1.0R / +3.0R).
- STOPPED_OUT: Hit Stop Loss (-1.0R).
- Persists history state across server reloads to data/extreme_live_trades.json.
"""

import json
import logging
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from session_filter import SessionFilterConfig, is_in_ny_session, is_weekday
from hyperliquid_client import lookup_mid
# Canonical timeframe table shared app-wide (defined in candle_store).
from candle_store import TIMEFRAME_MS

logger = logging.getLogger("extreme_trade_tracker")
IST = timezone(timedelta(hours=5, minutes=30))

APP_ENV = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "local")).strip().lower()
IS_PRODUCTION = APP_ENV in ("production", "prod", "server")
DEFAULT_STORAGE_FILE = "data/extreme_live_trades.json" if IS_PRODUCTION else f"data/extreme_live_trades_{APP_ENV or 'local'}.json"
PERSISTENCE_FILE = os.getenv("EXTREME_LIVE_TRADES_FILE", DEFAULT_STORAGE_FILE)

# A PENDING_RETRACE record whose scanner setup is absent for this many consecutive
# scan-valid cycles transitions to INVALIDATED (default 40 cycles ~ 20 min at 30s).
PENDING_ABSENT_EXPIRY_CYCLES = int(os.getenv("EXTREME_PENDING_EXPIRY_CYCLES", "40"))


def _ts_to_ist(ts_ms: int) -> str:
    """Formats an epoch-milliseconds timestamp as the ledger's IST display string."""
    return datetime.fromtimestamp(int(ts_ms) / 1000, IST).strftime("%d-%b %I:%M %p IST")


def _candle_close_ist(ts_ms: int, timeframe: str = "5m") -> str:
    """Formats a candle's ending/close time in IST (candle open ts + duration)."""
    dur = TIMEFRAME_MS.get(timeframe, 5 * 60 * 1000)
    return datetime.fromtimestamp((int(ts_ms) + dur) / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")


def _candle_ts(c: Any) -> int:
    return getattr(c, "timestamp", c.get("t", 0) if isinstance(c, dict) else 0)


def _candle_high(c: Any) -> float:
    return getattr(c, "high", c.get("h", 0.0) if isinstance(c, dict) else 0.0)


def _candle_low(c: Any) -> float:
    return getattr(c, "low", c.get("l", 0.0) if isinstance(c, dict) else 0.0)


# Completion target -> (R multiple, TrackedExtremeTrade attribute holding the target price)
_TP_TARGETS: Dict[str, Tuple[float, str]] = {
    "1R": (1.0, "tp_1r"),
    "2R": (2.0, "tp_2r"),
    "3R": (3.0, "tp_3r"),
}


def _resolve_tp_target(trade: Any) -> Tuple[float, float]:
    """Resolves (target_price, r_multiple) for a trade's completion target (unknown values fall back to 3R)."""
    mult, attr = _TP_TARGETS.get(trade.completion_target, _TP_TARGETS["3R"])
    return float(getattr(trade, attr)), mult


def _update_mfe(trade: Any, extreme_price: float, risk_r: float) -> None:
    """Tracks the favorable-excursion high-water mark (highs for longs, lows for shorts)."""
    if trade.direction == "Bullish":
        trade.max_favorable_price = max(trade.max_favorable_price or trade.entry_price, extreme_price)
        trade.mfe_r = max(trade.mfe_r, round((trade.max_favorable_price - trade.entry_price) / risk_r, 2))
    else:
        trade.max_favorable_price = min(trade.max_favorable_price or trade.entry_price, extreme_price)
        trade.mfe_r = max(trade.mfe_r, round((trade.entry_price - trade.max_favorable_price) / risk_r, 2))


def _close_trade(trade: Any, state: str, realized_r: float, status_detail: str,
                 exit_ts: int, closed_at_ist: str, duration_min: Optional[int] = None) -> None:
    """Single mutation point for terminal trade state: stamps exit evidence and realized R.

    Invalidations pass realized_r=0.0 (they never realize R) and omit duration_min
    (there is no holding period before entry).
    """
    trade.state = state
    trade.realized_r = realized_r
    trade.status_detail = status_detail
    trade.closed_at_ist = closed_at_ist
    trade.closed_timestamp = exit_ts
    if duration_min is not None:
        trade.duration_min = duration_min


@dataclass
class TrackedExtremeTrade:
    symbol: str
    direction: str  # "Bullish" | "Bearish"
    entry_price: float
    stop_loss: float
    risk_r: float
    risk_pct: float
    tp_1r: float
    tp_2r: float
    tp_3r: float
    completion_target: str
    trade_id: str = ""
    ltf_timeframe: str = "15m"
    htf_anchor: Dict[str, Any] = field(default_factory=dict)
    ltf_fvg: Dict[str, Any] = field(default_factory=dict)
    state: str = "PENDING_RETRACE"  # "PENDING_RETRACE" | "TRADE_ACTIVE" | "COMPLETED_TP" | "STOPPED_OUT" | "INVALIDATED"
    status_detail: str = ""
    created_at_ist: str = ""
    entry_filled_at_ist: Optional[str] = None
    closed_at_ist: Optional[str] = None
    realized_r: float = 0.0
    floating_r: float = 0.0
    max_favorable_price: float = 0.0
    mfe_r: float = 0.0
    duration_min: int = 0
    entry_timestamp: Optional[int] = None
    closed_timestamp: Optional[int] = None
    absent_cycles: int = 0
    telegram_message_id: Optional[int] = None
    telegram_discussion_thread_id: Optional[int] = None
    # Strategy-framework fields: originating strategy name and the effective
    # params dict at open time. Allows one ledger to serve all strategies.
    strategy: str = "extreme_fvg"
    strategy_params: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.trade_id:
            formed = self.ltf_fvg.get("formed_at", 0) if self.ltf_fvg else 0
            self.trade_id = f"{self.symbol}:{formed}:{self.entry_price:.2f}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TrackedExtremeTrade":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_keys})


class ExtremeTradeTracker:
    def __init__(
        self,
        storage_path: str = PERSISTENCE_FILE,
        session_filter: bool = False,
        weekday_filter: bool = False,
        entry_session_filter: bool = False,
        entry_weekday_filter: bool = False,
        sessions: Optional[str] = None,
        entry_sessions: Optional[str] = None,
        session_config: Optional[SessionFilterConfig] = None,
    ):
        self.storage_path = Path(storage_path)
        self.active_trades: Dict[str, TrackedExtremeTrade] = {}
        self.history: List[TrackedExtremeTrade] = []

        if session_config is not None:
            self.session_config = session_config
        else:
            self.session_config = SessionFilterConfig.from_legacy(
                session_filter=session_filter,
                weekday_filter=weekday_filter,
                entry_session_filter=entry_session_filter,
                entry_weekday_filter=entry_weekday_filter,
                sessions=sessions,
                entry_sessions=entry_sessions,
            )

        # Legacy backward-compatible attributes
        self.session_filter_enabled = self.session_config.fvg_sessions.strip().upper() != "ALL"
        self.weekday_filter_enabled = self.session_config.fvg_weekdays_only
        self.entry_session_filter_enabled = self.session_config.entry_sessions.strip().upper() != "ALL"
        self.entry_weekday_filter_enabled = self.session_config.entry_weekdays_only
        self._load()

    def update_session_config(self, session_config: SessionFilterConfig) -> None:
        self.session_config = session_config
        self.session_filter_enabled = self.session_config.fvg_sessions.strip().upper() != "ALL"
        self.weekday_filter_enabled = self.session_config.fvg_weekdays_only
        self.entry_session_filter_enabled = self.session_config.entry_sessions.strip().upper() != "ALL"
        self.entry_weekday_filter_enabled = self.session_config.entry_weekdays_only

    @classmethod
    def from_env(cls) -> "ExtremeTradeTracker":
        return cls(
            storage_path=PERSISTENCE_FILE,
            session_filter=os.getenv("EXTREME_SESSION_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes"),
            weekday_filter=os.getenv("EXTREME_WEEKDAY_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes"),
            entry_session_filter=os.getenv("EXTREME_ENTRY_SESSION_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes"),
            entry_weekday_filter=os.getenv("EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes"),
            sessions=os.getenv("EXTREME_SESSIONS", "ALL"),
            entry_sessions=os.getenv("EXTREME_ENTRY_SESSIONS", "ALL"),
        )

    def _load(self):
        try:
            if self.storage_path.exists():
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.active_trades = {
                    k: TrackedExtremeTrade.from_dict(v)
                    for k, v in data.get("active_trades", {}).items()
                }
                self.history = [
                    TrackedExtremeTrade.from_dict(t)
                    for t in data.get("history", [])
                ]
                logger.info(
                    "Loaded %d active trades and %d history records from %s",
                    len(self.active_trades),
                    len(self.history),
                    self.storage_path,
                )
        except Exception as exc:
            logger.warning("Failed to load extreme live trades from %s: %s", self.storage_path, exc)
            self.active_trades = {}
            self.history = []

    async def load_async(self) -> bool:
        """
        Asynchronously loads active trades and history from Redis if available,
        falling back to local disk persistence.
        """
        try:
            from redis_client import redis_client
            if redis_client.is_configured():
                redis_key = redis_client.get_key("extreme_trades")
                data = await redis_client.get_json(redis_key)
                if data and isinstance(data, dict):
                    self.active_trades = {
                        k: TrackedExtremeTrade.from_dict(v)
                        for k, v in data.get("active_trades", {}).items()
                    }
                    self.history = [
                        TrackedExtremeTrade.from_dict(t)
                        for t in data.get("history", [])
                    ]
                    logger.info(
                        "Restored %d active trades and %d history records from Redis ('%s')",
                        len(self.active_trades),
                        len(self.history),
                        redis_key,
                    )
                    self._save_local()
                    return True
        except Exception as exc:
            logger.warning("Failed to load extreme live trades from Redis: %s", exc)

        # Fallback to local file
        self._load()
        return bool(self.active_trades or self.history)

    def _save_local(self):
        """Saves trade state synchronously to local JSON file."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "active_trades": {k: t.to_dict() for k, t in self.active_trades.items()},
                        "history": [t.to_dict() for t in self.history],
                    },
                    f,
                    indent=2,
                )
        except Exception as exc:
            logger.warning("Failed to save extreme live trades to %s: %s", self.storage_path, exc)

    async def save_async(self) -> bool:
        """Saves trade state to both local disk and Redis."""
        self._save_local()
        try:
            from redis_client import redis_client
            if redis_client.is_configured():
                redis_key = redis_client.get_key("extreme_trades")
                data = {
                    "active_trades": {k: t.to_dict() for k, t in self.active_trades.items()},
                    "history": [t.to_dict() for t in self.history],
                }
                return await redis_client.set_json(redis_key, data)
        except Exception as exc:
            logger.warning("Failed to save extreme live trades to Redis: %s", exc)
        return False

    def _save(self):
        """Saves trade state locally and schedules async Redis save if event loop is running."""
        self._save_local()
        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    loop.create_task(self.save_async())
            except RuntimeError:
                pass
        except Exception as exc:
            logger.debug("Could not schedule async Redis save: %s", exc)

    def get_active_trade_for_symbol(self, symbol: str) -> Optional[TrackedExtremeTrade]:
        """
        Returns the active open trade for a symbol if one is currently in position (TRADE_ACTIVE).
        While active in the ledger, its entry price, SL, targets, and FVG anchor are strictly immutable.
        """
        clean_sym = symbol.strip().upper()
        for trade in self.active_trades.values():
            if trade.symbol.strip().upper() == clean_sym and trade.state == "TRADE_ACTIVE":
                return trade
        return None

    def get_pending_trade_for_symbol(self, symbol: str) -> Optional[TrackedExtremeTrade]:
        """Returns the pending retrace trade for a symbol if one exists."""
        clean_sym = symbol.strip().upper()
        for trade in self.active_trades.values():
            if trade.symbol.strip().upper() == clean_sym and trade.state == "PENDING_RETRACE":
                return trade
        return None

    def _resolve_live_session_config(
        self,
        session_config: Optional[SessionFilterConfig],
        session_filter: Optional[bool],
        weekday_filter: Optional[bool],
        entry_session_filter: Optional[bool],
        entry_weekday_filter: Optional[bool],
        sessions: Optional[str],
        entry_sessions: Optional[str],
    ) -> SessionFilterConfig:
        """Resolve per-call session filters without changing from_legacy precedence."""
        if session_config is not None:
            return session_config
        if any(x is not None for x in (session_filter, weekday_filter, entry_session_filter, entry_weekday_filter, sessions, entry_sessions)):
            return SessionFilterConfig.from_legacy(
                session_filter=session_filter if session_filter is not None else (self.session_config.fvg_sessions.strip().upper() != "ALL"),
                weekday_filter=weekday_filter if weekday_filter is not None else self.session_config.fvg_weekdays_only,
                entry_session_filter=entry_session_filter if entry_session_filter is not None else (self.session_config.entry_sessions.strip().upper() != "ALL"),
                entry_weekday_filter=entry_weekday_filter if entry_weekday_filter is not None else self.session_config.entry_weekdays_only,
                sessions=sessions or self.session_config.fvg_sessions,
                entry_sessions=entry_sessions or self.session_config.entry_sessions,
            )
        return self.session_config


    def _ingest_scanner_setups(
        self,
        setups: List[Dict[str, Any]],
        current_mids: Dict[str, float],
        session_config: SessionFilterConfig,
        now_ts: int,
        now_ist_str: str,
        events: List[Tuple[str, TrackedExtremeTrade]],
    ) -> Set[str]:
        """Registers/refreshes trades from scanner emissions; returns symbols seen this cycle.

        Invariants (why this is structured the way it is):
        - A symbol with a TRADE_ACTIVE ledger record is locked: entry/SL are immutable,
          so later scanner emissions for it are ignored.
        - An unfilled pending record follows the FRESHEST FVG emission in place (newer
          formed_at wins); anchor/FVG metadata is replaced without duplicate events.
        - Out-of-session fills are never ingested as active; the setup stays pending.
        """
        seen_symbols: Set[str] = set()
        for s in setups:
            sym = s["symbol"].strip().upper()
            curr_px = lookup_mid(current_mids, sym, float(s.get("current_price", s["entry_price"])))

            # If symbol already has an ACTIVE trade in the ledger, its entry price is LOCKED.
            existing_active = self.get_active_trade_for_symbol(sym)
            if existing_active:
                seen_symbols.add(sym)
                continue

            fvg_formed_at = s.get("target_fvg", {}).get("formed_at", 0)
            entry_px = s["entry_price"]
            trade_id = f"{sym}:{fvg_formed_at}:{entry_px:.2f}"
            seen_symbols.add(sym)

            # Check FVG formation session/weekday filters
            dur_ms = TIMEFRAME_MS.get(s.get("ltf_timeframe", "15m"), 15 * 60 * 1000)
            fvg_close_ts = fvg_formed_at + dur_ms if fvg_formed_at else now_ts
            if not session_config.is_fvg_valid(fvg_close_ts):
                continue

            existing_pending = self.get_pending_trade_for_symbol(sym)
            if existing_pending is not None and existing_pending.trade_id != trade_id:
                # Scanner offers a different setup for a symbol with an unfilled pending
                # record. The pending record must follow the FRESHEST emission (newer
                # formed_at) in place: stale anchor/FVG pairings are replaced and no
                # duplicate NEW_SETUP event is emitted.
                formed_ist = s.get("target_fvg", {}).get("formed_time_ist") or s.get("fvg_formation_time_ist")
                if not formed_ist and fvg_formed_at:
                    formed_ist = _candle_close_ist(fvg_formed_at, s.get("ltf_timeframe", "15m"))
                setup_created_ist = formed_ist or now_ist_str

                existing_formed = existing_pending.ltf_fvg.get("formed_at", 0) or 0
                if fvg_formed_at > existing_formed:
                    refreshed = TrackedExtremeTrade(
                        trade_id=trade_id,
                        symbol=sym,
                        direction=s["direction"],
                        ltf_timeframe=s.get("ltf_timeframe", "15m"),
                        entry_price=entry_px,
                        stop_loss=s["stop_loss"],
                        risk_r=s["risk_r"],
                        risk_pct=s["risk_pct"],
                        tp_1r=s["tp_1r"],
                        tp_2r=s["tp_2r"],
                        tp_3r=s["tp_3r"],
                        completion_target=s.get("completion_target", "2R"),
                        htf_anchor=s.get("anchor", {}),
                        ltf_fvg=s.get("target_fvg", {}),
                        strategy=s.get("strategy", "extreme_fvg"),
                        strategy_params=s.get("strategy_params", {}) or {},
                        state="PENDING_RETRACE",
                        status_detail="Waiting for Retrace (refreshed to latest emission)",
                        created_at_ist=setup_created_ist,
                        max_favorable_price=curr_px,
                    )
                    self.active_trades.pop(existing_pending.trade_id, None)
                    self.active_trades[trade_id] = refreshed
                    logger.info(
                        "Refreshed pending setup %s -> %s (newer FVG emission; anchor/FVG metadata updated)",
                        existing_pending.trade_id, trade_id,
                    )
                continue

            if trade_id not in self.active_trades:
                # Register new setup
                is_active = (s.get("state") == "TRADE_ACTIVE")
                if is_active:
                    entry_ts_eval = s.get("entry_timestamp") or now_ts
                    if not session_config.is_entry_valid(entry_ts_eval):
                        # If entry occurred outside allowed window, don't ingest as active
                        is_active = False

                status_det = f"Active (+{s.get('floating_r', 0)}R)" if is_active else "Waiting for Retrace"
                formed_ist = s.get("target_fvg", {}).get("formed_time_ist") or s.get("fvg_formation_time_ist")
                if not formed_ist and fvg_formed_at:
                    formed_ist = _candle_close_ist(fvg_formed_at, s.get("ltf_timeframe", "15m"))
                setup_created_ist = formed_ist or now_ist_str

                trade = TrackedExtremeTrade(
                    trade_id=trade_id,
                    symbol=sym,
                    direction=s["direction"],
                    ltf_timeframe=s.get("ltf_timeframe", "15m"),
                    entry_price=entry_px,
                    stop_loss=s["stop_loss"],
                    risk_r=s["risk_r"],
                    risk_pct=s["risk_pct"],
                    tp_1r=s["tp_1r"],
                    tp_2r=s["tp_2r"],
                    tp_3r=s["tp_3r"],
                    completion_target=s.get("completion_target", "2R"),
                    htf_anchor=s.get("anchor", {}),
                    ltf_fvg=s.get("target_fvg", {}),
                    strategy=s.get("strategy", "extreme_fvg"),
                    strategy_params=s.get("strategy_params", {}) or {},
                    state="TRADE_ACTIVE" if is_active else s.get("state", "PENDING_RETRACE"),
                    status_detail=status_det,
                    created_at_ist=setup_created_ist,
                    entry_filled_at_ist=s.get("entry_time_ist") if is_active else None,
                    floating_r=s.get("floating_r", 0.0) if is_active else 0.0,
                    max_favorable_price=curr_px,
                    mfe_r=max(0.0, s.get("floating_r", 0.0)) if is_active else 0.0,
                    entry_timestamp=s.get("entry_timestamp") or (now_ts if is_active else None),
                )
                self.active_trades[trade_id] = trade
                events.append(("NEW_SETUP", trade))
                if is_active:
                    events.append(("ENTRY_FILLED", trade))
            else:
                trade = self.active_trades[trade_id]
                old_state = trade.state
                new_state = s.get("state", trade.state)

                # Check if transitioned to active (out-of-session fills are suppressed)
                if old_state == "PENDING_RETRACE" and new_state == "TRADE_ACTIVE":
                    entry_ts_eval = s.get("entry_timestamp") or now_ts
                    if session_config.is_entry_valid(entry_ts_eval):
                        trade.state = "TRADE_ACTIVE"
                        trade.entry_filled_at_ist = s.get("entry_time_ist") or now_ist_str
                        trade.entry_timestamp = entry_ts_eval
                        trade.status_detail = "Active (Just Filled)"
                        events.append(("ENTRY_FILLED", trade))

        return seen_symbols

    def _monitor_pending_trade(
        self,
        trade: TrackedExtremeTrade,
        trade_id: str,
        candles: List[Any],
        curr_px: float,
        risk_r: float,
        session_config: SessionFilterConfig,
        seen_symbols: Set[str],
        now_ts: int,
        now_ist_str: str,
        events: List[Tuple[str, TrackedExtremeTrade]],
        to_close: List[Tuple[str, str, TrackedExtremeTrade]],
    ) -> None:
        """Pending-retrace monitor: replays post-formation candles for fill/invalidation,
        then applies live-mid invalidation and absent-setup expiry.

        Frozen event order: candle replay resolves SL before TP on the same candle
        (pessimistic); out-of-session entry touches are ignored, not deferred.
        Closure events are appended to `to_close` so they are emitted only after archival.
        """
        htf_bottom = trade.htf_anchor.get("bottom", 0.0)
        htf_top = trade.htf_anchor.get("top", float("inf"))
        formed_at = trade.ltf_fvg.get("formed_at", 0) or 0
        post_formation = [c for c in candles if _candle_ts(c) > formed_at] if candles else []
        post_formation.sort(key=_candle_ts)

        target_tp, target_mult = _resolve_tp_target(trade)

        filled = False
        fill_ts = None
        resolved = False
        for c in post_formation:
            c_ts, c_high, c_low = _candle_ts(c), _candle_high(c), _candle_low(c)
            c_ist = _candle_close_ist(c_ts, trade.ltf_timeframe)
            if not filled:
                if trade.direction == "Bullish":
                    sl_before_entry = c_low <= trade.stop_loss and c_high < trade.entry_price
                    anchor_before_entry = (
                        "bottom" in trade.htf_anchor
                        and trade.htf_anchor.get("bottom") is not None
                        and c_low < float(trade.htf_anchor["bottom"])
                        and c_high < trade.entry_price
                    )
                    if sl_before_entry or anchor_before_entry:
                        _close_trade(trade, "INVALIDATED", 0.0, "Invalidated (SL/Anchor Breached Before Entry)", c_ts, c_ist)
                        to_close.append((trade_id, "SETUP_INVALIDATED", trade))
                        resolved = True
                        break
                    # Check Fill (out-of-session touches are ignored, not deferred)
                    if c_low <= trade.entry_price and session_config.is_entry_valid(c_ts):
                        filled = True
                        fill_ts = c_ts
                        trade.entry_timestamp = fill_ts
                        trade.entry_filled_at_ist = c_ist
                else:  # Bearish
                    sl_before_entry = c_high >= trade.stop_loss and c_low > trade.entry_price
                    anchor_before_entry = (
                        "top" in trade.htf_anchor
                        and trade.htf_anchor.get("top") is not None
                        and c_high > float(trade.htf_anchor["top"])
                        and c_low > trade.entry_price
                    )
                    if sl_before_entry or anchor_before_entry:
                        _close_trade(trade, "INVALIDATED", 0.0, "Invalidated (SL/Anchor Breached Before Entry)", c_ts, c_ist)
                        to_close.append((trade_id, "SETUP_INVALIDATED", trade))
                        resolved = True
                        break
                    # Check Fill (out-of-session touches are ignored, not deferred)
                    if c_high >= trade.entry_price and session_config.is_entry_valid(c_ts):
                        filled = True
                        fill_ts = c_ts
                        trade.entry_timestamp = fill_ts
                        trade.entry_filled_at_ist = c_ist

            if filled:
                # Check exits chronologically on fill candle or subsequent candles
                _update_mfe(trade, c_high if trade.direction == "Bullish" else c_low, risk_r)
                dur_min = max(1, int((c_ts - fill_ts) / 60000))
                if trade.direction == "Bullish":
                    if c_low <= trade.stop_loss:
                        _close_trade(trade, "STOPPED_OUT", -1.0, "STOP LOSS HIT (-1.0R)", c_ts, c_ist, dur_min)
                        to_close.append((trade_id, "SL_HIT", trade))
                        resolved = True
                        break
                    if c_high >= target_tp:
                        _close_trade(trade, "COMPLETED_TP", target_mult, f"TP {trade.completion_target} HIT (+{target_mult:.1f}R)", c_ts, c_ist, dur_min)
                        to_close.append((trade_id, "TP_HIT", trade))
                        resolved = True
                        break
                else:  # Bearish
                    if c_high >= trade.stop_loss:
                        _close_trade(trade, "STOPPED_OUT", -1.0, "STOP LOSS HIT (-1.0R)", c_ts, c_ist, dur_min)
                        to_close.append((trade_id, "SL_HIT", trade))
                        resolved = True
                        break
                    if c_low <= target_tp:
                        _close_trade(trade, "COMPLETED_TP", target_mult, f"TP {trade.completion_target} HIT (+{target_mult:.1f}R)", c_ts, c_ist, dur_min)
                        to_close.append((trade_id, "TP_HIT", trade))
                        resolved = True
                        break

        if resolved:
            return

        if filled:
            trade.state = "TRADE_ACTIVE"
            trade.status_detail = "Active (Filled)"
            events.append(("ENTRY_FILLED", trade))
            return

        # No candle fill yet: live mid already breaching SL or the HTF anchor invalidates.
        if trade.direction == "Bullish":
            is_invalidated = curr_px <= trade.stop_loss or curr_px < htf_bottom
        else:
            is_invalidated = curr_px >= trade.stop_loss or curr_px > htf_top

        if is_invalidated:
            _close_trade(trade, "INVALIDATED", 0.0, "Invalidated (SL/Anchor Breached Before Entry)", now_ts, now_ist_str)
            to_close.append((trade_id, "SETUP_INVALIDATED", trade))
            return

        # Absent-setup expiry: scanner stopped emitting this symbol.
        if trade.symbol.strip().upper() not in seen_symbols:
            trade.absent_cycles += 1
            if trade.absent_cycles >= PENDING_ABSENT_EXPIRY_CYCLES:
                _close_trade(
                    trade, "INVALIDATED", 0.0,
                    f"Expired (setup absent from scanner for {trade.absent_cycles} cycles)",
                    now_ts, now_ist_str,
                )
                to_close.append((trade_id, "SETUP_INVALIDATED", trade))
        else:
            trade.absent_cycles = 0

    def _monitor_active_trade(
        self,
        trade: TrackedExtremeTrade,
        trade_id: str,
        candles: List[Any],
        curr_px: float,
        risk_r: float,
        now_ts: int,
        now_ist_str: str,
        to_close: List[Tuple[str, str, TrackedExtremeTrade]],
    ) -> None:
        """Active-position monitor: replays candles strictly chronologically with SL checked
        BEFORE TP on the same candle (pessimistic), then falls back to the live mid price.
        Closure events are appended to `to_close` so they are emitted only after archival.
        """
        target_tp, target_mult = _resolve_tp_target(trade)

        entry_t = trade.entry_timestamp or 0
        subsequent_candles = [c for c in candles if _candle_ts(c) >= entry_t] if candles else []
        subsequent_candles.sort(key=_candle_ts)

        for c in subsequent_candles:
            c_ts, c_high, c_low = _candle_ts(c), _candle_high(c), _candle_low(c)
            c_ist = _candle_close_ist(c_ts, trade.ltf_timeframe)
            dur_min = max(1, int((c_ts - entry_t) / 60000))
            _update_mfe(trade, c_high if trade.direction == "Bullish" else c_low, risk_r)
            if trade.direction == "Bullish":
                # 1. Stop Loss checked BEFORE Take Profit on the same candle (pessimistic)
                if c_low <= trade.stop_loss:
                    _close_trade(trade, "STOPPED_OUT", -1.0, "STOP LOSS HIT (-1.0R)", c_ts, c_ist, dur_min)
                    to_close.append((trade_id, "SL_HIT", trade))
                    return
                if c_high >= target_tp:
                    _close_trade(trade, "COMPLETED_TP", target_mult, f"TP {trade.completion_target} HIT (+{target_mult:.1f}R)", c_ts, c_ist, dur_min)
                    to_close.append((trade_id, "TP_HIT", trade))
                    return
            else:  # Bearish
                if c_high >= trade.stop_loss:
                    _close_trade(trade, "STOPPED_OUT", -1.0, "STOP LOSS HIT (-1.0R)", c_ts, c_ist, dur_min)
                    to_close.append((trade_id, "SL_HIT", trade))
                    return
                if c_low <= target_tp:
                    _close_trade(trade, "COMPLETED_TP", target_mult, f"TP {trade.completion_target} HIT (+{target_mult:.1f}R)", c_ts, c_ist, dur_min)
                    to_close.append((trade_id, "TP_HIT", trade))
                    return

        # Not closed on past candles: evaluate the live mid price.
        _update_mfe(trade, curr_px, risk_r)
        if trade.direction == "Bullish":
            trade.floating_r = round((curr_px - trade.entry_price) / risk_r, 2)
            hit_sl, hit_tp = curr_px <= trade.stop_loss, curr_px >= target_tp
        else:  # Bearish
            trade.floating_r = round((trade.entry_price - curr_px) / risk_r, 2)
            hit_sl, hit_tp = curr_px >= trade.stop_loss, curr_px <= target_tp

        if hit_sl or hit_tp:
            _close_trade(
                trade,
                "STOPPED_OUT" if hit_sl else "COMPLETED_TP",
                -1.0 if hit_sl else target_mult,
                "STOP LOSS HIT (-1.0R)" if hit_sl else f"TP {trade.completion_target} HIT (+{target_mult:.1f}R)",
                now_ts, now_ist_str,
                max(1, int((now_ts - entry_t) / 60000)) if entry_t else 1,
            )
            to_close.append((trade_id, "SL_HIT" if hit_sl else "TP_HIT", trade))
        else:
            trade.status_detail = f"Active ({'+' if trade.floating_r > 0 else ''}{trade.floating_r}R)"

    def process_live_setups(
        self,
        setups: List[Dict[str, Any]],
        current_mids: Dict[str, float],
        recent_candles_map: Optional[Dict[str, List[Any]]] = None,
        session_filter: Optional[bool] = None,
        weekday_filter: Optional[bool] = None,
        entry_session_filter: Optional[bool] = None,
        entry_weekday_filter: Optional[bool] = None,
        sessions: Optional[str] = None,
        entry_sessions: Optional[str] = None,
        session_config: Optional[SessionFilterConfig] = None,
    ) -> List[Tuple[str, TrackedExtremeTrade]]:
        """
        Ingests live scanner setups, tracks new entries, monitors open positions,
        and resolves TP / SL exits.
        Returns a list of event tuples: (event_type, trade)
        e.g. ("NEW_SETUP", trade), ("ENTRY_FILLED", trade), ("TP_HIT", trade), ("SL_HIT", trade)
        """
        events = []
        now_ist_str = datetime.now(IST).strftime("%d-%b %I:%M %p IST")
        now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        session_config = self._resolve_live_session_config(
            session_config,
            session_filter,
            weekday_filter,
            entry_session_filter,
            entry_weekday_filter,
            sessions,
            entry_sessions,
        )

        # 1. Ingest/Update setups from scanner (register new, refresh stale pendings)
        seen_symbols = self._ingest_scanner_setups(
            setups, current_mids, session_config, now_ts, now_ist_str, events,
        )

        # 2. Monitor all open trades: check both TRADE_ACTIVE (for TP/SL) and PENDING_RETRACE (for invalidation / breach)
        from hyperliquid_client import SYMBOL_ALIASES
        to_close = []
        for trade_id, trade in list(self.active_trades.items()):
            raw_sym = SYMBOL_ALIASES.get(trade.symbol.strip().upper(), trade.symbol.strip().upper())
            curr_px = lookup_mid(current_mids, trade.symbol, trade.entry_price)
            risk_r = trade.risk_r if trade.risk_r > 0 else (trade.entry_price * 0.001)

            candles = (recent_candles_map.get(raw_sym) or recent_candles_map.get(trade.symbol) or []) if recent_candles_map else []

            # A. Pending-retrace: candle replay for fill/invalidation, then live/absent expiry
            if trade.state == "PENDING_RETRACE":
                self._monitor_pending_trade(
                    trade, trade_id, candles, curr_px, risk_r, session_config,
                    seen_symbols, now_ts, now_ist_str, events, to_close,
                )
                continue

            if trade.state != "TRADE_ACTIVE":
                continue

            # B. Active position: chronological candle replay (SL before TP) + live-mid check
            self._monitor_active_trade(
                trade, trade_id, candles, curr_px, risk_r, now_ts, now_ist_str, to_close,
            )


        # 3. Archive resolved trades to history
        for trade_id, evt_type, trade in to_close:
            self.history.insert(0, trade)
            del self.active_trades[trade_id]
            events.append((evt_type, trade))

        self._save()
        return events

    def get_summary(self, strategy: Optional[str] = None) -> Dict[str, Any]:
        """Calculates live performance summary statistics.
        When ``strategy`` is given, aggregates only trades with that strategy name.
        """
        def _filter(trades: List["TrackedExtremeTrade"]) -> List["TrackedExtremeTrade"]:
            if strategy:
                return [t for t in trades if t.strategy == strategy]
            return trades

        closed_history = _filter(self.history)
        closed_trades = [t for t in closed_history if t.state in ("COMPLETED_TP", "STOPPED_OUT")]
        total_closed = len(closed_trades)
        wins = sum(1 for t in closed_trades if t.state == "COMPLETED_TP")
        losses = total_closed - wins
        win_rate = round((wins / total_closed * 100), 1) if total_closed > 0 else 0.0
        net_r = round(sum(t.realized_r for t in closed_trades), 2)
        avg_mfe = round(sum(t.mfe_r for t in closed_trades) / total_closed, 2) if total_closed > 0 else 0.0

        active_all = _filter(list(self.active_trades.values()))
        active_count = sum(1 for t in active_all if t.state == "TRADE_ACTIVE")
        pending_count = sum(1 for t in active_all if t.state == "PENDING_RETRACE")

        return {
            "total_tracked_trades": len(closed_history) + len(active_all),
            "total_closed_trades": total_closed,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": win_rate,
            "net_realized_r": net_r,
            "avg_mfe_r": avg_mfe,
            "active_now": active_count,
            "pending_now": pending_count,
        }

    def clear_history(self):
        """Clears closed trade history."""
        self.history = []
        self._save()

    def get_filtered_trades(
        self,
        state: Optional[str] = None,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        strategy: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
    ) -> Dict[str, Any]:
        """
        Filters and paginates tracked live trades (active + history).
        Pass ``strategy`` to filter by originating strategy name (e.g. ``extreme_fvg``).
        """
        active_list = [t.to_dict() for t in self.active_trades.values()]
        hist_list = [t.to_dict() for t in self.history]
        all_trades = active_list + hist_list
        # Sort combined trades latest first
        all_trades.sort(
            key=lambda x: x.get("entry_timestamp") or x.get("ltf_fvg", {}).get("formed_at", 0),
            reverse=True,
        )

        # Apply Filters
        if state:
            state_clean = state.strip().upper()
            all_trades = [t for t in all_trades if t.get("state") == state_clean]
        if symbol:
            sym_clean = symbol.strip().upper()
            all_trades = [t for t in all_trades if t.get("symbol", "").upper() == sym_clean]
        if direction:
            dir_clean = direction.strip().capitalize()
            all_trades = [t for t in all_trades if t.get("direction") == dir_clean]
        if strategy:
            all_trades = [t for t in all_trades if t.get("strategy") == strategy]

        total = len(all_trades)
        total_pages = max(1, (total + per_page - 1) // per_page) if per_page > 0 else 1

        # Metrics on the filtered subset
        completed = [t for t in all_trades if t.get("state") == "COMPLETED_TP"]
        stopped = [t for t in all_trades if t.get("state") == "STOPPED_OUT"]
        active = [t for t in all_trades if t.get("state") == "TRADE_ACTIVE"]
        closed = completed + stopped
        win_rate = round(len(completed) / max(len(closed), 1) * 100, 1) if closed else 0.0
        net_pnl_r = round(sum(t.get("realized_r", 0.0) for t in closed), 2)
        avg_mfe = round(sum(t.get("mfe_r", 0.0) for t in all_trades) / max(len(all_trades), 1), 2) if all_trades else 0.0

        # Pagination slice
        safe_page = max(1, page)
        start = (safe_page - 1) * per_page
        paginated_trades = all_trades[start:start + per_page]

        return {
            "status": "success",
            "filters": {
                "state": state,
                "symbol": symbol,
                "direction": direction,
                "strategy": strategy,
            },
            "pagination": {
                "page": safe_page,
                "per_page": per_page,
                "total": total,
                "pages": total_pages,
            },
            "metrics": {
                "trades": total,
                "total_tracked_trades": total,
                "closed_trades": len(closed),
                "total_closed_trades": len(closed),
                "completed": len(completed),
                "stopped": len(stopped),
                "active_now": len(active),
                "winrate": win_rate,
                "win_rate_pct": win_rate,
                "net_pnl_r": net_pnl_r,
                "net_realized_r": net_pnl_r,
                "avg_mfe_r": avg_mfe,
            },
            "summary": self.get_summary(strategy=strategy),
            "trades": paginated_trades,
            "active_trades": [t for t in paginated_trades if t.get("state") in ("PENDING_RETRACE", "TRADE_ACTIVE")],
            "history": [t for t in paginated_trades if t.get("state") not in ("PENDING_RETRACE", "TRADE_ACTIVE")],
        }


# Singleton Instance
extreme_trade_tracker = ExtremeTradeTracker.from_env()
