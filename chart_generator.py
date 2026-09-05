"""
TradingView-Style Candlestick Chart Generator for Crypto FVG Screener.
Renders clean dark-themed charts with highlighted 4H and LTF Fair Value Gaps,
and TradingView-style Long/Short Risk:Reward position projection tools.
"""

import io
import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import matplotlib
matplotlib.use("Agg")  # Non-interactive headless backend
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

from strategy import Candle, FVG, TPLevels, price_in_fvg

IST = timezone(timedelta(hours=5, minutes=30))

# TradingView Pro Theme Colors
BG_COLOR = "#131722"
PANEL_COLOR = "#1e222d"
GRID_COLOR = "#1e222d"
BORDER_COLOR = "#2a2e39"
TEXT_COLOR = "#d1d4dc"
TEXT_MUTED = "#787b86"

BULL_COLOR = "#089981"  # TradingView Green
BEAR_COLOR = "#f23645"  # TradingView Red

HTF_FVG_COLOR = "#7c3aed"  # Purple for 4H FVG
LTF_FVG_COLOR = "#d97706"  # Amber for LTF FVG

TARGET_GREEN_BOX = "#089981"
STOP_RED_BOX = "#f23645"

TIMEFRAME_MS = {
    "1m": 60 * 1000,
    "3m": 3 * 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 3600 * 1000,
    "1d": 24 * 3600 * 1000,
}


def get_candle_duration_ms(timeframe: Optional[str], default_tf: str = "5m") -> int:
    """Returns the candle duration in milliseconds for any timeframe string."""
    tf = (timeframe or default_tf).lower()
    return TIMEFRAME_MS.get(tf, TIMEFRAME_MS.get(default_tf, 5 * 60 * 1000))


