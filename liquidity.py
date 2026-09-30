"""
Structural Liquidity Detection & Sweep Analysis (liquidity.py).

Pure-function module in the style of session_filter.py. Provides:
1. Fractal swing-point detection on closed candles.
2. Structural liquidity pools: equal highs/lows (clustered swing extremes),
   prior-day high/low (PDH/PDL), and session extremes.
3. Stop-hunt sweep detection: wick traded through a pool level with a close
   back on the original side.
4. Freshness queries used by Strategy 3 to gate LTF FVG setups on a recent
   opposing-side liquidity sweep.

All functions take timestamps in milliseconds and are deterministic — no
network, no globals, no lookahead: every detection uses only candles at or
before the evaluation timestamp.
"""

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Literal, Optional, Tuple

try:
    from strategy_extreme_fvg import Candle, TIMEFRAME_MS
except ImportError:  # pragma: no cover - allows standalone import in isolation
    from dataclasses import dataclass as _dc

    @_dc
    class Candle:  # type: ignore[no-redef]
        timestamp: int
        open: float
        high: float
        low: float
        close: float
        volume: float

    TIMEFRAME_MS = {}

# Fractal half-window: a bar is a swing high if its high is the strict maximum
# of the surrounding k bars on each side (k=2 → classic 5-bar fractal).
SWING_K = 2

# Default clustering tolerance as a fraction of price (0.05% of mid).
DEFAULT_CLUSTER_TOL_PCT = 0.05

# Number of touches required to classify a clustered level as an equal
# highs/lows pool (>= 2). Single-touch levels become MINOR pools.
EQUAL_TOUCHES_REQUIRED = 2

# Fraction of a pool's level a wick must trade through to count as swept.
SWEEP_PENETRATION_FRAC = 0.10

PoolKind = Literal["EQUAL_HIGHS", "EQUAL_LOWS", "PDH", "PDL", "SESSION_HIGH", "SESSION_LOW", "MINOR"]
PoolSide = Literal["ABOVE", "BELOW"]


@dataclass
class LiquidityPool:
    """A structural price level where resting stops/liquidity is assumed."""

    kind: PoolKind
    price: float
    side: PoolSide  # ABOVE = resting stops above price (shorts' stops), BELOW = below
    formed_at: int  # ms timestamp when the level was first established
    touches: int = 1
    strength: float = 1.0  # touches + recency weighting (informational)
    swept_at: Optional[int] = None  # ms timestamp of the sweep, once detected

    def to_dict(self) -> Dict:
        return {
            "kind": self.kind,
            "price": round(self.price, 8),
            "side": self.side,
            "formed_at": self.formed_at,
            "touches": self.touches,
            "strength": round(self.strength, 3),
            "swept_at": self.swept_at,
        }


@dataclass
class SwingPoint:
    """A confirmed fractal swing extreme. Confirmed only after k later bars."""

    index: int
    timestamp: int
    price: float
    kind: Literal["HIGH", "LOW"]


# ---------------------------------------------------------------------------
# Swing detection
# ---------------------------------------------------------------------------

def detect_swing_points(candles: List[Candle], k: int = SWING_K) -> List[SwingPoint]:
    """
    Returns fractal swing highs/lows confirmable within `candles`.

    A bar i is a swing high when its high is strictly above the k bars to its
    left and at-or-above the k bars to its right (ties allowed on the right so
    a plateau's first bar anchors the level). Mirrored for swing lows.
    Confirmation requires candle i+k to exist — no lookahead: callers passing a
    candle list truncated at time T only ever see swings confirmed by T.
    """
    out: List[SwingPoint] = []
    n = len(candles)
    if n < 2 * k + 1:
        return out
    for i in range(k, n - k):
        c = candles[i]
        left = candles[i - k:i]
        right = candles[i + 1:i + k + 1]
        if c.high > max(x.high for x in left) and c.high >= max(x.high for x in right):
            out.append(SwingPoint(index=i, timestamp=c.timestamp, price=c.high, kind="HIGH"))
        if c.low < min(x.low for x in left) and c.low <= min(x.low for x in right):
            out.append(SwingPoint(index=i, timestamp=c.timestamp, price=c.low, kind="LOW"))
    return out


