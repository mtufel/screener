"""
Historical Backtester for Strategy 3 — Liquidity-Sweep FVG.

Mirrors the Strategy 2 backtest loop (chronological LTF FVG formation events,
4H anchor reconstruction, conservative SL-first execution) and adds:

1. Gates between candidate discovery and entry:
   - gap-band exclusion (default 0.10–0.20%),
   - fresh opposing-side liquidity sweep before FVG formation (structural
     pools built as-of each formation close — no lookahead),
   - entry-session filter at actual fill time,
   - anchor-age guard at fill time (dead zone [24h, 48h) by default).
2. Liquidity-first TP: when tp_mode="LIQUIDITY", the target is the nearest
   opposing pool at least min_rr R beyond entry (buffered 0.02% in front of
   the level); realized R = signed distance-to-TP / risk. Falls back to fixed
   fallback_target_r when no qualifying pool exists (mode recorded per trade).

The simulation loop intentionally mirrors backtest_extreme_fvg.py so results
are directly comparable to the S2 baseline.
"""

import argparse
import asyncio
import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import logging
import os
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from dotenv import load_dotenv

from liquidity import (
    Candle,
    LiquidityPool,
    build_pool_templates,
    detect_swing_points,
    parse_gap_band,
    pools_from_templates,
)
from strategy_extreme_fvg import (
    FVG,
    TouchedAnchor,
    HTF_CANDLE_DURATION_MS,
    TIMEFRAME_MS,
    compute_all_active_4h_fvgs,
)
from strategy_liquidity_sweep_fvg import (
    DEAD_ZONE_DEFAULT,
    DEFAULT_TP_BUFFER_PCT,
    apply_liquidity_tp,
    evaluate_fill_gates,
    evaluate_formation_gates,
    select_extreme_gated_fvg,
)
from backtest_extreme_fvg import ExtremeHistoricalTrade
from session_filter import SessionFilterConfig

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
logger = logging.getLogger("strategy3-backtester")


@dataclass
class Strategy3Trade(ExtremeHistoricalTrade):
    """S2 trade record plus Strategy 3 metadata (tp mode, sweep, gate stats)."""

    tp_mode: str = "FIXED_R"
    tp_pool_kind: Optional[str] = None
    tp_pool_price: Optional[float] = None
    sweep_pool_kind: Optional[str] = None
    sweep_pool_price: Optional[float] = None
    sweep_ts: Optional[int] = None
    anchor_age_h_at_fill: float = 0.0
    realized_r: float = -1.0  # actual R for this trade's own tp_mode

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "tp_mode": self.tp_mode,
            "tp_pool": {"kind": self.tp_pool_kind, "price": self.tp_pool_price} if self.tp_pool_price else None,
            "sweep": {"kind": self.sweep_pool_kind, "price": self.sweep_pool_price,
                      "ts": self.sweep_ts} if self.sweep_pool_kind else None,
            "anchor_age_h_at_fill": round(self.anchor_age_h_at_fill, 2),
            "realized_r": round(self.realized_r, 3),
        })
        return base


@dataclass
class Strategy3BacktestReport:
    """Summary statistics for a Strategy 3 backtest run."""

    symbol: str
    days: int
    ltf_timeframe: str
    invalidation_mode: str
    min_gap_pct: float
    require_sweep: bool
    sweep_max_age_h: float
    gap_band: Optional[Tuple[float, float]]
    anchor_age_guard: bool
    tp_mode: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    net_r: float
    profit_factor: float
    max_drawdown_r: float
    avg_hold_min: float
    avg_mfe_r: float
    liquidity_tp_count: int
    fixed_tp_count: int
    gate_rejects: Dict[str, int]
    trades: List[Strategy3Trade] = field(default_factory=list)

    def summary_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "days": self.days, "ltf": self.ltf_timeframe,
            "total_trades": self.total_trades, "win_rate": round(self.win_rate, 1),
            "net_r": round(self.net_r, 1), "profit_factor": None if self.profit_factor == float("inf") else round(self.profit_factor, 2),
            "max_drawdown_r": round(self.max_drawdown_r, 1), "liquidity_tp": self.liquidity_tp_count,
            "fixed_tp": self.fixed_tp_count, "gate_rejects": self.gate_rejects,
        }