def generate_setup_chart(
    symbol: str,
    direction: str,
    candles_ltf: List[Candle],
    htf_fvg: Optional[FVG],
    ltf_fvg: Optional[FVG],
    entry_price: float,
    sl_price: float,
    tp_levels: TPLevels,
    stage: str = "ACTIVATED",
    ltf_timeframe: str = os.getenv("LTF_TIMEFRAME", "5m"),
    entry_time_ms: Optional[int] = None,
    fvg_formed_time_ms: Optional[int] = None,
    output_path: Optional[str] = None,
) -> bytes:
    """
    Renders a high-resolution TradingView-style candlestick chart PNG image.

    Returns:
        bytes: Raw PNG image data.
    """
    if not candles_ltf:
        return b""

    # Smart Window Slicing: center around the trade formation / entry timestamp
    anchor_ms = fvg_formed_time_ms or (ltf_fvg.formed_at if ltf_fvg else None) or entry_time_ms
    if anchor_ms and anchor_ms > 0 and len(candles_ltf) > 30:
        anchor_idx = min(range(len(candles_ltf)), key=lambda idx: abs(candles_ltf[idx].timestamp - anchor_ms))
        start_win = max(0, anchor_idx - 14)
        end_win = min(len(candles_ltf), anchor_idx + 32)
        view_candles = candles_ltf[start_win:end_win]
    else:
        view_candles = candles_ltf[-45:] if len(candles_ltf) >= 45 else candles_ltf

    n_candles = len(view_candles)
    if n_candles == 0:
        return b""

    fig, ax = plt.subplots(figsize=(13, 7.0), dpi=130)
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # 1. Plot Candlesticks
    candle_width = 0.62
    wick_width = 1.3

    for idx, c in enumerate(view_candles):
        is_green = c.close >= c.open
        color = BULL_COLOR if is_green else BEAR_COLOR
        lower_body = min(c.open, c.close)
        body_height = max(abs(c.close - c.open), (c.high - c.low) * 0.03)

        # Draw Wick (High-Low)
        ax.plot([idx, idx], [c.low, c.high], color=color, linewidth=wick_width, zorder=3)

        # Draw Body
        rect = patches.Rectangle(
            (idx - candle_width / 2, lower_body),
            candle_width,
            body_height,
            linewidth=0.8,
            edgecolor=color,
            facecolor=color,
            zorder=4,
        )
        ax.add_patch(rect)

    # 2. Highlight 4H FVG Zone (Purple Shaded Band)
    if htf_fvg:
        htf_bottom = min(htf_fvg.bottom, htf_fvg.top)
        htf_top = max(htf_fvg.bottom, htf_fvg.top)
        ax.axhspan(
            htf_bottom,
            htf_top,
            color=HTF_FVG_COLOR,
            alpha=0.14,
            zorder=1,
        )
        ax.axhline(htf_bottom, color=HTF_FVG_COLOR, linestyle="--", linewidth=0.8, alpha=0.5, zorder=2)
        ax.axhline(htf_top, color=HTF_FVG_COLOR, linestyle="--", linewidth=0.8, alpha=0.5, zorder=2)
        ax.text(
            0.5,
            (htf_bottom + htf_top) / 2.0,
            f" 4H FVG Zone [${htf_bottom:,.2f} – ${htf_top:,.2f}]",
            color="#c4b5fd",
            fontsize=8.5,
            fontweight="bold",
            va="center",
            zorder=2,
        )
    else:
        htf_bottom = min([c.low for c in view_candles])
        htf_top = max([c.high for c in view_candles])

    # 3. Highlight LTF FVG Zone & Formation Candles [c1, c2, c3]
    fvg_c3_idx = max(2, n_candles - 3)
    formation_anchor = fvg_formed_time_ms or (ltf_fvg.formed_at if ltf_fvg else 0)

    if ltf_fvg:
        ltf_bottom = min(ltf_fvg.bottom, ltf_fvg.top)
        ltf_top = max(ltf_fvg.bottom, ltf_fvg.top)

        # Locate FVG formation candle c3 in view_candles
        for i, c in enumerate(view_candles):
            if c.timestamp == formation_anchor:
                fvg_c3_idx = i
                break
        else:
            if formation_anchor > 0:
                fvg_c3_idx = min(range(len(view_candles)), key=lambda idx: abs(view_candles[idx].timestamp - formation_anchor))

        fvg_c1_idx = max(0, fvg_c3_idx - 2)
        fvg_c2_idx = max(0, fvg_c3_idx - 1)

        # Draw the LTF FVG Zone Box (from c1 forward to right margin)
        ltf_box_width = (n_candles + 8.5) - (fvg_c1_idx - 0.4)
        ltf_rect = patches.Rectangle(
            (fvg_c1_idx - 0.4, ltf_bottom),
            ltf_box_width,
            max(0.0001, ltf_top - ltf_bottom),
            linewidth=1.2,
            edgecolor=LTF_FVG_COLOR,
            facecolor=LTF_FVG_COLOR,
            alpha=0.24,
            zorder=2,
        )
        ax.add_patch(ltf_rect)
        ax.text(
            fvg_c1_idx,
            ltf_top,
            f" {ltf_timeframe} {direction} FVG [${ltf_bottom:,.2f} – ${ltf_top:,.2f}]",
            color="#fbbf24",
            fontsize=8.5,
            fontweight="bold",
            va="bottom",
            zorder=3,
        )

        # Draw visual markers on the 3 candles forming the FVG
        if direction == "Bullish":
            y_c1 = view_candles[fvg_c1_idx].low
            y_c2 = view_candles[fvg_c2_idx].low
            y_c3 = view_candles[fvg_c3_idx].low
            ax.text(fvg_c1_idx, y_c1, " ①", color="#fbbf24", fontsize=9, fontweight="bold", ha="center", va="top", zorder=7)
            ax.text(fvg_c2_idx, y_c2, " ②", color="#fbbf24", fontsize=9, fontweight="bold", ha="center", va="top", zorder=7)
            ax.text(fvg_c3_idx, y_c3, " ③⚡", color="#fbbf24", fontsize=9, fontweight="bold", ha="center", va="top", zorder=7)
        else:
            y_c1 = view_candles[fvg_c1_idx].high
            y_c2 = view_candles[fvg_c2_idx].high
            y_c3 = view_candles[fvg_c3_idx].high
            ax.text(fvg_c1_idx, y_c1, " ①", color="#fbbf24", fontsize=9, fontweight="bold", ha="center", va="bottom", zorder=7)
            ax.text(fvg_c2_idx, y_c2, " ②", color="#fbbf24", fontsize=9, fontweight="bold", ha="center", va="bottom", zorder=7)
            ax.text(fvg_c3_idx, y_c3, " ③⚡", color="#fbbf24", fontsize=9, fontweight="bold", ha="center", va="bottom", zorder=7)
    else:
        ltf_bottom = entry_price
        ltf_top = entry_price

    # 4. TradingView Style Long/Short Position Tool Projection Box & Retrace Entry Annotation
    if stage == "ACTIVATED":
        # ACTIVE: Locate the exact Retrace Entry candle
        entry_idx = min(fvg_c3_idx + 1, n_candles - 1)
        if entry_time_ms and entry_time_ms > 0:
            for idx, c in enumerate(view_candles):
                if c.timestamp == entry_time_ms:
                    entry_idx = idx
                    break
            else:
                entry_idx = min(range(len(view_candles)), key=lambda idx: abs(view_candles[idx].timestamp - entry_time_ms))
        elif ltf_fvg:
            for idx in range(fvg_c3_idx + 1, n_candles):
                chk_c = view_candles[idx]
                if (direction == "Bullish" and chk_c.low <= ltf_fvg.top and chk_c.high >= ltf_fvg.bottom) or \
                   (direction == "Bearish" and chk_c.high >= ltf_fvg.bottom and chk_c.low <= ltf_fvg.top):
                    entry_idx = idx
                    break

        pos_start_idx = entry_idx - 0.25
        box_width = (n_candles + 8.5) - pos_start_idx

        # Pinpoint Retrace Entry Candle with high-contrast badge & arrow
        y_range = max([c.high for c in view_candles]) - min([c.low for c in view_candles])
        y_offset = max(y_range * 0.05, 0.5)

        if direction == "Bullish":
            entry_candle_low = view_candles[entry_idx].low
            ax.annotate(
                "▲ ENTRY",
                xy=(entry_idx, entry_candle_low),
                xytext=(entry_idx, entry_candle_low - y_offset),
                color="#10b981",
                fontsize=8.5,
                fontweight="bold",
                ha="center",
                arrowprops=dict(facecolor="#10b981", edgecolor="#10b981", arrowstyle="->", lw=1.6),
                bbox=dict(boxstyle="round,pad=0.2", facecolor=BG_COLOR, edgecolor="#10b981", alpha=0.95),
                zorder=8,
            )
        else:
            entry_candle_high = view_candles[entry_idx].high
            ax.annotate(
                "▼ ENTRY",
                xy=(entry_idx, entry_candle_high),
                xytext=(entry_idx, entry_candle_high + y_offset),
                color="#f43f5e",
                fontsize=8.5,
                fontweight="bold",
                ha="center",
                arrowprops=dict(facecolor="#f43f5e", edgecolor="#f43f5e", arrowstyle="->", lw=1.6),
                bbox=dict(boxstyle="round,pad=0.2", facecolor=BG_COLOR, edgecolor="#f43f5e", alpha=0.95),
                zorder=8,
            )
    else:
        # PENDING RETRACE: Never overlap with candles! Project into future right margin
        pos_start_idx = n_candles - 0.2
        box_width = 8.5

    if direction == "Bullish":
        # Green Target Box (Entry -> 2.0R TP)
        target_height = tp_levels.r2 - entry_price
        target_box = patches.Rectangle(
            (pos_start_idx, entry_price),
            box_width,
            target_height,
            facecolor=TARGET_GREEN_BOX,
            edgecolor=TARGET_GREEN_BOX,
            alpha=0.22,
            linewidth=1.0,
            zorder=2,
        )
        ax.add_patch(target_box)

        # Red Stop Box (Entry -> SL)
        risk_height = entry_price - sl_price
        stop_box = patches.Rectangle(
            (pos_start_idx, sl_price),
            box_width,
            risk_height,
            facecolor=STOP_RED_BOX,
            edgecolor=STOP_RED_BOX,
            alpha=0.22,
            linewidth=1.0,
            zorder=2,
        )
        ax.add_patch(stop_box)
    else:
        # Bearish: Green Target Box (Entry -> 2.0R TP downward)
        target_height = entry_price - tp_levels.r2
        target_box = patches.Rectangle(
            (pos_start_idx, tp_levels.r2),
            box_width,
            target_height,
            facecolor=TARGET_GREEN_BOX,
            edgecolor=TARGET_GREEN_BOX,
            alpha=0.22,
            linewidth=1.0,
            zorder=2,
        )
        ax.add_patch(target_box)

        # Red Stop Box (Entry -> SL upward)
        risk_height = sl_price - entry_price
        stop_box = patches.Rectangle(
            (pos_start_idx, entry_price),
            box_width,
            risk_height,
            facecolor=STOP_RED_BOX,
            edgecolor=STOP_RED_BOX,
            alpha=0.22,
            linewidth=1.0,
            zorder=2,
        )
        ax.add_patch(stop_box)

    # 5. Draw Key Price Level Lines (from pos_start_idx forward)
    line_end_x = pos_start_idx + box_width
    ax.plot([pos_start_idx, line_end_x], [entry_price, entry_price], color="#818cf8", linestyle="-", linewidth=1.3, alpha=0.95, zorder=5)
    ax.plot([pos_start_idx, line_end_x], [sl_price, sl_price], color=BEAR_COLOR, linestyle="--", linewidth=1.3, alpha=0.95, zorder=5)
    ax.plot([pos_start_idx, line_end_x], [tp_levels.r1, tp_levels.r1], color="#34d399", linestyle=":", linewidth=1.0, alpha=0.75, zorder=5)
    ax.plot([pos_start_idx, line_end_x], [tp_levels.r2, tp_levels.r2], color=BULL_COLOR, linestyle="-", linewidth=1.4, alpha=0.95, zorder=5)

    # Dotted subtle extension to left of pos_start_idx
    ax.axhline(entry_price, color="#818cf8", linestyle=":", linewidth=0.6, alpha=0.35, zorder=1)
    ax.axhline(sl_price, color=BEAR_COLOR, linestyle=":", linewidth=0.6, alpha=0.35, zorder=1)
    ax.axhline(tp_levels.r2, color=BULL_COLOR, linestyle=":", linewidth=0.6, alpha=0.35, zorder=1)

    # Badges / Labels on right side with correct +/- signs
    right_x = n_candles + 1.0
    tp_sign = "+" if direction == "Bullish" else "-"
    sl_sign = "-" if direction == "Bullish" else "+"

    ax.text(
        right_x,
        tp_levels.r2,
        f" TP 2.0R: ${tp_levels.r2:,.2f} ({tp_sign}{tp_levels.r2_points:,.1f} pts)",
        color=BULL_COLOR,
        fontsize=9,
        fontweight="bold",
        va="center",
        bbox=dict(boxstyle="round,pad=0.25", facecolor=BG_COLOR, edgecolor=BULL_COLOR, alpha=0.95),
        zorder=6,
    )
    ax.text(
        right_x,
        entry_price,
        f" ENTRY: ${entry_price:,.2f}",
        color="#a5b4fc",
        fontsize=9,
        fontweight="bold",
        va="center",
        bbox=dict(boxstyle="round,pad=0.25", facecolor=BG_COLOR, edgecolor="#6366f1", alpha=0.95),
        zorder=6,
    )
    ax.text(
        right_x,
        sl_price,
        f" STOP: ${sl_price:,.2f} ({sl_sign}{tp_levels.sl_points:,.1f} pts)",
        color=BEAR_COLOR,
        fontsize=9,
        fontweight="bold",
        va="center",
        bbox=dict(boxstyle="round,pad=0.25", facecolor=BG_COLOR, edgecolor=BEAR_COLOR, alpha=0.95),
        zorder=6,
    )

    # 6. Formatting Axes, Grid & Limits
    ax.set_xlim(-1, n_candles + 11)

    # Calculate robust Y-Limits strictly based on visible price action & trade execution
    all_prices = (
        [c.low for c in view_candles]
        + [c.high for c in view_candles]
        + [sl_price, entry_price, tp_levels.r1, tp_levels.r2, ltf_bottom, ltf_top]
    )
    y_min = min(all_prices)
    y_max = max(all_prices)
    y_padding = max(0.0001, (y_max - y_min) * 0.10)
    ax.set_ylim(y_min - y_padding, y_max + y_padding)

    # Format X-axis with IST timestamps
    x_ticks = np.linspace(0, n_candles - 1, min(7, n_candles), dtype=int)
    x_labels = [
        datetime.fromtimestamp(view_candles[i].timestamp / 1000.0, tz=IST).strftime("%I:%M %p")
        for i in x_ticks
    ]
    ax.set_xticks(x_ticks)
    ax.set_xticklabels(x_labels, color=TEXT_MUTED, fontsize=8.5, fontfamily="monospace")

    # Format Y-axis
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.tick_params(colors=TEXT_MUTED, labelsize=8.5)
    for label in ax.get_yticklabels():
        label.set_fontfamily = "monospace"

    # Grid
    ax.grid(True, color=GRID_COLOR, linestyle="-", linewidth=0.6, alpha=0.8)
    for spine in ax.spines.values():
        spine.set_color(BORDER_COLOR)

    # Header Title
    stage_text = "PENDING RETRACE"
    if stage == "ACTIVATED":
        # Check if price has subsequently breached SL or reached 2.0R TP
        is_sl_hit = False
        is_tp_hit = False
        start_eval_idx = max(0, int(pos_start_idx))
        for c in view_candles[start_eval_idx:]:
            if direction == "Bullish":
                if c.low <= sl_price:
                    is_sl_hit = True
                    break
                if c.high >= tp_levels.r2:
                    is_tp_hit = True
                    break
            else:
                if c.high >= sl_price:
                    is_sl_hit = True
                    break
                if c.low <= tp_levels.r2:
                    is_tp_hit = True
                    break

        if is_sl_hit:
            stage_text = "STOP LOSS HIT"
        elif is_tp_hit:
            stage_text = "2.0R TARGET TP HIT"
        else:
            stage_text = "TRADE ACTIVATED"

    now_ist_str = datetime.now(IST).strftime("%d-%b-%Y %I:%M %p IST")

    plt.title(
        f"{symbol}-PERP · {ltf_timeframe}  |  4H FVG STRATEGY  [{stage_text}]",
        color=TEXT_COLOR,
        fontsize=12,
        fontweight="bold",
        fontfamily="monospace",
        loc="left",
        pad=14,
    )
    plt.suptitle(
        f"Generated at {now_ist_str}",
        color=TEXT_MUTED,
        fontsize=8.5,
        fontfamily="monospace",
        x=0.86,
        y=0.96,
    )

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", facecolor=BG_COLOR, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    img_bytes = buf.getvalue()

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(img_bytes)

    return img_bytes