# ---------------------------------------------------------------------------
# Pool construction
# ---------------------------------------------------------------------------

def _cluster_levels(
    levels: List[Tuple[float, int]],
    tol_abs: float,
) -> List[Tuple[float, int]]:
    """
    Clusters levels into (mean_price, total_touches) groups using leader-based
    clustering: sorted by price, each level joins the current group while it is
    within tol_abs of the group's FIRST (leader) price. This bounds every
    cluster's spread to tol_abs — chained clustering would let long dense
    regions merge into one group whose mean drifts far from any real level.
    Levels input: [(price, weight), ...].
    """
    if not levels:
        return []
    levels = sorted(levels, key=lambda x: x[0])
    clusters: List[List[Tuple[float, int]]] = []
    leader: Optional[float] = None
    for price, _w in levels:
        if leader is None or price - leader > tol_abs:
            clusters.append([(price, _w)])
            leader = price
        else:
            clusters[-1].append((price, _w))
    out: List[Tuple[float, int]] = []
    for group in clusters:
        total_w = sum(w for _p, w in group)
        mean_p = sum(p * w for p, w in group) / total_w if total_w > 0 else sum(p for p, _ in group) / len(group)
        out.append((mean_p, total_w))
    return out


def _tolerance(mid_price: float, tol_pct: float) -> float:
    return max(mid_price * tol_pct / 100.0, 1e-12)


def _strength(touches: int, age_ms: int, recency_half_life_ms: int = 7 * 24 * 3600 * 1000) -> float:
    """Informational strength: touches decayed by age (half-life 7d)."""
    decay = 0.5 ** (age_ms / recency_half_life_ms) if age_ms > 0 else 1.0
    return (1.0 + touches) * decay


def find_liquidity_pools(
    candles: List[Candle],
    timeframe: str = "5m",
    tol_pct: float = DEFAULT_CLUSTER_TOL_PCT,
    now_ms: Optional[int] = None,
    include_minor: bool = True,
    swings: Optional[List[SwingPoint]] = None,
) -> List[LiquidityPool]:
    """
    Builds the structural liquidity map from closed candles as of `now_ms`.

    Thin wrapper over build_pool_templates + pools_from_templates so live and
    backtest share ONE pool-construction code path (identical by construction).

    Pools:
    - EQUAL_HIGHS / EQUAL_LOWS: clustered swing extremes with >= 2 touches.
    - MINOR: single-touch swing extremes (included only when include_minor).
    - PDH / PDL: prior UTC day high/low.

    A k-fractal swing only contributes once its confirmation bar (k bars later)
    has opened — no lookahead. `swings` may pass precomputed full-series swing
    points for reuse across many as-of evaluations.
    """
    if not candles:
        return []
    now = now_ms if now_ms is not None else candles[-1].timestamp
    visible = [c for c in candles if c.timestamp <= now]
    templates = build_pool_templates(visible, timeframe=timeframe, tol_pct=tol_pct, swings=swings)
    return pools_from_templates(templates, now, include_minor=include_minor)


def pools_for_direction(pools: List[LiquidityPool], direction: str) -> List[LiquidityPool]:
    """Pools that represent opposing liquidity for a trade direction.

    Bullish setups seek liquidity BELOW (sell-side pools to be swept before
    entry); bearish setups seek liquidity ABOVE (buy-side pools).
    """
    want = "BELOW" if direction == "Bullish" else "ABOVE"
    return [p for p in pools if p.side == want]


# ---------------------------------------------------------------------------
# Precomputed pool templates (fast as-of reconstruction for backtests)
# ---------------------------------------------------------------------------

