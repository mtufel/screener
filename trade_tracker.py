"""
Live Trade Tracker and Alert Deduplication Manager.
Tracks active FVG setups and open trades with strict lifecycle rules:
1. No Breakeven adjustment: Trade exits ONLY on Target Take Profit (WIN) or Stop Loss (LOSS).
2. Config-driven Single Active Position per Coin (prevents overlapping duplicate trades).
3. Closes trade immediately on Target Take Profit (2.0R / 3.0R) so Stop Loss can NEVER fire after TP.
4. Emits clear milestone alerts (1.0R / 1.5R progress) while trade remains open towards 2.0R TP.
"""

import os
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from strategy import SetupResult, TPLevels, Candle
from chart_generator import generate_setup_chart

logger = logging.getLogger("crypto_screener.trade_tracker")
IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_SINGLE_POSITION = os.getenv("SINGLE_ACTIVE_POSITION", "true").strip().lower() in ("true", "1", "yes")


@dataclass
class TrackedTrade:
    """Represents a statefully tracked FVG setup and trade."""
    setup_id: str
    symbol: str
    direction: str  # "Bullish" | "Bearish"
    ltf_timeframe: str
    entry_price: float
    sl_price: float  # Fixed Stop Loss (never modified, trade only exits on TP or SL)
    tp_levels: TPLevels
    htf_fvg_bottom: float
    htf_fvg_top: float
    ltf_fvg_bottom: float
    ltf_fvg_top: float
    score: float
    stage: str  # "PENDING_RETRACE" | "ACTIVATED" | "CLOSED_TP" | "CLOSED_SL"
    created_at_ist: str
    activated_at_ist: Optional[str] = None
    closed_at_ist: Optional[str] = None
    alert1_sent: bool = False  # Setup Formed alert
    alert2_sent: bool = False  # Trade Activated alert
    tp1_alert_sent: bool = False
    tp1_5_alert_sent: bool = False
    tp2_alert_sent: bool = False
    tp3_alert_sent: bool = False
    chart_image_path: Optional[str] = None