def generate_extreme_setup_chart(
    symbol: str,
    direction: str,
    candles_ltf: List[Candle],
    htf_fvg_bottom: float,
    htf_fvg_top: float,
    htf_first_touch_ist: Optional[str],
    ltf_fvg_bottom: float,
    ltf_fvg_top: float,
    ltf_fvg_formed_ts: int,
    entry_price: float,
    stop_loss: float,
    tp_1r: float,
    tp_2r: float,
    tp_3r: float,
    state: str = "PENDING_RETRACE",
    floating_r: float = 0.0,
    ltf_timeframe: str = os.getenv("EXTREME_LTF_TIMEFRAME", "5m"),
    entry_time_ts: Optional[int] = None,
    exit_time_ts: Optional[int] = None,
    output_path: Optional[str] = None,
) -> bytes:
    """
    Renders a high-resolution TradingView-style chart for Strategy 2 (Extreme LTF FVG).
    Highlights 4H Anchor Zone (purple), Extreme LTF FVG (amber), Entry, SL, and 1R/2R/3R targets.
    """
    if not candles_ltf:
        return b""

    c_dur = get_candle_duration_ms(ltf_timeframe, os.getenv("EXTREME_LTF_TIMEFRAME", "5m"))

    # Smart Window Slicing:
    # For live setups, always include candles leading up to the current live moment.
    # For historical backtest trades, show from entry to exit time + delta on both sides (and LTF FVG formation if within range).
    is_historical = str(state).startswith("HISTORICAL_") or (exit_time_ts is not None and exit_time_ts > 0)
    if not is_historical:
        view_candles = candles_ltf[-50:] if len(candles_ltf) >= 50 else candles_ltf
    else:
        if len(candles_ltf) <= 60 and (entry_time_ts is None or abs(candles_ltf[0].timestamp - (entry_time_ts or 0)) < 24 * 3600 * 1000):
            view_candles = candles_ltf
        else:
            anchor_entry = entry_time_ts or (candles_ltf[0].timestamp if candles_ltf else 0)
            entry_idx = min(range(len(candles_ltf)), key=lambda idx: abs(candles_ltf[idx].timestamp - anchor_entry))
            
            if exit_time_ts and exit_time_ts > 0:
                exit_idx = min(range(len(candles_ltf)), key=lambda idx: abs(candles_ltf[idx].timestamp - exit_time_ts))
            else:
                exit_idx = min(len(candles_ltf) - 1, entry_idx + 15)

            # Check if LTF FVG formation fits within delta (<= 25 bars before entry)
            start_win = max(0, entry_idx - 8)
            if ltf_fvg_formed_ts and 0 < (anchor_entry - ltf_fvg_formed_ts) <= 25 * c_dur:
                fvg_idx = min(range(len(candles_ltf)), key=lambda idx: abs(candles_ltf[idx].timestamp - ltf_fvg_formed_ts))
                start_win = max(0, min(start_win, fvg_idx - 4))

            end_win = min(len(candles_ltf), max(exit_idx + 8, entry_idx + 12))
            view_candles = candles_ltf[start_win:end_win]

    n_candles = len(view_candles)
    if n_candles == 0:
        return b""

    fig, ax = plt.subplots(figsize=(13, 7.2), dpi=130)
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    candle_width = 0.62
    wick_width = 1.3

    # 1. Draw Candlesticks
    for idx, c in enumerate(view_candles):
        is_green = c.close >= c.open
        color = BULL_COLOR if is_green else BEAR_COLOR
        lower_body = min(c.open, c.close)
        body_height = max(abs(c.close - c.open), (c.high - c.low) * 0.03)

        ax.plot([idx, idx], [c.low, c.high], color=color, linewidth=wick_width, zorder=3)
        rect = patches.Rectangle(
            (idx - candle_width / 2, lower_body),
            candle_width,
            body_height,
            linewidth=0.8,
            edgecolor=color,
            facecolor=color,
            zorder=4,
        )
        ax.add_patch(rect)

    # 2. Determine local price range for crisp candlestick resolution
    local_prices = (
        [c.high for c in view_candles]
        + [c.low for c in view_candles]
        + [entry_price, stop_loss, tp_1r, tp_2r, tp_3r, ltf_fvg_top, ltf_fvg_bottom]
    )
    local_min = min(local_prices)
    local_max = max(local_prices)
    local_range = max(1e-6, local_max - local_min)

    # Check if 4H Anchor Zone is reasonably near the local trade action (within 15% margin)
    is_htf_near = (
        htf_fvg_bottom >= (local_min - 0.15 * local_range)
        and htf_fvg_top <= (local_max + 0.15 * local_range)
    )

    if is_htf_near:
        # Fit 4H anchor into chart Y-axis and draw shaded horizontal span
        all_prices = local_prices + [htf_fvg_top, htf_fvg_bottom]
        y_min, y_max = min(all_prices), max(all_prices)
        y_pad = (y_max - y_min) * 0.08
        ax.set_ylim(y_min - y_pad, y_max + y_pad)

        ax.axhspan(
            htf_fvg_bottom,
            htf_fvg_top,
            xmin=0,
            xmax=1,
            color=HTF_FVG_COLOR,
            alpha=0.18,
            label="4H Anchor Zone",
            zorder=2,
        )
        ax.axhline(htf_fvg_top, color=HTF_FVG_COLOR, linestyle=":", linewidth=1.0, alpha=0.6)
        ax.axhline(htf_fvg_bottom, color=HTF_FVG_COLOR, linestyle=":", linewidth=1.0, alpha=0.6)

        mid_htf = (htf_fvg_bottom + htf_fvg_top) / 2
        touch_info = f" (1st Touch: {htf_first_touch_ist})" if htf_first_touch_ist else ""
        ax.text(2, mid_htf, f"4H ANCHOR ZONE [${htf_fvg_bottom:,.2f} - ${htf_fvg_top:,.2f}]{touch_info}", color="#c084fc", fontsize=8, fontfamily="monospace", va="center")
    else:
        # 4H Anchor is far away: Do NOT expand Y-axis. Keep candlesticks crisp and clear!
        y_min = local_min
        y_max = local_max
        y_pad = local_range * 0.12
        ax.set_ylim(y_min - y_pad, y_max + y_pad)

        # Draw a prominent, sleek 4H Anchor Info Badge at the top-left
        arrow = "▲ Above" if htf_fvg_bottom > local_max else "▼ Below"
        touch_info = f" | 1st Touch: {htf_first_touch_ist}" if htf_first_touch_ist else ""
        htf_badge_text = f"4H ANCHOR ({arrow} Chart): [${htf_fvg_bottom:,.2f} - ${htf_fvg_top:,.2f}]{touch_info}"

        ax.text(
            0.02,
            0.94,
            htf_badge_text,
            transform=ax.transAxes,
            color="#e9d5ff",
            fontsize=8.5,
            fontweight="bold",
            fontfamily="monospace",
            va="top",
            bbox=dict(boxstyle="round,pad=0.45", facecolor="#2e1065", edgecolor="#7c3aed", alpha=0.9, linewidth=1.1),
            zorder=10,
        )

    # 3. Locate Entry Candle
    entry_idx = None
    # Priority 1: Match exact entry_time_ts if passed
    if entry_time_ts and entry_time_ts > 0:
        for idx, c in enumerate(view_candles):
            if abs(c.timestamp - entry_time_ts) < (c_dur / 2) or (c.timestamp <= entry_time_ts < c.timestamp + c_dur):
                entry_idx = idx
                break

    # Priority 2: Chronological scan for first candle post-formation that touched entry price
    if entry_idx is None:
        min_post_formation_ts = (ltf_fvg_formed_ts + c_dur) if ltf_fvg_formed_ts else 0
        for idx, c in enumerate(view_candles):
            if min_post_formation_ts and c.timestamp < min_post_formation_ts:
                continue
            if direction == "Bullish" and c.low <= entry_price:
                entry_idx = idx
                break
            elif direction == "Bearish" and c.high >= entry_price:
                entry_idx = idx
                break

    # 4. Draw Extreme LTF FVG (Amber)
    ltf_start_idx = 0
    if ltf_fvg_formed_ts:
        for idx, c in enumerate(view_candles):
            if c.timestamp >= ltf_fvg_formed_ts:
                ltf_start_idx = max(0, idx - 2)
                break

    # If trade has filled entry, box terminates cleanly at entry; otherwise extends forward
    if entry_idx is not None and 0 <= entry_idx < n_candles:
        fvg_box_end = min(n_candles - 1, entry_idx) + 1.2
    else:
        fvg_box_end = n_candles + 2.5
    fvg_width = max(1.0, fvg_box_end - ltf_start_idx)

    ltf_fvg_rect = patches.Rectangle(
        (ltf_start_idx, min(ltf_fvg_bottom, ltf_fvg_top)),
        fvg_width,
        abs(ltf_fvg_top - ltf_fvg_bottom),
        linewidth=1.2,
        edgecolor=LTF_FVG_COLOR,
        facecolor=LTF_FVG_COLOR,
        alpha=0.28,
        zorder=2,
    )
    ax.add_patch(ltf_fvg_rect)

    # 5. Draw Trade Position Lines (Entry, SL, TP 1R, 2R, 3R)
    entry_line_color = "#38bdf8" if direction == "Bullish" else "#fb923c"
    ax.axhline(entry_price, color=entry_line_color, linestyle="-", linewidth=1.8, label=f"Entry: ${entry_price:,.2f}", zorder=5)
    ax.axhline(stop_loss, color=STOP_RED_BOX, linestyle="--", linewidth=1.6, label=f"Stop Loss: ${stop_loss:,.2f}", zorder=5)
    ax.axhline(tp_1r, color="#22d3ee", linestyle=":", linewidth=1.2, label=f"TP 1R: ${tp_1r:,.2f}", zorder=5)
    ax.axhline(tp_2r, color=TARGET_GREEN_BOX, linestyle="-", linewidth=2.0, label=f"TP 2R (Primary): ${tp_2r:,.2f}", zorder=5)
    ax.axhline(tp_3r, color="#34d399", linestyle=":", linewidth=1.2, label=f"TP 3R: ${tp_3r:,.2f}", zorder=5)

    # Annotations on the right price axis with clean styling
    right_x = n_candles + 0.2
    pill_kw = dict(boxstyle="square,pad=0.15", facecolor=BG_COLOR, edgecolor="none", alpha=0.75)
    ax.text(right_x, entry_price, f" ENTRY ${entry_price:,.2f}", color=entry_line_color, fontsize=8, fontweight="bold", fontfamily="monospace", va="center", bbox=pill_kw, zorder=6)
    ax.text(right_x, stop_loss, f" SL ${stop_loss:,.2f}", color=STOP_RED_BOX, fontsize=8, fontweight="bold", fontfamily="monospace", va="center", bbox=pill_kw, zorder=6)
    ax.text(right_x, tp_1r, f" 1R ${tp_1r:,.2f}", color="#22d3ee", fontsize=7.5, fontfamily="monospace", va="center", bbox=pill_kw, zorder=6)
    ax.text(right_x, tp_2r, f" 2R ${tp_2r:,.2f} ★", color=TARGET_GREEN_BOX, fontsize=8, fontweight="bold", fontfamily="monospace", va="center", bbox=pill_kw, zorder=6)
    ax.text(right_x, tp_3r, f" 3R ${tp_3r:,.2f}", color="#34d399", fontsize=7.5, fontfamily="monospace", va="center", bbox=pill_kw, zorder=6)

    # Annotate the Entry Candle
    if entry_idx is not None and 0 <= entry_idx < n_candles:
        entry_c = view_candles[entry_idx]
        entry_color = "#38bdf8" if direction == "Bullish" else "#fb923c"
        ax.axvline(entry_idx, color=entry_color, linestyle=":", linewidth=1.2, alpha=0.6, zorder=2)

        if direction == "Bullish":
            text_y = min(entry_c.low, entry_price) - (local_range * 0.08)
            ax.annotate(
                f"▲ ENTRY FILLED\n${entry_price:,.2f}",
                xy=(entry_idx, entry_price),
                xytext=(entry_idx, text_y),
                ha="center",
                va="top",
                color="#38bdf8",
                fontsize=8,
                fontweight="bold",
                fontfamily="monospace",
                arrowprops=dict(arrowstyle="->", color="#38bdf8", lw=1.2),
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#0c4a6e", edgecolor="#0284c7", alpha=0.85, linewidth=1.0),
                zorder=7,
            )
        else:
            text_y = min(y_max - 0.03 * local_range, max(entry_c.high, entry_price) + (local_range * 0.05))
            ax.annotate(
                f"▼ ENTRY FILLED\n${entry_price:,.2f}",
                xy=(entry_idx, entry_price),
                xytext=(entry_idx, text_y),
                ha="center",
                va="bottom",
                color="#fb923c",
                fontsize=8,
                fontweight="bold",
                fontfamily="monospace",
                arrowprops=dict(arrowstyle="->", color="#fb923c", lw=1.2),
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#7c2d12", edgecolor="#ea580c", alpha=0.85, linewidth=1.0),
                zorder=7,
            )

    # 6. Mark the Exit Candle (if historical trade or exit timestamp is provided)
    if exit_time_ts and exit_time_ts > 0:
        exit_idx = None
        # Match exact candle containing or closest to exit timestamp using timeframe duration
        for idx, c in enumerate(view_candles):
            if abs(c.timestamp - exit_time_ts) < (c_dur / 2) or (c.timestamp <= exit_time_ts < c.timestamp + c_dur):
                exit_idx = idx
                break

        if exit_idx is None and view_candles:
            closest_idx = min(range(len(view_candles)), key=lambda idx: abs(view_candles[idx].timestamp - exit_time_ts))
            if abs(view_candles[closest_idx].timestamp - exit_time_ts) <= 2 * c_dur:
                exit_idx = closest_idx

        if exit_idx is not None and 0 <= exit_idx < n_candles:
            exit_c = view_candles[exit_idx]
            is_win = ("TP" in str(state).upper() and "STOPPED" not in str(state).upper()) or (floating_r > 0 and not str(state).startswith("HISTORICAL_"))
            exit_color = TARGET_GREEN_BOX if is_win else STOP_RED_BOX
            ax.axvline(exit_idx, color=exit_color, linestyle=":", linewidth=1.2, alpha=0.6, zorder=2)

            exit_label = "★ TP EXIT" if is_win else "✖ SL EXIT"
            exit_bg = "#064e3b" if is_win else "#7f1d1d"
            exit_border = "#10b981" if is_win else "#ef4444"

            if direction == "Bullish":
                y_pos = exit_c.high if is_win else exit_c.low
                text_y = min(y_max - 0.03 * local_range, y_pos + (local_range * 0.05)) if is_win else max(y_min + 0.03 * local_range, y_pos - (local_range * 0.08))
                va = "bottom" if is_win else "top"
            else:
                y_pos = exit_c.low if is_win else exit_c.high
                text_y = max(y_min + 0.03 * local_range, y_pos - (local_range * 0.08)) if is_win else min(y_max - 0.03 * local_range, y_pos + (local_range * 0.05))
                va = "top" if is_win else "bottom"

            ax.annotate(
                f"{exit_label}",
                xy=(exit_idx, y_pos),
                xytext=(exit_idx, text_y),
                ha="center",
                va=va,
                color=exit_color,
                fontsize=8,
                fontweight="bold",
                fontfamily="monospace",
                arrowprops=dict(arrowstyle="->", color=exit_color, lw=1.2),
                bbox=dict(boxstyle="round,pad=0.25", facecolor=exit_bg, edgecolor=exit_border, alpha=0.85, linewidth=1.0),
                zorder=7,
            )

    # Styling and Grid
    ax.grid(True, color=GRID_COLOR, linestyle="--", linewidth=0.5, alpha=0.7)
    ax.set_xlim(-1, n_candles + 5)

    # X-axis Timestamps in IST
    step = max(1, n_candles // 7)
    x_indices = list(range(0, n_candles, step))
    x_labels = [datetime.fromtimestamp(view_candles[i].timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p") for i in x_indices]
    ax.set_xticks(x_indices)
    ax.set_xticklabels(x_labels, color=TEXT_MUTED, fontsize=8, fontfamily="monospace")
    ax.yaxis.tick_right()
    ax.tick_params(colors=TEXT_MUTED, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(BORDER_COLOR)

    # Header and Status
    state_str = str(state).replace("HISTORICAL_", "").upper()
    if state_str == "TRADE_ACTIVE":
        status_str = f"ACTIVE ({floating_r:+.2f}R)"
    elif state_str in ("COMPLETED_TP", "TP_HIT", "WIN") or "TP" in state_str:
        status_str = f"COMPLETED_TP ({floating_r:+.2f}R)"
    elif state_str in ("STOPPED_OUT", "SL_HIT", "LOSS") or "STOPPED" in state_str or "SL" in state_str:
        status_str = f"STOPPED_OUT ({floating_r:+.2f}R)"
    elif state_str == "INVALIDATED":
        status_str = "INVALIDATED"
    elif state_str == "PENDING_RETRACE":
        status_str = "PENDING RETRACE"
    else:
        status_str = f"{state_str} ({floating_r:+.2f}R)" if floating_r != 0 else state_str

    now_ist_str = datetime.now(IST).strftime("%d-%b-%Y %I:%M:%S %p IST")
    plt.title(
        f"{symbol}-PERP · {ltf_timeframe}  |  EXTREME LTF STRATEGY  [{status_str}]",
        color=TEXT_COLOR,
        fontsize=12,
        fontweight="bold",
        fontfamily="monospace",
        loc="left",
        pad=14,
    )
    plt.suptitle(
        f"IST: {now_ist_str}",
        color=TEXT_MUTED,
        fontsize=8.5,
        fontfamily="monospace",
        x=0.86,
        y=0.96,
    )

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", facecolor=BG_COLOR, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    img_bytes = buf.getvalue()

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(img_bytes)

    return img_bytes

