"""
TradingView-Style Candlestick Chart Generator for Crypto FVG Screener.
Renders clean dark-themed charts with highlighted 4H and LTF Fair Value Gaps,
and TradingView-style Long/Short Risk:Reward position projection tools.
"""

import io
import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional

# Canonical timeframe table shared app-wide (defined in candle_store)
from candle_store import TIMEFRAME_MS

import matplotlib
matplotlib.use("Agg")  # Non-interactive headless backend
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

from strategy_extreme_fvg import Candle

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


def get_candle_duration_ms(timeframe: Optional[str], default_tf: str = "5m") -> int:
    """Returns the candle duration in milliseconds for any timeframe string."""
    tf = (timeframe or default_tf).lower()
    return TIMEFRAME_MS.get(tf, TIMEFRAME_MS.get(default_tf, 5 * 60 * 1000))


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
        if entry_time_ts and entry_time_ts > 0 and len(candles_ltf) > 50:
            anchor_ms = min(entry_time_ts, ltf_fvg_formed_ts or entry_time_ts)
            entry_idx = min(range(len(candles_ltf)), key=lambda idx: abs(candles_ltf[idx].timestamp - anchor_ms))
            start_win = max(0, entry_idx - 6)
            view_candles = candles_ltf[start_win:]
            if len(view_candles) > 200:
                view_candles = view_candles[-200:]
        else:
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
    ltf_formed_str = datetime.fromtimestamp(ltf_fvg_formed_ts / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST") if ltf_fvg_formed_ts else "--"
    ax.text(
        ltf_start_idx,
        max(ltf_fvg_bottom, ltf_fvg_top),
        f" Extreme {ltf_timeframe} {direction} FVG (Formed: {ltf_formed_str}) [${min(ltf_fvg_bottom, ltf_fvg_top):,.2f} – ${max(ltf_fvg_bottom, ltf_fvg_top):,.2f}]",
        color="#fbbf24",
        fontsize=8.5,
        fontweight="bold",
        va="bottom",
        zorder=3,
    )

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
    ltf_formed_sub = ltf_formed_str if 'ltf_formed_str' in locals() else "--"
    subtitle_str = f"LTF FVG Formed: {ltf_formed_sub}"
    if htf_first_touch_ist:
        subtitle_str += f" | 4H 1st Touch: {htf_first_touch_ist}"
    subtitle_str += f" | Generated: {now_ist_str}"

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
        subtitle_str,
        color=TEXT_MUTED,
        fontsize=8.5,
        fontfamily="monospace",
        x=0.76,
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

