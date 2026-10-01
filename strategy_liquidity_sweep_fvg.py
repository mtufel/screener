"""
Strategy 3 — Liquidity-Sweep FVG (strategy_liquidity_sweep_fvg.py).

Implements the video model ("Every Trader Should Know This 4H FVG Strategy"):
4H FVG for bias → liquidity sweep → LTF FVG entry → target opposing liquidity.

Pipeline:
1. Reuse Strategy 2's proven 4H machinery (cache, touch anchors, extreme LTF
   selection, execution parameters) — no rewrite of the multi-timeframe core.
2. Gate candidate LTF FVGs with data-proven filters measured in 90-day
   backtests (2026-06→2026-09, BTC/ETH/SOL 5m CLOSE):
   - FRESH SWEEP: an opposing-side structural liquidity pool (equal highs/lows,
     PDH/PDL) must be swept within `sweep_max_age_h` before LTF FVG formation.
   - ANCHOR AGE GUARD: skip setups whose 4H anchor age at fill falls inside the
     measured dead zone [24h, 48h) (2R WR 20% BTC / 11.8% ETH there).
   - GAP BAND EXCLUSION: reject LTF FVGs whose gap% lies inside the losing band
     (default 0.10–0.20%).
   - ENTRY SESSION: entries only in the NY killzone (13:00–16:00 UTC), which
     carried effectively all measured profit.
3. LIQUIDITY-FIRST TP: when `tp_mode="LIQUIDITY"`, target the nearest opposing
   pool at least `min_rr_for_liquidity` R beyond entry (buffered 0.02% in front
   of the level); fall back to fixed `fallback_target_r` otherwise.

This module never mutates Strategy 2 state; it imports pure helpers only.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from liquidity import (
    Candle,
    LiquidityPool,
    anchor_age_guard_ok,
    detect_swing_points,
    find_liquidity_pools,
    has_fresh_sweep,
    liquidity_take_profit,
    parse_gap_band,
)
from session_filter import SessionFilterConfig
from strategy_extreme_fvg import (
    FVG,
    TouchedAnchor,
    TIMEFRAME_MS,
    build_extreme_trade_setup,
    find_unmitigated_ltf_fvgs,
    get_most_recent_touched_anchor_for_symbol,
)

logger = logging.getLogger(__name__)

# Default gates (overridable via params at runtime; env fallbacks for ops).
DEFAULT_SWEEP_MAX_AGE_H = float(os.getenv("EXTREME_SWEEP_MAX_AGE_H", "2"))
DEFAULT_ANCHOR_AGE_GUARD = os.getenv("EXTREME_ANCHOR_AGE_GUARD", "true").strip().lower() in ("true", "1", "yes")
DEFAULT_GAP_BAND = os.getenv("EXTREME_GAP_BAND_EXCLUDE", "0.10,0.20")
DEFAULT_TP_MODE = os.getenv("EXTREME_TP_MODE", "LIQUIDITY").strip().upper()
DEFAULT_MIN_RR_FOR_LIQUIDITY = float(os.getenv("EXTREME_MIN_RR_FOR_LIQUIDITY", "1.5"))
DEFAULT_FALLBACK_R = 2.0
DEFAULT_TP_BUFFER_PCT = float(os.getenv("EXTREME_TP_BUFFER_PCT", "0.02"))

DEAD_ZONE_DEFAULT = (24.0, 48.0)


@dataclass
class SweepGateResult:
    """Outcome of the sweep gate for one candidate FVG."""

    passed: bool
    reason: str  # "OK" | "NO_FRESH_SWEEP" | "NO_POOLS" (pools exist but none swept)
    pool: Optional[LiquidityPool] = None
    sweep_ts: Optional[int] = None


def check_anchor_age(anchor: TouchedAnchor, fill_ts_ms: int, dead_zone: Tuple[float, float] = DEAD_ZONE_DEFAULT) -> bool:
    """Anchor-age gate: False inside the measured dead zone [lo, hi) hours."""
    age_h = (fill_ts_ms - anchor.fvg.close_timestamp) / 3_600_000.0
    return anchor_age_guard_ok(age_h, dead_zone)


def check_gap_band(fvg: FVG, band: Optional[Tuple[float, float]]) -> bool:
    """Gap-band gate: True when FVG gap% is OUTSIDE the excluded band."""
    if band is None:
        return True
    lo, hi = band
    return not (lo <= fvg.gap_pct < hi)


def check_fresh_sweep(
    candles_ltf: List[Candle],
    direction: str,
    from_ts_ms: int,
    fvg_formed_close_ts_ms: int,
    sweep_max_age_ms: float,
    tol_pct: float = 0.05,
    swings: Optional[List[Any]] = None,
    timestamps: Optional[List[int]] = None,
) -> SweepGateResult:
    """
    Fresh-sweep gate per the video's 'sweep → FVG → entry' model.

    Builds the liquidity map as of the FVG formation close (no lookahead:
    swings are filtered to those confirmable by that timestamp), then requires
    an opposing-side pool swept within `sweep_max_age_ms` before formation.
    """
    pools = find_liquidity_pools(
        candles_ltf,
        timeframe="5m",
        tol_pct=tol_pct,
        now_ms=fvg_formed_close_ts_ms,
        swings=swings,
    )
    if not pools:
        return SweepGateResult(passed=False, reason="NO_FRESH_SWEEP")
    swept, pool, sweep_ts = has_fresh_sweep(
        candles_ltf,
        pools,
        direction=direction,
        from_ts=from_ts_ms,
        to_ts=fvg_formed_close_ts_ms,
        max_age_ms=sweep_max_age_ms,
        timestamps=timestamps,
    )
    if not swept:
        return SweepGateResult(passed=False, reason="NO_FRESH_SWEEP")
    return SweepGateResult(passed=True, reason="OK", pool=pool, sweep_ts=sweep_ts)


def select_gated_ltf_fvg(
    candles_ltf: List[Candle],
    anchor: TouchedAnchor,
    current_price: float,
    ltf_timeframe: str = "5m",
    min_gap_pct: float = 0.05,
    completion_target: str = "2R",
    require_sweep: bool = True,
    sweep_max_age_h: float = DEFAULT_SWEEP_MAX_AGE_H,
    gap_band: Optional[Tuple[float, float]] = None,
    entry_session_config: Optional[SessionFilterConfig] = None,
    anchor_age_dead_zone: Tuple[float, float] = DEAD_ZONE_DEFAULT,
    fill_probe_ts_ms: Optional[int] = None,
    swings: Optional[List[Any]] = None,
    timestamps: Optional[List[int]] = None,
) -> Tuple[Optional[FVG], List[FVG], Dict[str, int]]:
    """
    Gated version of S2's discovery + extreme selection.

    Scans unmitigated LTF FVGs post-touch (S2 engine call), then rejects
    candidates that fail: gap band, entry-session, fresh-sweep, or (when the
    candidate is already TRADE_ACTIVE) the anchor-age guard evaluated at its
    actual fill timestamp. Selection keeps S2's extreme rule (deepest for
    bullish, highest for bearish) among survivors.

    The entry-session gate constrains the FILL, not the FVG formation: a
    PENDING_RETRACE candidate has not filled yet, so the session is not yet
    decidable here and the gate is skipped for it — `extreme_trade_tracker`
    enforces the window at fill, ignoring out-of-session touches rather than
    deferring them. Pass `fill_probe_ts_ms` to decide pending candidates
    yourself (backtests/replays that already know the fill time).

    `fill_probe_ts_ms` also overrides the anchor-age fill probe (backtests pass the
    simulated fill time; live uses now).

    Returns (selected_fvg_or_None, survivors, gate_reject_counts).
    """
    unmitigated = find_unmitigated_ltf_fvgs(
        candles_ltf=candles_ltf,
        after_timestamp=anchor.first_touch_timestamp,
        direction=anchor.fvg.direction,
        current_price=current_price,
        ltf_timeframe=ltf_timeframe,
        min_gap_pct=min_gap_pct,
        completion_target=completion_target,
    )
    rejects = {"GAP_BAND": 0, "ENTRY_SESSION": 0, "NO_FRESH_SWEEP": 0, "ANCHOR_AGE": 0}
    if not unmitigated:
        return (None, [], rejects)

    # One swing scan over the series, reused for every as-of pool build.
    if swings is None:
        swings = detect_swing_points(candles_ltf)
    sweep_max_age_ms = sweep_max_age_h * 3_600_000.0

    survivors: List[FVG] = []
    for fvg in unmitigated:
        formed_close_ts = fvg.close_timestamp

        if not check_gap_band(fvg, gap_band):
            rejects["GAP_BAND"] += 1
            continue

        # Entry-session gate. The session constrains the FILL, not the formation:
        # the source is explicit that the FVG may form outside the window and only
        # the entry has to land inside it. A TRADE_ACTIVE candidate already carries
        # its real fill timestamp, so probe that. A PENDING_RETRACE one has not
        # filled yet, so the session is not yet decidable — skip the gate and let
        # `extreme_trade_tracker` enforce it at fill (out-of-session touches are
        # ignored there, not deferred). `fill_probe_ts_ms` lets a caller that already
        # knows the fill time decide it here instead.
        if entry_session_config is not None:
            if fvg.lifecycle_state == "TRADE_ACTIVE":
                session_probe_ts = fvg.entry_timestamp or fill_probe_ts_ms
            else:
                session_probe_ts = fill_probe_ts_ms
            if session_probe_ts is not None and not entry_session_config.is_entry_valid(session_probe_ts):
                rejects["ENTRY_SESSION"] += 1
                continue

        if require_sweep:
            gate = check_fresh_sweep(
                candles_ltf=candles_ltf,
                direction=anchor.fvg.direction,
                from_ts_ms=anchor.first_touch_timestamp,
                fvg_formed_close_ts_ms=formed_close_ts,
                sweep_max_age_ms=sweep_max_age_ms,
                swings=swings,
                timestamps=timestamps,
            )
            if not gate.passed:
                rejects["NO_FRESH_SWEEP"] += 1
                continue

        # Anchor-age guard: exact fill timestamp when already active, else the
        # formation close as the earliest possible fill (probe).
        if fvg.lifecycle_state == "TRADE_ACTIVE":
            probe_ts = fvg.entry_timestamp or formed_close_ts
        else:
            probe_ts = fill_probe_ts_ms or formed_close_ts
        if not check_anchor_age(anchor, probe_ts, anchor_age_dead_zone):
            rejects["ANCHOR_AGE"] += 1
            continue

        survivors.append(fvg)

    if not survivors:
        return (None, [], rejects)

    if anchor.fvg.direction == "Bullish":
        best = min(survivors, key=lambda f: (f.bottom, f.formed_at))
    else:
        best = max(survivors, key=lambda f: (f.top, -f.formed_at))
    return (best, survivors, rejects)


def apply_liquidity_tp(
    pools: List[LiquidityPool],
    direction: str,
    entry_price: float,
    risk_r: float,
    tp_mode: str = DEFAULT_TP_MODE,
    min_rr_for_liquidity: float = DEFAULT_MIN_RR_FOR_LIQUIDITY,
    fallback_target_r: float = DEFAULT_FALLBACK_R,
    buffer_pct: float = DEFAULT_TP_BUFFER_PCT,
) -> Dict[str, Any]:
    """
    Resolves the take-profit per tp_mode and returns a dict with
    tp_price / tp_mode / tp_pool (None for FIXED_R) for setup enrichment.
    """
    if tp_mode == "LIQUIDITY":
        tp_price, mode, pool = liquidity_take_profit(
            pools=pools,
            direction=direction,
            entry_price=entry_price,
            risk_r=risk_r,
            min_rr=min_rr_for_liquidity,
            fallback_r=fallback_target_r,
            buffer_pct=buffer_pct,
        )
        return {"tp_price": tp_price, "tp_mode": mode, "tp_pool": pool}
    tp_price = entry_price + fallback_target_r * risk_r if direction == "Bullish" else entry_price - fallback_target_r * risk_r
    return {"tp_price": tp_price, "tp_mode": "FIXED_R", "tp_pool": None}


async def get_liquidity_sweep_setup_for_symbol(
    symbol: str,
    ltf_timeframe: str = "5m",
    client: Optional[Any] = None,
    use_close_invalidation: bool = False,
    min_gap_pct: float = 0.05,
    completion_target: str = "2R",
    require_sweep: bool = True,
    sweep_max_age_h: float = DEFAULT_SWEEP_MAX_AGE_H,
    gap_band_exclude: Optional[str] = DEFAULT_GAP_BAND,
    anchor_age_guard: bool = DEFAULT_ANCHOR_AGE_GUARD,
    tp_mode: str = DEFAULT_TP_MODE,
    min_rr_for_liquidity: float = DEFAULT_MIN_RR_FOR_LIQUIDITY,
    fallback_target_r: float = DEFAULT_FALLBACK_R,
    session_config: Optional[SessionFilterConfig] = None,
    candles_4h: Optional[List[Candle]] = None,
    candles_ltf: Optional[List[Candle]] = None,
) -> Optional[Dict[str, Any]]:
    """
    End-to-end Strategy 3 setup for one symbol. Returns a dict payload (not an
    ExtremeTradeSetup) so the setup can carry tp_mode/tp_pool/sweep metadata
    without touching S2 dataclasses:

        {
          "symbol", "direction", "anchor", "ltf_fvg", "entry_price",
          "stop_loss", "risk_r", "tp_price", "tp_mode", "tp_pool",
          "sweep_pool", "sweep_ts", "targets": {"1R","2R","3R"},
          "gate_rejects": {...}, "survivor_count": int,
        }
    """
    cli = client
    if candles_ltf is None:
        if cli is None:
            from market_data_provider import market_data_provider as cli_default
            cli = cli_default
        raw_ltf = await cli.get_last_n_candles(symbol=symbol, timeframe=ltf_timeframe, n=300)
        if not raw_ltf:
            return None
        candles_ltf = [Candle.from_dict(c) if isinstance(c, dict) else c for c in raw_ltf]
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
    if isinstance(gap_band_exclude, tuple):
        band = gap_band_exclude
    else:
        band = parse_gap_band(gap_band_exclude) if gap_band_exclude else None
    dead_zone = DEAD_ZONE_DEFAULT if anchor_age_guard else (-1.0, -1.0)  # (-1,-1) never blocks
    swings = detect_swing_points(candles_ltf)  # one scan, reused below
    timestamps = [c.timestamp for c in candles_ltf]

    best, survivors, rejects = select_gated_ltf_fvg(
        candles_ltf=candles_ltf,
        anchor=anchor,
        current_price=current_price,
        ltf_timeframe=ltf_timeframe,
        min_gap_pct=min_gap_pct,
        completion_target=completion_target,
        require_sweep=require_sweep,
        sweep_max_age_h=sweep_max_age_h,
        gap_band=band,
        entry_session_config=session_config,
        anchor_age_dead_zone=dead_zone,
        swings=swings,
        timestamps=timestamps,
    )
    if best is None:
        logger.debug(
            "[Strategy3] [%s] gated out: rejects=%s (anchor %s [%.4f-%.4f])",
            symbol, rejects, anchor.fvg.direction, anchor.fvg.bottom, anchor.fvg.top,
        )
        return None

    direction = anchor.fvg.direction
    c1, c2, c3 = best.c1, best.c2, best.c3
    if direction == "Bullish":
        entry_price = best.top
        stop_loss = min(c1.low, c2.low, c3.low)
    else:
        entry_price = best.bottom
        stop_loss = max(c1.high, c2.high, c3.high)
    risk_r = abs(entry_price - stop_loss)
    if risk_r <= 0:
        return None

    # Liquidity map as of NOW for TP selection (pools ahead of price).
    pools_now = find_liquidity_pools(candles_ltf, timeframe=ltf_timeframe)
    tp = apply_liquidity_tp(
        pools=pools_now,
        direction=direction,
        entry_price=entry_price,
        risk_r=risk_r,
        tp_mode=tp_mode,
        min_rr_for_liquidity=min_rr_for_liquidity,
        fallback_target_r=fallback_target_r,
    )
    tp_price = tp["tp_price"]

    # Sweep metadata for the selected candidate (reuses the swing scan).
    sweep_pool = None
    sweep_ts = None
    if require_sweep:
        gate = check_fresh_sweep(
            candles_ltf=candles_ltf,
            direction=direction,
            from_ts_ms=anchor.first_touch_timestamp,
            fvg_formed_close_ts_ms=best.close_timestamp,
            sweep_max_age_ms=sweep_max_age_h * 3_600_000.0,
            swings=swings,
            timestamps=timestamps,
        )
        sweep_pool, sweep_ts = gate.pool, gate.sweep_ts

    targets = {
        "1R": entry_price + risk_r if direction == "Bullish" else entry_price - risk_r,
        "2R": entry_price + 2 * risk_r if direction == "Bullish" else entry_price - 2 * risk_r,
        "3R": entry_price + 3 * risk_r if direction == "Bullish" else entry_price - 3 * risk_r,
    }

    logger.info(
        "[Strategy3] [%s] SETUP %s | entry %.6f | SL %.6f | TP %.6f (%s) | risk %.3f%% | sweep %s | rejects %s",
        symbol, direction, entry_price, stop_loss, tp_price, tp["tp_mode"],
        risk_r / entry_price * 100.0,
        f"{sweep_pool.kind if sweep_pool else '-'}@{sweep_pool.price if sweep_pool else '-'}",
        rejects,
    )
    return {
        "symbol": symbol,
        "direction": direction,
        "anchor": anchor,
        "ltf_fvg": best,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "risk_r": risk_r,
        "tp_price": tp_price,
        "tp_mode": tp["tp_mode"],
        "tp_pool": tp["tp_pool"],
        "sweep_pool": sweep_pool,
        "sweep_ts": sweep_ts,
        "targets": targets,
        "gate_rejects": rejects,
        "survivor_count": len(survivors),
        "ltf_timeframe": ltf_timeframe,
    }