@dataclass
class PoolTemplates:
    """Full-series pool structure for O(#pools) as-of reconstruction.

    Swing clusters are computed once over the whole series; an as-of view then
    counts only members whose confirmation bar (ts + k*duration) has opened by
    `now`. Clustering is therefore replayed exactly for levels separated by
    more than the tolerance; chained levels within tolerance of each other may
    classify MINOR-vs-EQUAL slightly differently early on (documented
    approximation; mean shifts are < tolerance).
    """

    timeframe: str
    tolerance: float
    clusters: List[Dict[str, Any]]  # {side, members: [(ready_ts, price)]}
    daily: List[Dict[str, Any]]     # sorted by day_start: {start, end, high, low, high_ts, low_ts}


def build_pool_templates(
    candles: List[Candle],
    timeframe: str = "5m",
    tol_pct: float = DEFAULT_CLUSTER_TOL_PCT,
    swings: Optional[List[SwingPoint]] = None,
) -> PoolTemplates:
    """Precomputes swing clusters and daily extremes once for a full series."""
    duration = TIMEFRAME_MS.get(timeframe, 5 * 60 * 1000)
    if not candles:
        return PoolTemplates(timeframe=timeframe, tolerance=1e-12, clusters=[], daily=[])
    mid = sum(c.close for c in candles[-50:]) / len(candles[-50:])
    tol = _tolerance(mid, tol_pct)
    if swings is None:
        swings = detect_swing_points(candles)

    highs = [(s.timestamp + duration * SWING_K, s.price, s.timestamp) for s in swings if s.kind == "HIGH"]
    lows = [(s.timestamp + duration * SWING_K, s.price, s.timestamp) for s in swings if s.kind == "LOW"]

    def cluster(ready_levels: List[Tuple[int, float, int]], side: PoolSide) -> List[Dict[str, Any]]:
        levels_sorted = sorted(ready_levels, key=lambda x: x[1])
        out: List[Dict[str, Any]] = []
        group: List[Tuple[int, float, int]] = []
        leader: Optional[float] = None
        for ready_ts, price, swing_ts in levels_sorted:
            if leader is None or price - leader > tol:
                if group:
                    out.append({"side": side, "members": group})
                group = []
                leader = price
            group.append((ready_ts, price, swing_ts))
        if group:
            out.append({"side": side, "members": group})
        return out

    clusters = cluster(highs, "ABOVE") + cluster(lows, "BELOW")

    daily_map: Dict[str, Dict[str, Any]] = {}
    for c in candles:
        day = datetime.fromtimestamp(c.timestamp / 1000, tz=timezone.utc).date()
        key = day.isoformat()
        d = daily_map.setdefault(key, {
            "start": int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000),
            "end": int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000) + 86_400_000,
            "high": c.high, "high_ts": c.timestamp, "low": c.low, "low_ts": c.timestamp,
        })
        if c.high > d["high"]:
            d["high"] = c.high
            d["high_ts"] = c.timestamp
        if c.low < d["low"]:
            d["low"] = c.low
            d["low_ts"] = c.timestamp
    daily = [daily_map[k] for k in sorted(daily_map.keys())]
    return PoolTemplates(timeframe=timeframe, tolerance=tol, clusters=clusters, daily=daily)


def pools_from_templates(templates: PoolTemplates, now_ms: int, include_minor: bool = True) -> List[LiquidityPool]:
    """Reconstructs the liquidity map as of `now_ms` from precomputed templates."""
    pools: List[LiquidityPool] = []
    for cl in templates.clusters:
        ready = [(price, swing_ts) for _rts, price, swing_ts in cl["members"] if _rts <= now_ms]
        if not ready:
            continue
        touches = len(ready)
        mean_p = sum(pr for pr, _st in ready) / touches
        kind: PoolKind = "EQUAL_HIGHS" if (cl["side"] == "ABOVE" and touches >= EQUAL_TOUCHES_REQUIRED) else (
            "EQUAL_LOWS" if touches >= EQUAL_TOUCHES_REQUIRED else "MINOR"
        )
        if kind == "MINOR" and not include_minor:
            continue
        formed = min(st for _p, st in ready)
        pools.append(LiquidityPool(kind=kind, price=mean_p, side=cl["side"], formed_at=formed,
                                   touches=touches, strength=_strength(touches, max(0, now_ms - formed))))

    day_list = templates.daily
    for i, d in enumerate(day_list):
        if d["start"] < now_ms <= d["end"] and i >= 1:
            prev = day_list[i - 1]
            pools.append(LiquidityPool(kind="PDH", price=prev["high"], side="ABOVE", formed_at=prev["high_ts"],
                                       touches=1, strength=_strength(1, max(0, now_ms - prev["high_ts"]))))
            pools.append(LiquidityPool(kind="PDL", price=prev["low"], side="BELOW", formed_at=prev["low_ts"],
                                       touches=1, strength=_strength(1, max(0, now_ms - prev["low_ts"]))))
            break
    return pools