def _hour(ts_ms: int) -> int:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour


async def run_liquidity_sweep_backtest(
    symbol: str,
    days: int = 90,
    ltf_timeframe: str = "5m",
    use_close_invalidation: bool = False,
    min_gap_pct: float = 0.05,
    require_sweep: bool = True,
    sweep_max_age_h: float = 2.0,
    gap_band_exclude: Optional[str] = "0.10,0.20",
    anchor_age_guard: bool = True,
    tp_mode: str = "LIQUIDITY",
    min_rr_for_liquidity: float = 1.5,
    fallback_target_r: float = 2.0,
    entry_sessions: Optional[str] = "NY_KZ",
    entry_weekday_only: bool = True,
    session_filter: bool = False,
    weekday_filter: bool = False,
    sessions: Optional[str] = None,
    client: Optional[Any] = None,
) -> Strategy3BacktestReport:
    """Runs the Strategy 3 simulation over `days` of history."""
    from market_data_provider import market_data_provider as _default_provider
    prov = client or _default_provider
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 24 * 3600 * 1000
    fetch_from = start_ms - 14 * 24 * 3600 * 1000  # 4H warmup

    try:
        if hasattr(prov, "get_historical_candles_range"):
            raw_4h = await prov.get_historical_candles_range(symbol, "4h", fetch_from, now_ms)
            raw_ltf = await prov.get_historical_candles_range(symbol, ltf_timeframe, fetch_from, now_ms)
        else:
            from hyperliquid_client import SYMBOL_ALIASES
            raw_sym = SYMBOL_ALIASES.get(symbol.upper(), symbol.upper())
            raw_4h = await prov.get_candle_snapshot(raw_sym, "4h", fetch_from, now_ms)
            raw_ltf = await prov.get_candle_snapshot(raw_sym, ltf_timeframe, fetch_from, now_ms)
    except Exception as exc:
        logger.error("Failed to retrieve historical candles for %s (%s): %s", symbol, ltf_timeframe, exc)
        raw_4h, raw_ltf = [], []

    def _empty_report() -> Strategy3BacktestReport:
        return Strategy3BacktestReport(
            symbol=symbol, days=days, ltf_timeframe=ltf_timeframe,
            invalidation_mode="close" if use_close_invalidation else "wick",
            min_gap_pct=min_gap_pct, require_sweep=require_sweep, sweep_max_age_h=sweep_max_age_h,
            gap_band=parse_gap_band(gap_band_exclude) if isinstance(gap_band_exclude, str) else gap_band_exclude,
            anchor_age_guard=anchor_age_guard, tp_mode=tp_mode,
            total_trades=0, wins=0, losses=0, win_rate=0.0, net_r=0.0, profit_factor=0.0,
            max_drawdown_r=0.0, avg_hold_min=0.0, avg_mfe_r=0.0,
            liquidity_tp_count=0, fixed_tp_count=0, gate_rejects=defaultdict(int),
        )

    if not raw_4h or not raw_ltf:
        logger.warning("Insufficient historical candles for %s (%s)", symbol, ltf_timeframe)
        return _empty_report()

    candles_4h = [Candle.from_dict(c) for c in sorted(raw_4h, key=lambda x: x.get("t", 0))]
    candles_ltf = [Candle.from_dict(c) for c in sorted(raw_ltf, key=lambda x: x.get("t", 0))]

    ltf_duration_ms = TIMEFRAME_MS.get(ltf_timeframe, 5 * 60 * 1000)
    ltf_timestamps = [c.timestamp for c in candles_ltf]
    ltf_close_timestamps = [c.timestamp + ltf_duration_ms for c in candles_ltf]
    htf_close_timestamps = [c.timestamp + HTF_CANDLE_DURATION_MS for c in candles_4h]

    # Pool construction is the hot path: precompute cluster templates once,
    # then reconstruct as-of views in O(#pools).
    full_swings = detect_swing_points(candles_ltf)
    templates = build_pool_templates(candles_ltf, timeframe=ltf_timeframe, swings=full_swings)

    def pools_asof(asof_ms: int) -> List[LiquidityPool]:
        return pools_from_templates(templates, asof_ms)

    band: Optional[Tuple[float, float]] = (
        parse_gap_band(gap_band_exclude) if isinstance(gap_band_exclude, str) else gap_band_exclude
    )
    sweep_max_age_ms = sweep_max_age_h * 3_600_000.0

    # Pre-detect all LTF 3-candle FVGs (same pre-filter as S2 backtester).
    ltf_fvgs: List[Tuple[int, FVG]] = []
    for idx in range(len(candles_ltf) - 2):
        c1, c2, c3 = candles_ltf[idx], candles_ltf[idx + 1], candles_ltf[idx + 2]
        if c3.low > c1.high:
            gap = (c3.low - c1.high) / c1.high * 100.0
            if gap >= min_gap_pct:
                ltf_fvgs.append((idx + 2, FVG(direction="Bullish", top=c3.low, bottom=c1.high,
                                               c1=c1, c2=c2, c3=c3, formed_at=c3.timestamp, timeframe=ltf_timeframe)))
        elif c3.high < c1.low:
            gap = (c1.low - c3.high) / c1.low * 100.0
            if gap >= min_gap_pct:
                ltf_fvgs.append((idx + 2, FVG(direction="Bearish", top=c1.low, bottom=c3.high,
                                               c1=c1, c2=c2, c3=c3, formed_at=c3.timestamp, timeframe=ltf_timeframe)))

    executed: List[Strategy3Trade] = []
    gate_rejects: Dict[str, int] = defaultdict(int)
    entered_fvg_timestamps = set()
    first_touch_map: Dict[int, Optional[Tuple[int, str]]] = {}

    def get_4h_first_touch(fvg: FVG) -> Optional[Tuple[int, str]]:
        if fvg.formed_at in first_touch_map:
            return first_touch_map[fvg.formed_at]
        fvg_close_ts = fvg.close_timestamp
        s = bisect.bisect_left(ltf_timestamps, fvg_close_ts)
        res = None
        for c in candles_ltf[s:]:
            if fvg.direction == "Bullish":
                if c.low <= fvg.top and c.high >= fvg.bottom:
                    res = (c.timestamp, ltf_timeframe)
                    break
            else:
                if c.high >= fvg.bottom and c.low <= fvg.top:
                    res = (c.timestamp, ltf_timeframe)
                    break
        first_touch_map[fvg.formed_at] = res
        return res

    active_4h_cache: Dict[int, List[FVG]] = {}

    fvg_ptr = 0
    n_fvgs = len(ltf_fvgs)
    n_ltf = len(candles_ltf)
    curr_sim_idx = 2

    while fvg_ptr < n_fvgs:
        fvg_idx, current_fvg = ltf_fvgs[fvg_ptr]
        if fvg_idx < curr_sim_idx:
            fvg_ptr += 1
            continue

        curr_time = ltf_close_timestamps[fvg_idx]
        curr_ltf = candles_ltf[fvg_idx]

        num_4h = bisect.bisect_right(htf_close_timestamps, curr_time)
        if num_4h < 3:
            fvg_ptr += 1
            continue

        if num_4h not in active_4h_cache:
            closed_4h = candles_4h[:num_4h]
            active_4h_cache[num_4h] = compute_all_active_4h_fvgs(
                candles_4h=closed_4h,
                current_time_ms=htf_close_timestamps[num_4h - 1],
                use_close_invalidation=use_close_invalidation,
                enforce_closed_filter=True,
            )
        base_4h = active_4h_cache[num_4h]
        active_4h = [
            f for f in base_4h
            if not ((f.direction == "Bullish" and curr_ltf.close < f.bottom) or (f.direction == "Bearish" and curr_ltf.close > f.top))
        ]
        if not active_4h:
            fvg_ptr += 1
            continue

        touched_anchors: List[TouchedAnchor] = []
        for fvg in active_4h:
            ft_info = get_4h_first_touch(fvg)
            if not ft_info or ft_info[0] > curr_ltf.timestamp:
                continue
            first_ts, first_tf = ft_info
            start_rec = bisect.bisect_left(ltf_timestamps, first_ts)
            rec_ts = first_ts
            is_inside = False
            for c in reversed(candles_ltf[start_rec:fvg_idx + 1]):
                if fvg.direction == "Bullish":
                    if c.low <= fvg.top and c.high >= fvg.bottom:
                        rec_ts = c.timestamp
                        is_inside = (curr_ltf.close >= fvg.bottom and curr_ltf.close <= fvg.top)
                        break
                else:
                    if c.high >= fvg.bottom and c.low <= fvg.top:
                        rec_ts = c.timestamp
                        is_inside = (curr_ltf.close >= fvg.bottom and curr_ltf.close <= fvg.top)
                        break
            touched_anchors.append(TouchedAnchor(fvg=fvg, first_touch_timestamp=first_ts,
                                                 most_recent_touch_timestamp=rec_ts,
                                                 is_currently_inside=is_inside, touch_timeframe=first_tf))
        if not touched_anchors:
            fvg_ptr += 1
            continue
        touched_anchors.sort(key=lambda a: (a.is_currently_inside, a.most_recent_touch_timestamp), reverse=True)
        anchor = touched_anchors[0]

        if current_fvg.direction != anchor.fvg.direction or current_fvg.close_timestamp < anchor.first_touch_timestamp:
            fvg_ptr += 1
            continue

        # Candidate pool: same-direction, post-touch, not yet used, not invalidated.
        candidate_pool: List[FVG] = []
        for prev_ptr in range(fvg_ptr + 1):
            p_idx, p_fvg = ltf_fvgs[prev_ptr]
            if p_fvg.direction != anchor.fvg.direction or p_fvg.close_timestamp < anchor.first_touch_timestamp:
                continue
            if p_fvg.formed_at in entered_fvg_timestamps:
                continue
            is_inval = False
            for sub_c in candles_ltf[p_idx + 1:fvg_idx + 1]:
                if p_fvg.direction == "Bullish":
                    if sub_c.low <= min(p_fvg.c1.low, p_fvg.c2.low, p_fvg.c3.low):
                        is_inval = True
                        break
                else:
                    if sub_c.high >= max(p_fvg.c1.high, p_fvg.c2.high, p_fvg.c3.high):
                        is_inval = True
                        break
            if not is_inval:
                candidate_pool.append(p_fvg)
        if not candidate_pool:
            fvg_ptr += 1
            continue

        # ---- Strategy 3 gates: shared two-phase pipeline ----
        # Phase 1 (gap band, fresh sweep) is decidable per candidate at
        # formation, so it filters the pool before the extreme selection.
        gated: List[Tuple[FVG, Optional[LiquidityPool], Optional[int]]] = []
        for f in candidate_pool:
            formation = evaluate_formation_gates(
                f,
                gap_band=band,
                require_sweep=require_sweep,
                anchor_first_touch_ts=anchor.first_touch_timestamp,
                candles_ltf=candles_ltf,
                sweep_max_age_ms=sweep_max_age_ms,
                ltf_timeframe=ltf_timeframe,
                timestamps=ltf_timestamps,
            )
            if not formation.passed:
                gate_rejects[formation.reason] += 1
            else:
                gated.append((f, formation.pool, formation.sweep_ts))
        if not gated:
            fvg_ptr += 1
            continue

        # Extreme selection — shared rule, so live and backtest agree on the winner.
        selected = select_extreme_gated_fvg(gated, anchor.fvg.direction)
        if selected is None:
            fvg_ptr += 1
            continue
        best_ltf, best_pool, best_sweep_ts = selected

        if best_ltf.formed_at in entered_fvg_timestamps:
            fvg_ptr += 1
            continue

        # Formation-session filter (S2 parity, off by default here).
        if session_filter and sessions:
            from session_filter import is_in_session
            if not is_in_session(best_ltf.close_timestamp, sessions):
                gate_rejects["FORMATION_SESSION"] += 1
                entered_fvg_timestamps.add(best_ltf.formed_at)
                fvg_ptr += 1
                continue

        is_bullish = best_ltf.direction == "Bullish"
        entry_price = best_ltf.top if is_bullish else best_ltf.bottom
        stop_loss = (min(best_ltf.c1.low, best_ltf.c2.low, best_ltf.c3.low) if is_bullish
                     else max(best_ltf.c1.high, best_ltf.c2.high, best_ltf.c3.high))
        risk_r = abs(entry_price - stop_loss)
        if risk_r <= 0:
            fvg_ptr += 1
            continue

        # Fill simulation (S2 semantics: SL-before-entry without tag → void).
        entry_triggered = False
        fill_idx = None
        k = fvg_idx + 1
        while k < n_ltf:
            c_k = candles_ltf[k]
            if is_bullish:
                if c_k.low <= stop_loss and c_k.high < entry_price:
                    break
                if c_k.low <= entry_price:
                    entry_triggered = True
                    fill_idx = k
                    break
            else:
                if c_k.high >= stop_loss and c_k.low > entry_price:
                    break
                if c_k.high >= entry_price:
                    entry_triggered = True
                    fill_idx = k
                    break
            k += 1

        if not entry_triggered:
            fvg_ptr += 1
            continue

        fill_ts = candles_ltf[fill_idx].timestamp + ltf_duration_ms  # fill at candle close

        # Entry session + weekday + anchor age, all at the ACTUAL fill.
        # Shared phase-2 gate: a backtest can only decide these once it has
        # simulated the fill, which is why selection happens first here while
        # live defers the same gates to extreme_trade_tracker at fill time.
        anchor_age_h = (fill_ts - anchor.fvg.close_timestamp) / 3_600_000.0
        _fill_reject = evaluate_fill_gates(
            fill_ts,
            anchor,
            entry_session_config=SessionFilterConfig(
                entry_sessions=entry_sessions or "ALL",
                entry_weekdays_only=entry_weekday_only,
            ),
            anchor_age_dead_zone=DEAD_ZONE_DEFAULT if anchor_age_guard else None,
        )
        if _fill_reject is not None:
            gate_rejects[_fill_reject] += 1
            entered_fvg_timestamps.add(best_ltf.formed_at)
            fvg_ptr += 1
            continue

        # ---- Take-profit resolution ----
        # Delegates to the live engine's resolver so backtest and live agree on
        # `tp_mode` by construction. Calling `liquidity_take_profit()` directly
        # here silently ignored `tp_mode` (always liquidity-first).
        pools_at_fill = pools_asof(fill_ts)
        _tp = apply_liquidity_tp(
            pools=pools_at_fill,
            direction=best_ltf.direction,
            entry_price=entry_price,
            risk_r=risk_r,
            tp_mode=tp_mode,
            min_rr_for_liquidity=min_rr_for_liquidity,
            fallback_target_r=fallback_target_r,
            buffer_pct=DEFAULT_TP_BUFFER_PCT,
        )
        tp_price, tp_mode_used, tp_pool = _tp["tp_price"], _tp["tp_mode"], _tp["tp_pool"]

        # ---- Forward simulation to TP or SL (SL first on collision bar) ----
        hit = False
        hit_1r = False
        exit_ts = fill_ts
        exit_reason = "TIME_EXPIRED"
        max_fav = entry_price
        max_adv = entry_price
        tp_1r_price = entry_price + risk_r if is_bullish else entry_price - risk_r
        for c in candles_ltf[fill_idx:]:
            exit_ts = c.timestamp + ltf_duration_ms
            if is_bullish:
                max_fav = max(max_fav, c.high)
                max_adv = min(max_adv, c.low)
                if c.low <= stop_loss:
                    exit_reason = "STOPPED_OUT"
                    break
                if not hit_1r and c.high >= tp_1r_price:
                    hit_1r = True
                if c.high >= tp_price:
                    hit = True
                    exit_reason = "TP_LIQUIDITY" if tp_mode_used == "LIQUIDITY" else "TP_FIXED"
                    break
            else:
                max_fav = min(max_fav, c.low)
                max_adv = max(max_adv, c.high)
                if c.high >= stop_loss:
                    exit_reason = "STOPPED_OUT"
                    break
                if not hit_1r and c.low <= tp_1r_price:
                    hit_1r = True
                if c.low <= tp_price:
                    hit = True
                    exit_reason = "TP_LIQUIDITY" if tp_mode_used == "LIQUIDITY" else "TP_FIXED"
                    break

        realized = (abs(tp_price - entry_price) / risk_r) if hit else -1.0
        realized_1r = 1.0 if hit_1r else -1.0
        mfe_r = abs(max_fav - entry_price) / risk_r
        mae_r = abs(entry_price - max_adv) / risk_r
        duration_min = max(1, int((exit_ts - fill_ts) / 60000))

        trade = Strategy3Trade(
            symbol=symbol,
            direction=best_ltf.direction,
            entry_timestamp=fill_ts,
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk_r=risk_r,
            tp_1r=entry_price + risk_r if is_bullish else entry_price - risk_r,
            tp_2r=entry_price + 2 * risk_r if is_bullish else entry_price - 2 * risk_r,
            tp_3r=entry_price + 3 * risk_r if is_bullish else entry_price - 3 * risk_r,
            hit_1r=hit_1r,
            hit_2r=False,
            hit_3r=False,
            exit_timestamp=exit_ts,
            exit_reason=exit_reason,  # type: ignore[arg-type]
            realized_r_1r=realized_1r,
            realized_r_2r=-1.0,
            realized_r_3r=-1.0,
            mfe_r=mfe_r,
            mae_r=mae_r,
            duration_minutes=duration_min,
            ltf_fvg_bottom=best_ltf.bottom,
            ltf_fvg_top=best_ltf.top,
            htf_fvg_bottom=anchor.fvg.bottom,
            htf_fvg_top=anchor.fvg.top,
            fvg_formation_timestamp=best_ltf.close_timestamp,
            fvg_formed_at=best_ltf.formed_at,
            htf_formed_timestamp=anchor.fvg.close_timestamp,
            htf_first_touch_timestamp=anchor.first_touch_timestamp,
            htf_most_recent_touch_timestamp=anchor.most_recent_touch_timestamp,
            ltf_gap_pct=best_ltf.gap_pct,
            ltf_timeframe=ltf_timeframe,
            tp_mode=tp_mode_used,
            tp_pool_kind=tp_pool.kind if tp_pool else None,
            tp_pool_price=tp_pool.price if tp_pool else None,
            sweep_pool_kind=best_pool.kind if best_pool else None,
            sweep_pool_price=best_pool.price if best_pool else None,
            sweep_ts=best_sweep_ts,
            anchor_age_h_at_fill=anchor_age_h,
            realized_r=realized,
        )
        executed.append(trade)
        entered_fvg_timestamps.add(best_ltf.formed_at)

        bars_held = max(1, duration_min // (ltf_duration_ms // 60000))
        curr_sim_idx = max(fvg_idx + 1, fill_idx + bars_held)
        fvg_ptr += 1

    # ---- Tally ----
    total = len(executed)
    wins = sum(1 for t in executed if t.exit_reason.startswith("TP_"))
    losses = total - wins
    net_r = sum(t.realized_r for t in executed)
    gross_win = sum(t.realized_r for t in executed if t.realized_r > 0)
    gross_loss = sum(-t.realized_r for t in executed if t.realized_r < 0)
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    peak = curr_eq = max_dd = 0.0
    for t in executed:
        curr_eq += t.realized_r
        peak = max(peak, curr_eq)
        max_dd = max(max_dd, peak - curr_eq)

    return Strategy3BacktestReport(
        symbol=symbol, days=days, ltf_timeframe=ltf_timeframe,
        invalidation_mode="close" if use_close_invalidation else "wick",
        min_gap_pct=min_gap_pct, require_sweep=require_sweep, sweep_max_age_h=sweep_max_age_h,
        gap_band=band, anchor_age_guard=anchor_age_guard, tp_mode=tp_mode,
        total_trades=total, wins=wins, losses=losses,
        win_rate=(wins / total * 100) if total else 0.0,
        net_r=net_r, profit_factor=pf, max_drawdown_r=max_dd,
        avg_hold_min=(sum(t.duration_minutes for t in executed) / total) if total else 0.0,
        avg_mfe_r=(sum(t.mfe_r for t in executed) / total) if total else 0.0,
        liquidity_tp_count=sum(1 for t in executed if t.tp_mode == "LIQUIDITY"),
        fixed_tp_count=sum(1 for t in executed if t.tp_mode == "FIXED_R"),
        gate_rejects=dict(gate_rejects),
        trades=executed,
    )


def print_report(report: Strategy3BacktestReport) -> None:
    print("\n" + "=" * 80)
    print(f"  📊 STRATEGY 3 BACKTEST: {report.symbol} ({report.days}d, {report.ltf_timeframe}, {report.invalidation_mode.upper()})")
    print("=" * 80)
    print(f"  • Sweep gate:            {'ON (max age %.1fh)' % report.sweep_max_age_h if report.require_sweep else 'OFF'}")
    print(f"  • Gap band excluded:     {report.gap_band if report.gap_band else 'OFF'}")
    print(f"  • Anchor-age guard:      {'ON [24h,48h)' if report.anchor_age_guard else 'OFF'}")
    print(f"  • TP mode:               {report.tp_mode}")
    print(f"  • Total trades:          {report.total_trades}  (wins {report.wins} / losses {report.losses})")
    print(f"  • Win rate:              {report.win_rate:.1f}%")
    print(f"  • Net R:                 {report.net_r:+.1f}R")
    pf = "inf" if report.profit_factor == float("inf") else f"{report.profit_factor:.2f}"
    print(f"  • Profit factor:         {pf}")
    print(f"  • Max drawdown:          -{report.max_drawdown_r:.1f}R")
    print(f"  • Avg hold:              {report.avg_hold_min:.0f} min | Avg MFE {report.avg_mfe_r:+.2f}R")
    print(f"  • TP: {report.liquidity_tp_count} liquidity / {report.fixed_tp_count} fixed")
    print(f"  • Gate rejects:          {report.gate_rejects}")
    if report.trades:
        print("\n  📜 LAST 5 TRADES:")
        for i, t in enumerate(report.trades[-5:], 1):
            emoji = "✅" if t.realized_r > 0 else "❌"
            print(f"    {i}. {t.direction:7s} @ {t.entry_price:.4f} | {t.entry_time_ist} | {emoji} {t.realized_r:+.2f}R | {t.exit_reason} | TP {t.tp_mode}" + (f" ({t.tp_pool_kind})" if t.tp_pool_kind else ""))
    print("=" * 80 + "\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy 3 Liquidity-Sweep FVG backtester")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--ltf", default="5m", choices=["1m", "5m", "15m", "1h"])
    parser.add_argument("--invalidation", default="wick", choices=["wick", "close"])
    parser.add_argument("--min-gap-pct", type=float, default=0.05)
    parser.add_argument("--no-sweep-gate", action="store_true")
    parser.add_argument("--sweep-max-age-h", type=float, default=2.0)
    parser.add_argument("--gap-band-exclude", default="0.10,0.20")
    parser.add_argument("--no-anchor-age-guard", action="store_true")
    parser.add_argument("--tp-mode", default="LIQUIDITY", choices=["LIQUIDITY", "FIXED_R"])
    parser.add_argument("--min-rr-for-liquidity", type=float, default=1.5)
    parser.add_argument("--entry-sessions", default="NY_KZ")
    args = parser.parse_args()

    report = await run_liquidity_sweep_backtest(
        symbol=args.symbol.upper(), days=args.days, ltf_timeframe=args.ltf,
        use_close_invalidation=(args.invalidation == "close"), min_gap_pct=args.min_gap_pct,
        require_sweep=not args.no_sweep_gate, sweep_max_age_h=args.sweep_max_age_h,
        gap_band_exclude=args.gap_band_exclude, anchor_age_guard=not args.no_anchor_age_guard,
        tp_mode=args.tp_mode, min_rr_for_liquidity=args.min_rr_for_liquidity,
        entry_sessions=args.entry_sessions,
    )
    print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