class TradeTracker:
    """Manages active setup alerts and open trade lifecycle."""

    def __init__(self, single_active_position: bool = DEFAULT_SINGLE_POSITION):
        # Mapping of setup_id -> TrackedTrade
        self.trades: Dict[str, TrackedTrade] = {}
        self.single_active_position: bool = single_active_position

    def get_setup_id(self, setup: SetupResult) -> str:
        """Generates a unique deterministic ID for a setup."""
        return f"{setup.symbol}_{setup.direction}_{int(setup.htf_fvg.formed_at)}_{int(setup.ltf_fvg.formed_at)}_{setup.ltf_timeframe}"

    def has_active_trade_for_symbol(self, symbol: str, direction: str) -> Optional[TrackedTrade]:
        """
        Checks if there is already an active (ACTIVATED or PENDING_RETRACE) trade
        for this symbol and direction.
        """
        for t in self.trades.values():
            if t.symbol == symbol and t.direction == direction and t.stage in ("ACTIVATED", "PENDING_RETRACE"):
                return t
        return None

    def register_or_update_setup(
        self,
        setup: SetupResult,
        candles_ltf: List[Candle],
    ) -> Tuple[bool, Optional[str], Optional[bytes]]:
        """
        Processes a setup from the live screener:
        - If single_active_position is True and coin already has an active trade: suppresses new overlapping setup.
        - If setup is new and in PENDING_RETRACE: triggers Alert 1 once.
        - If setup transitions to ACTIVATED: triggers Alert 2 once.
        - If already alerted for this stage: skips sending duplicates.

        Returns:
            Tuple[bool, Optional[str], Optional[bytes]]: (should_alert, alert_text, chart_bytes)
        """
        setup_id = self.get_setup_id(setup)
        now_ist = datetime.now(IST).strftime("%d-%b-%Y %I:%M %p IST")

        # 1. Check single active position restriction (if configured)
        if self.single_active_position:
            existing_active = self.has_active_trade_for_symbol(setup.symbol, setup.direction)
            if existing_active and existing_active.setup_id != setup_id:
                logger.info(
                    "Single position mode: Suppressed new %s %s setup (%s) because trade %s is already active.",
                    setup.symbol,
                    setup.direction,
                    setup_id,
                    existing_active.setup_id,
                )
                return False, None, None

        trade = self.trades.get(setup_id)
        if trade is None:
            trade = TrackedTrade(
                setup_id=setup_id,
                symbol=setup.symbol,
                direction=setup.direction,
                ltf_timeframe=setup.ltf_timeframe or "5m",
                entry_price=setup.entry_price,
                sl_price=setup.sl_ref,
                tp_levels=setup.tp_levels,
                htf_fvg_bottom=setup.htf_fvg.bottom,
                htf_fvg_top=setup.htf_fvg.top,
                ltf_fvg_bottom=setup.ltf_fvg.bottom,
                ltf_fvg_top=setup.ltf_fvg.top,
                score=setup.score,
                stage=setup.stage,
                created_at_ist=now_ist,
            )
            self.trades[setup_id] = trade

        # If trade was already closed (TP or SL), do not re-alert!
        if trade.stage in ("CLOSED_TP", "CLOSED_SL"):
            return False, None, None

        # 2. Check Alert 1 (PENDING_RETRACE)
        if setup.stage == "PENDING_RETRACE" and not trade.alert1_sent:
            trade.alert1_sent = True
            trade.stage = "PENDING_RETRACE"
            chart_path = f"static/charts/{setup_id}.png"
            trade.chart_image_path = chart_path

            chart_bytes = generate_setup_chart(
                symbol=setup.symbol,
                direction=setup.direction,
                candles_ltf=candles_ltf,
                htf_fvg=setup.htf_fvg,
                ltf_fvg=setup.ltf_fvg,
                entry_price=setup.entry_price,
                sl_price=setup.sl_ref,
                tp_levels=setup.tp_levels,
                stage="PENDING_RETRACE",
                ltf_timeframe=setup.ltf_timeframe,
                output_path=chart_path,
            )

            from telegram_client import format_single_setup
            alert_text = format_single_setup(setup)
            logger.info("New Stage 1 alert dispatched for %s (%s).", setup.symbol, setup_id)
            return True, alert_text, chart_bytes

        # 3. Check Alert 2 (ACTIVATED)
        elif setup.stage == "ACTIVATED" and not trade.alert2_sent:
            trade.alert2_sent = True
            trade.stage = "ACTIVATED"
            trade.activated_at_ist = now_ist
            trade.entry_price = setup.entry_price
            chart_path = f"static/charts/{setup_id}.png"
            trade.chart_image_path = chart_path

            chart_bytes = generate_setup_chart(
                symbol=setup.symbol,
                direction=setup.direction,
                candles_ltf=candles_ltf,
                htf_fvg=setup.htf_fvg,
                ltf_fvg=setup.ltf_fvg,
                entry_price=setup.entry_price,
                sl_price=setup.sl_ref,
                tp_levels=setup.tp_levels,
                stage="ACTIVATED",
                ltf_timeframe=setup.ltf_timeframe,
                output_path=chart_path,
            )

            from telegram_client import format_single_setup
            alert_text = format_single_setup(setup)
            logger.info("Trade ACTIVATED alert dispatched for %s (%s).", setup.symbol, setup_id)
            return True, alert_text, chart_bytes

        # Duplicate setup -> Suppress alert
        return False, None, None

    def check_open_trades(self, all_mids: Dict[str, float]) -> List[str]:
        """
        Evaluates all active ACTIVATED trades against current market mid prices.
        
        Strict Rules:
        1. Trade exits ONLY on Target Take Profit (2.0R) or Stop Loss (SL).
        2. Progress milestones (1.0R / 1.5R) notify the user while keeping the trade open.
        3. Once 2.0R Target is reached: Trade is marked CLOSED_TP (WIN) and completed.
        4. If Target TP is not reached and price hits initial SL: Trade is marked CLOSED_SL (LOSS).
        5. Closed trades are NEVER checked again.
        """
        updates: List[str] = []
        now_ist = datetime.now(IST).strftime("%d-%b-%Y %I:%M %p IST")

        for trade_id, trade in list(self.trades.items()):
            if trade.stage != "ACTIVATED":
                continue

            current_price = all_mids.get(trade.symbol)
            if not current_price or current_price <= 0:
                continue

            is_bullish = trade.direction == "Bullish"
            tp = trade.tp_levels

            # ==================================================================
            # 1. TAKE PROFIT TARGET CHECKS (Progress Milestones & Target Exit)
            # ==================================================================
            # 1.0R Progress Milestone
            tp1_hit = (current_price >= tp.r1) if is_bullish else (current_price <= tp.r1)
            if tp1_hit and not trade.tp1_alert_sent:
                trade.tp1_alert_sent = True
                msg = (
                    f"🎯 <b>{trade.symbol}-PERP — 1.0R TAKE PROFIT REACHED!</b>\n"
                    f"<b>Direction:</b> {trade.direction} ({trade.ltf_timeframe})\n"
                    f"<b>Entry:</b> ${trade.entry_price:,.2f} ➜ <b>Current:</b> ${current_price:,.2f}\n"
                    f"<b>Gain:</b> +{tp.r1_points:,.2f} pts (+{tp.risk_pct:.2f}% | +1.0 R)\n"
                    f"<b>Target:</b> Aiming for 2.0R (${tp.r2:,.2f})"
                )
                updates.append(msg)
                logger.info("Trade %s reached 1.0R milestone at $%s.", trade_id, current_price)

            # 1.5R Progress Milestone
            tp1_5_hit = (current_price >= tp.r1_5) if is_bullish else (current_price <= tp.r1_5)
            if tp1_5_hit and not trade.tp1_5_alert_sent:
                trade.tp1_5_alert_sent = True
                msg = (
                    f"🎯 <b>{trade.symbol}-PERP — 1.5R TAKE PROFIT REACHED!</b>\n"
                    f"<b>Direction:</b> {trade.direction} ({trade.ltf_timeframe})\n"
                    f"<b>Entry:</b> ${trade.entry_price:,.2f} ➜ <b>Current:</b> ${current_price:,.2f}\n"
                    f"<b>Gain:</b> +{tp.r1_5_points:,.2f} pts (+{tp.risk_pct * 1.5:.2f}% | +1.5 R) 🚀\n"
                    f"<b>Target:</b> Aiming for 2.0R (${tp.r2:,.2f})"
                )
                updates.append(msg)

            # 2.0R Target Take Profit -> OFFICIAL TRADE EXIT (WIN)
            tp2_hit = (current_price >= tp.r2) if is_bullish else (current_price <= tp.r2)
            if tp2_hit and not trade.tp2_alert_sent:
                trade.tp2_alert_sent = True
                trade.stage = "CLOSED_TP"
                trade.closed_at_ist = now_ist
                msg = (
                    f"🎯 🏆 <b>{trade.symbol}-PERP — 2.0R TARGET TAKE PROFIT HIT!</b>\n"
                    f"<b>Direction:</b> {trade.direction} ({trade.ltf_timeframe})\n"
                    f"<b>Entry Price:</b> ${trade.entry_price:,.2f}\n"
                    f"<b>Exit Price:</b> ${current_price:,.2f}\n"
                    f"<b>Gain:</b> +{tp.r2_points:,.2f} pts (+{tp.risk_pct * 2.0:.2f}% | +2.0 R) 🔥\n"
                    f"<b>Status:</b> ✅ Trade Closed (WIN) at {now_ist}"
                )
                updates.append(msg)
                logger.info("Trade %s closed on 2.0R Target TP at $%s.", trade_id, current_price)
                continue  # Trade is officially completed; do NOT check SL!

            # 3.0R Maximum Target (if trade managed beyond 2R)
            tp3_hit = (current_price >= tp.r3) if is_bullish else (current_price <= tp.r3)
            if tp3_hit and not trade.tp3_alert_sent:
                trade.tp3_alert_sent = True
                trade.stage = "CLOSED_TP"
                trade.closed_at_ist = now_ist
                msg = (
                    f"🎯 🚀 <b>{trade.symbol}-PERP — 3.0R MAXIMUM TARGET SMASHED!</b>\n"
                    f"<b>Direction:</b> {trade.direction} ({trade.ltf_timeframe})\n"
                    f"<b>Entry Price:</b> ${trade.entry_price:,.2f}\n"
                    f"<b>Exit Price:</b> ${current_price:,.2f}\n"
                    f"<b>Gain:</b> +{tp.r3_points:,.2f} pts (+{tp.risk_pct * 3.0:.2f}% | +3.0 R) 🏆\n"
                    f"<b>Status:</b> ✅ Trade Closed (WIN) at {now_ist}"
                )
                updates.append(msg)
                logger.info("Trade %s closed on 3.0R Maximum TP at $%s.", trade_id, current_price)
                continue

            # ==================================================================
            # 2. STOP LOSS CHECK (Fixed SL Exit)
            # ==================================================================
            sl_hit = (current_price <= trade.sl_price) if is_bullish else (current_price >= trade.sl_price)
            if sl_hit:
                trade.stage = "CLOSED_SL"
                trade.closed_at_ist = now_ist
                sl_loss_pts = abs(trade.entry_price - trade.sl_price)
                sl_loss_pct = (sl_loss_pts / trade.entry_price) * 100.0

                msg = (
                    f"❌ 🔴 <b>{trade.symbol}-PERP — STOP LOSS HIT</b>\n"
                    f"<b>Direction:</b> {trade.direction} ({trade.ltf_timeframe})\n"
                    f"<b>Entry Price:</b> ${trade.entry_price:,.2f}\n"
                    f"<b>Exit Price:</b> ${current_price:,.2f}\n"
                    f"<b>Loss:</b> -{sl_loss_pts:,.2f} pts (-{sl_loss_pct:.2f}% | -1.0 R)\n"
                    f"<b>Status:</b> ❌ Trade Closed (LOSS) at {now_ist}"
                )
                updates.append(msg)
                logger.info("Trade %s hit Stop Loss at $%s.", trade_id, current_price)
                continue

        return updates


# Global singleton instance
trade_tracker = TradeTracker()