# ---------------------------------------------------------------------------
# Sweep detection
# ---------------------------------------------------------------------------

def detect_sweep(candle: Candle, pool: LiquidityPool, penetration_frac: float = SWEEP_PENETRATION_FRAC) -> bool:
    """
    True when `candle` sweeps `pool`: wick trades through the level by at least
    `penetration_frac` of the pool-to-close distance proxy, and the candle
    CLOSES back on the pool's original side (stop-hunt signature).

    For an ABOVE pool: candle.high > pool.price AND candle.close < pool.price.
    For a BELOW pool: candle.low < pool.price AND candle.close > pool.price.

    `penetration_frac` (percent of pool price) is a noise floor so microscopic
    float-level grazes on low-priced symbols do not register as sweeps.
    """
    if pool.side == "ABOVE":
        if candle.high <= pool.price or candle.close >= pool.price:
            return False
        reach = candle.high - pool.price
    else:
        if candle.low >= pool.price or candle.close <= pool.price:
            return False
        reach = pool.price - candle.low
    noise_floor = pool.price * (penetration_frac / 100.0) * 0.01
    return reach > noise_floor


def find_pool_sweep(candles: List[Candle], pool: LiquidityPool, from_ts: int = 0, to_ts: Optional[int] = None) -> Optional[int]:
    """
    Returns the ms timestamp of the FIRST candle in [from_ts, to_ts) that sweeps
    `pool`, or None. Only candles with timestamp >= pool.formed_at can sweep it.
    """
    if to_ts is None:
        to_ts = candles[-1].timestamp + 1 if candles else 0
    for c in candles:
        if c.timestamp < from_ts or c.timestamp >= to_ts:
            continue
        if c.timestamp < pool.formed_at:
            continue
        if detect_sweep(c, pool):
            return c.timestamp
    return None


def has_fresh_sweep(
    candles: List[Candle],
    pools: List[LiquidityPool],
    direction: str,
    from_ts: int,
    to_ts: int,
    max_age_ms: int = 2 * 3600 * 1000,
    now_ms: Optional[int] = None,
    timestamps: Optional[List[int]] = None,
) -> Tuple[bool, Optional[LiquidityPool], Optional[int]]:
    """
    True when an opposing-side pool is freshly swept in the window ending at
    `to_ts` (a candle starting in [to_ts - max_age_ms, to_ts) that began after
    `from_ts`), considering only pools formed before `to_ts`.

    Uses candle OPEN timestamps; a sweep candle must have started within
    [max(to_ts - max_age_ms, from_ts), to_ts).

    `timestamps` (open timestamps parallel to `candles`) enables bisect slicing
    instead of a full-list scan — pass it from hot loops.

    Returns (swept, pool, sweep_timestamp).
    """
    window_start = max(from_ts, to_ts - max_age_ms)
    opposing = pools_for_direction(pools, direction)
    if not opposing:
        return (False, None, None)
    if timestamps is not None:
        i0 = bisect.bisect_left(timestamps, window_start)
        i1 = bisect.bisect_left(timestamps, to_ts)
        window = candles[i0:i1]
    else:
        window = [c for c in candles if window_start <= c.timestamp < to_ts]
    best: Optional[Tuple[int, LiquidityPool, int]] = None
    for c in window:
        for pool in opposing:
            if pool.formed_at >= to_ts or pool.formed_at > c.timestamp:
                continue
            if detect_sweep(c, pool):
                # Prefer the most recent sweep, then strongest pool.
                key = (c.timestamp, pool.strength)
                if best is None or key > (best[2], best[1].strength):
                    best = (c.timestamp, pool, c.timestamp)
    if best is None:
        return (False, None, None)
    _ts, pool, sweep_ts = best
    return (True, pool, sweep_ts)


