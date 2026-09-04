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
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("extreme_trade_tracker")
IST = timezone(timedelta(hours=5, minutes=30))

PERSISTENCE_FILE = os.getenv("EXTREME_LIVE_TRADES_FILE", "data/extreme_live_trades.json")


@dataclass
class TrackedExtremeTrade:
    trade_id: str
    symbol: str
    direction: str  # "Bullish" | "Bearish"
    ltf_timeframe: str
    entry_price: float
    stop_loss: float
    risk_r: float
    risk_pct: float
    tp_1r: float
    tp_2r: float
    tp_3r: float
    completion_target: str
    htf_anchor: Dict[str, Any]
    ltf_fvg: Dict[str, Any]
    state: str  # "PENDING_RETRACE" | "TRADE_ACTIVE" | "COMPLETED_TP" | "STOPPED_OUT" | "INVALIDATED"
    status_detail: str
    created_at_ist: str
    entry_filled_at_ist: Optional[str] = None
    closed_at_ist: Optional[str] = None
    realized_r: float = 0.0
    floating_r: float = 0.0
    max_favorable_price: float = 0.0
    mfe_r: float = 0.0
    duration_min: int = 0
    entry_timestamp: Optional[int] = None
    closed_timestamp: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TrackedExtremeTrade":
        return cls(**d)