# ---------------------------------------------------------------------------
# Liquidity-first take-profit
# ---------------------------------------------------------------------------

def nearest_opposing_pool(
    pools: List[LiquidityPool],
    direction: str,
    entry_price: float,
    min_distance: float = 0.0,
    exclude_swept: bool = True,
) -> Optional[LiquidityPool]:
    """
    Nearest unswept pool on the profit side of entry at least `min_distance`
    away (absolute price distance). Bullish → pools ABOVE entry; bearish →
    pools BELOW entry.
    """
    side = "ABOVE" if direction == "Bullish" else "BELOW"
    candidates: List[Tuple[float, float, LiquidityPool]] = []
    for p in pools:
        if p.side != side:
            continue
        if exclude_swept and p.swept_at is not None:
            continue
        dist = (p.price - entry_price) if side == "ABOVE" else (entry_price - p.price)
        if dist >= min_distance and dist > 0:
            candidates.append((dist, -p.strength, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]


def liquidity_take_profit(
    pools: List[LiquidityPool],
    direction: str,
    entry_price: float,
    risk_r: float,
    min_rr: float = 1.5,
    fallback_r: float = 2.0,
    buffer_pct: float = 0.02,
) -> Tuple[float, str, Optional[LiquidityPool]]:
    """
    Liquidity-first TP resolution (the video's 'target opposing liquidity').

    - Finds the nearest opposing pool at least min_rr * risk_r beyond entry.
    - If found: TP = pool price pulled `buffer_pct`% toward entry (front-run of
      the level so the limit can actually fill).
    - Else: fixed fallback_r target.

    Returns (tp_price, mode, pool) where mode is "LIQUIDITY" or "FIXED_R".
    """
    pool = nearest_opposing_pool(pools, direction, entry_price, min_distance=min_rr * risk_r)
    if pool is None:
        tp = entry_price + fallback_r * risk_r if direction == "Bullish" else entry_price - fallback_r * risk_r
        return (tp, "FIXED_R", None)
    if direction == "Bullish":
        tp = pool.price - abs(pool.price) * buffer_pct / 100.0
        tp = min(tp, pool.price)
    else:
        tp = pool.price + abs(pool.price) * buffer_pct / 100.0
        tp = max(tp, pool.price)
    return (tp, "LIQUIDITY", pool)


def parse_gap_band(spec: Optional[str]) -> Optional[Tuple[float, float]]:
    """Parses '0.10,0.20' → (0.10, 0.20); None/empty → None; invalid → None."""
    if not spec or not spec.strip():
        return None
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 2:
        return None
    try:
        lo, hi = float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None
    if lo < 0 or hi <= lo or math.isnan(lo) or math.isnan(hi) or math.isinf(lo) or math.isinf(hi):
        return None
    return (lo, hi)


def anchor_age_guard_ok(anchor_age_hours: float, dead_zone: Tuple[float, float] = (24.0, 48.0)) -> bool:
    """False when anchor age sits inside the measured dead zone [lo, hi)."""
    lo, hi = dead_zone
    return not (lo <= anchor_age_hours < hi)


__all__ = [
    "Candle",
    "LiquidityPool",
    "SwingPoint",
    "SWING_K",
    "DEFAULT_CLUSTER_TOL_PCT",
    "EQUAL_TOUCHES_REQUIRED",
    "SWEEP_PENETRATION_FRAC",
    "detect_swing_points",
    "find_liquidity_pools",
    "pools_for_direction",
    "detect_sweep",
    "find_pool_sweep",
    "has_fresh_sweep",
    "nearest_opposing_pool",
    "liquidity_take_profit",
    "build_pool_templates",
    "pools_from_templates",
    "parse_gap_band",
    "anchor_age_guard_ok",
]