class ExtremeTradeTracker:
    def __init__(self, storage_path: str = PERSISTENCE_FILE):
        self.storage_path = Path(storage_path)
        self.active_trades: Dict[str, TrackedExtremeTrade] = {}
        self.history: List[TrackedExtremeTrade] = []
        self._load()

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

    def _save(self):
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

    def process_live_setups(
        self,
        setups: List[Dict[str, Any]],
        current_mids: Dict[str, float],
        recent_candles_map: Optional[Dict[str, List[Any]]] = None,
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

        # 1. Ingest/Update setups from scanner
        seen_symbols = set()
        for s in setups:
            sym = s["symbol"].strip().upper()
            curr_px = float(current_mids.get(sym, s.get("current_price", s["entry_price"])))

            # If symbol already has an ACTIVE trade in the ledger, its entry price is LOCKED.
            existing_active = self.get_active_trade_for_symbol(sym)
            if existing_active:
                seen_symbols.add(sym)
                continue

            fvg_formed_at = s.get("target_fvg", {}).get("formed_at", 0)
            entry_px = s["entry_price"]
            trade_id = f"{sym}:{fvg_formed_at}:{entry_px:.2f}"
            seen_symbols.add(sym)

            if trade_id not in self.active_trades:
                # Register new setup
                is_active = (s.get("state") == "TRADE_ACTIVE")
                status_det = f"Active (+{s.get('floating_r', 0)}R)" if is_active else "Waiting for Retrace"
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
                    state=s.get("state", "PENDING_RETRACE"),
                    status_detail=status_det,
                    created_at_ist=now_ist_str,
                    entry_filled_at_ist=s.get("entry_time_ist") if is_active else None,
                    floating_r=s.get("floating_r", 0.0),
                    max_favorable_price=curr_px,
                    mfe_r=max(0.0, s.get("floating_r", 0.0)),
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

                # Check if transitioned to active
                if old_state == "PENDING_RETRACE" and new_state == "TRADE_ACTIVE":
                    trade.state = "TRADE_ACTIVE"
                    trade.entry_filled_at_ist = s.get("entry_time_ist") or now_ist_str
                    trade.entry_timestamp = s.get("entry_timestamp") or now_ts
                    trade.status_detail = "Active (Just Filled)"
                    events.append(("ENTRY_FILLED", trade))

        # 2. Monitor all open trades: check both TRADE_ACTIVE (for TP/SL) and PENDING_RETRACE (for invalidation / breach)
        from hyperliquid_client import SYMBOL_ALIASES
        to_close = []
        for trade_id, trade in list(self.active_trades.items()):
            raw_sym = SYMBOL_ALIASES.get(trade.symbol.strip().upper(), trade.symbol.strip().upper())
            curr_px = float(current_mids.get(raw_sym, current_mids.get(trade.symbol, trade.entry_price)))
            risk_r = trade.risk_r if trade.risk_r > 0 else (trade.entry_price * 0.001)

            candles = (recent_candles_map.get(raw_sym) or recent_candles_map.get(trade.symbol) or []) if recent_candles_map else []

            # A. Monitor PENDING_RETRACE setups for invalidation before entry
            if trade.state == "PENDING_RETRACE":
                recent_high = max([getattr(c, "high", c.get("h", curr_px) if isinstance(c, dict) else curr_px) for c in candles], default=curr_px) if candles else curr_px
                recent_low = min([getattr(c, "low", c.get("l", curr_px) if isinstance(c, dict) else curr_px) for c in candles], default=curr_px) if candles else curr_px
                htf_bottom = trade.htf_anchor.get("bottom", 0.0)
                htf_top = trade.htf_anchor.get("top", float("inf"))

                is_invalidated = False
                if trade.direction == "Bullish":
                    if min(curr_px, recent_low) <= trade.stop_loss or min(curr_px, recent_low) < htf_bottom:
                        is_invalidated = True
                else:  # Bearish
                    if max(curr_px, recent_high) >= trade.stop_loss or max(curr_px, recent_high) > htf_top:
                        is_invalidated = True

                if is_invalidated:
                    trade.state = "INVALIDATED"
                    trade.status_detail = "Invalidated (SL/Anchor Breached Before Entry)"
                    trade.closed_at_ist = now_ist_str
                    trade.closed_timestamp = now_ts
                    to_close.append((trade_id, "SETUP_INVALIDATED", trade))
                continue

            if trade.state != "TRADE_ACTIVE":
                continue

            # B. Monitor TRADE_ACTIVE trades against live prices & post-entry candle extremes
            # Target definition based on completion target
            target_tp = trade.tp_2r if trade.completion_target == "2R" else (trade.tp_1r if trade.completion_target == "1R" else trade.tp_3r)
            target_mult = 2.0 if trade.completion_target == "2R" else (1.0 if trade.completion_target == "1R" else 3.0)

            entry_t = trade.entry_timestamp or 0
            subsequent_candles = []
            for c in candles:
                c_ts = getattr(c, "timestamp", c.get("t", 0) if isinstance(c, dict) else 0)
                if c_ts >= entry_t:
                    subsequent_candles.append(c)

            recent_high = max([getattr(c, "high", c.get("h", curr_px) if isinstance(c, dict) else curr_px) for c in subsequent_candles], default=curr_px) if subsequent_candles else curr_px
            recent_low = min([getattr(c, "low", c.get("l", curr_px) if isinstance(c, dict) else curr_px) for c in subsequent_candles], default=curr_px) if subsequent_candles else curr_px

            # Update MFE & Floating R
            if trade.direction == "Bullish":
                effective_high = max(curr_px, recent_high)
                effective_low = min(curr_px, recent_low)
                trade.floating_r = round((curr_px - trade.entry_price) / risk_r, 2)
                trade.max_favorable_price = max(trade.max_favorable_price or trade.entry_price, effective_high)
                trade.mfe_r = max(trade.mfe_r, round((trade.max_favorable_price - trade.entry_price) / risk_r, 2))

                # Check TP Hit
                if effective_high >= target_tp:
                    trade.state = "COMPLETED_TP"
                    trade.realized_r = target_mult
                    trade.status_detail = f"TP {trade.completion_target} HIT (+{target_mult:.1f}R)"
                    trade.closed_at_ist = now_ist_str
                    trade.closed_timestamp = now_ts
                    if trade.entry_timestamp:
                        trade.duration_min = max(1, int((now_ts - trade.entry_timestamp) / 60000))
                    to_close.append((trade_id, "TP_HIT", trade))

                # Check SL Hit
                elif effective_low <= trade.stop_loss:
                    trade.state = "STOPPED_OUT"
                    trade.realized_r = -1.0
                    trade.status_detail = "STOP LOSS HIT (-1.0R)"
                    trade.closed_at_ist = now_ist_str
                    trade.closed_timestamp = now_ts
                    if trade.entry_timestamp:
                        trade.duration_min = max(1, int((now_ts - trade.entry_timestamp) / 60000))
                    to_close.append((trade_id, "SL_HIT", trade))
                else:
                    trade.status_detail = f"Active ({'+' if trade.floating_r > 0 else ''}{trade.floating_r}R)"

            else:  # Bearish
                effective_high = max(curr_px, recent_high)
                effective_low = min(curr_px, recent_low)
                trade.floating_r = round((trade.entry_price - curr_px) / risk_r, 2)
                trade.max_favorable_price = min(trade.max_favorable_price or trade.entry_price, effective_low)
                trade.mfe_r = max(trade.mfe_r, round((trade.entry_price - trade.max_favorable_price) / risk_r, 2))

                # Check TP Hit
                if effective_low <= target_tp:
                    trade.state = "COMPLETED_TP"
                    trade.realized_r = target_mult
                    trade.status_detail = f"TP {trade.completion_target} HIT (+{target_mult:.1f}R)"
                    trade.closed_at_ist = now_ist_str
                    trade.closed_timestamp = now_ts
                    if trade.entry_timestamp:
                        trade.duration_min = max(1, int((now_ts - trade.entry_timestamp) / 60000))
                    to_close.append((trade_id, "TP_HIT", trade))

                # Check SL Hit
                elif effective_high >= trade.stop_loss:
                    trade.state = "STOPPED_OUT"
                    trade.realized_r = -1.0
                    trade.status_detail = "STOP LOSS HIT (-1.0R)"
                    trade.closed_at_ist = now_ist_str
                    trade.closed_timestamp = now_ts
                    if trade.entry_timestamp:
                        trade.duration_min = max(1, int((now_ts - trade.entry_timestamp) / 60000))
                    to_close.append((trade_id, "SL_HIT", trade))
                else:
                    trade.status_detail = f"Active ({'+' if trade.floating_r > 0 else ''}{trade.floating_r}R)"

        # 3. Archive resolved trades to history
        for trade_id, evt_type, trade in to_close:
            self.history.insert(0, trade)
            del self.active_trades[trade_id]
            events.append((evt_type, trade))

        self._save()
        return events

    def get_summary(self) -> Dict[str, Any]:
        """Calculates live performance summary statistics across all daemon-tracked trades."""
        closed_trades = [t for t in self.history if t.state in ("COMPLETED_TP", "STOPPED_OUT")]
        total_closed = len(closed_trades)
        wins = len([t for t in closed_trades if t.state == "COMPLETED_TP"])
        losses = len([t for t in closed_trades if t.state == "STOPPED_OUT"])
        win_rate = round((wins / total_closed * 100), 1) if total_closed > 0 else 0.0
        net_r = round(sum(t.realized_r for t in closed_trades), 2)
        avg_mfe = round(sum(t.mfe_r for t in closed_trades) / total_closed, 2) if total_closed > 0 else 0.0

        active_count = len([t for t in self.active_trades.values() if t.state == "TRADE_ACTIVE"])
        pending_count = len([t for t in self.active_trades.values() if t.state == "PENDING_RETRACE"])

        return {
            "total_tracked_trades": len(self.history) + len(self.active_trades),
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


# Singleton Instance
extreme_trade_tracker = ExtremeTradeTracker()
