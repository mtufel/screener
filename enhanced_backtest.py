"""
Enhanced Backtest — Strategy 2 Extreme FVG: Bias & Parameter Research.

Approach:
  Phase A — Base simulation (close invalidation, min_gap 0.05, 2R) runs ONCE and
            records every executed trade together with its feature vector
            (LTF FVG age, distance from 4H zone, gap%, 4H FVG age, momentum,
            session). Selection mirrors the deployed engine (deepest extreme FVG).
  Phase B — Marginal analysis: slice the executed-trade set by each feature and
            report win rate / net R / profit factor per bin. This reveals which
            values carry a real edge (FVG age decay? distance? gap size?).
  Phase C — Confirmation: re-run the simulation with the top filter combo to
            verify aggregate improvement over the baseline.

Run:  python3 enhanced_backtest.py [--symbol BTC] [--days 60] [--ltf 5m]
Outputs: enhanced_backtest_results.json, enhanced_backtest_findings.md
"""

import argparse
import asyncio
import bisect
import datetime as _dt
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

os.environ.setdefault("DATA_PROVIDER", "binance")
load_dotenv()

from market_data_provider import get_market_data_provider
from strategy_extreme_fvg import (
    Candle,
    FVG,
    compute_all_active_4h_fvgs,
    TIMEFRAME_MS,
    HTF_CANDLE_DURATION_MS,
)

# ------------------------------------------------------------------------------
# Data
# ------------------------------------------------------------------------------

async def fetch_candles(prov, symbol, tf, start_ms, end_ms):
    raw = await prov.get_historical_candles_range(symbol, tf, start_ms, end_ms)
    return [Candle.from_dict(c) for c in sorted(raw, key=lambda x: x.get("t", 0))]


def _ny_session(ts_ms: int) -> bool:
    dt = _dt.datetime.fromtimestamp(ts_ms / 1000.0, tz=_dt.timezone.utc)
    hour = dt.hour + dt.minute / 60.0
    return 13.0 <= hour < 22.0


def build_ltf_fvgs(candles, ltf_tf, min_gap):
    out = []
    for idx in range(len(candles) - 2):
        c1, c2, c3 = candles[idx], candles[idx + 1], candles[idx + 2]
        if c3.low > c1.high:
            gap = (c3.low - c1.high) / c1.high * 100.0
            if gap >= min_gap:
                out.append((idx + 2, FVG("Bullish", c3.low, c1.high, c1, c2, c3, c3.timestamp, timeframe=ltf_tf)))
        elif c3.high < c1.low:
            gap = (c1.low - c3.high) / c1.low * 100.0
            if gap >= min_gap:
                out.append((idx + 2, FVG("Bearish", c1.low, c3.high, c1, c2, c3, c3.timestamp, timeframe=ltf_tf)))
    return out


def simulate_trade(direction, entry, sl, subsequent, ltf_dur_ms):
    risk = abs(entry - sl)
    if risk <= 0:
        risk = entry * 0.001
    if direction == "Bullish":
        tp1, tp2, tp3 = entry + risk, entry + 2 * risk, entry + 3 * risk
    else:
        tp1, tp2, tp3 = entry - risk, entry - 2 * risk, entry - 3 * risk
    hit1 = hit2 = hit3 = False
    for c in subsequent:
        if direction == "Bullish":
            if c.low <= sl:
                break
            if not hit1 and c.high >= tp1: hit1 = True
            if not hit2 and c.high >= tp2: hit2 = True
            if c.high >= tp3:
                hit3 = True; break
        else:
            if c.high >= sl:
                break
            if not hit1 and c.low <= tp1: hit1 = True
            if not hit2 and c.low <= tp2: hit2 = True
            if c.low <= tp3:
                hit3 = True; break
    return {"1R": 1.0 if hit1 else -1.0, "2R": 2.0 if hit2 else -1.0, "3R": 3.0 if hit3 else -1.0,
            "hit1": hit1, "hit2": hit2, "hit3": hit3}


@dataclass
class BaseConfig:
    min_gap_pct: float = 0.05
    use_close_invalidation: bool = True
    session_filter: bool = False


def run_base_simulation(candles_4h, candles_ltf, ltf_tf, start_ms, cfg: BaseConfig):
    """Runs the deployed-style simulation once, returning a list of executed trades
    each annotated with its feature vector. No bias filters except min_gap."""
    ltf_dur = TIMEFRAME_MS.get(ltf_tf, 5 * 60 * 1000)
    candles_ltf = [c for c in candles_ltf if c.timestamp >= start_ms]
    if len(candles_ltf) < 3:
        return []
    ltf_close_ts = [c.timestamp + ltf_dur for c in candles_ltf]
    htf_close_ts = [c.timestamp + HTF_CANDLE_DURATION_MS for c in candles_4h]
    ltf_ts = [c.timestamp for c in candles_ltf]
    ltf_fvgs = build_ltf_fvgs(candles_ltf, ltf_tf, cfg.min_gap_pct)
    n_fvgs = len(ltf_fvgs)
    n_ltf = len(candles_ltf)

    first_touch_map: Dict[int, Optional[Tuple[int, str]]] = {}
    def first_touch(fvg):
        if fvg.formed_at in first_touch_map:
            return first_touch_map[fvg.formed_at]
        start_i = bisect.bisect_left(ltf_ts, fvg.close_timestamp)
        res = None
        for c in candles_ltf[start_i:]:
            if fvg.direction == "Bullish":
                if c.low <= fvg.top and c.high >= fvg.bottom:
                    res = (c.timestamp, ltf_tf); break
            else:
                if c.high >= fvg.bottom and c.low <= fvg.top:
                    res = (c.timestamp, ltf_tf); break
        first_touch_map[fvg.formed_at] = res
        return res

    active_4h_cache = {}
    executed = []
    entered_fvg_ts = set()
    curr_sim_idx = 2
    fvg_ptr = 0

    while fvg_ptr < n_fvgs:
        fvg_idx, cur_fvg = ltf_fvgs[fvg_ptr]
        if fvg_idx < curr_sim_idx:
            fvg_ptr += 1; continue
        curr_time = ltf_close_ts[fvg_idx]
        num_4h = bisect.bisect_right(htf_close_ts, curr_time)
        if num_4h < 3:
            fvg_ptr += 1; continue
        if num_4h not in active_4h_cache:
            closed_4h = candles_4h[:num_4h]
            active_4h_cache[num_4h] = compute_all_active_4h_fvgs(
                candles_4h=closed_4h, current_time_ms=htf_close_ts[num_4h - 1],
                use_close_invalidation=cfg.use_close_invalidation, enforce_closed_filter=True)
        base_4h = active_4h_cache[num_4h]
        active_4h = [f for f in base_4h if not (
            (f.direction == "Bullish" and cur_fvg.c3.close < f.bottom) or
            (f.direction == "Bearish" and cur_fvg.c3.close > f.top))]
        if not active_4h:
            fvg_ptr += 1; continue

        # anchored touches
        touched = []
        for fvg in active_4h:
            ft = first_touch(fvg)
            if not ft or ft[0] > cur_fvg.c3.timestamp:
                continue
            touched.append((ft[0], fvg))
        if not touched:
            fvg_ptr += 1; continue
        best_touch = max(t for t, _ in touched)
        anchor = next(f for t, f in touched if t == best_touch)

        if cur_fvg.direction != anchor.direction or cur_fvg.close_timestamp < best_touch:
            fvg_ptr += 1; continue

        # candidate pool (deepest extreme selection)
        candidates = []
        for pp in range(fvg_ptr + 1):
            pid, p = ltf_fvgs[pp]
            if p.direction != anchor.direction or p.close_timestamp < best_touch:
                continue
            if p.formed_at in entered_fvg_ts:
                continue
            sl = min(p.c1.low, p.c2.low, p.c3.low) if p.direction == "Bullish" else max(p.c1.high, p.c2.high, p.c3.high)
            inval = False
            for sub in candles_ltf[pid + 1:fvg_idx + 1]:
                if p.direction == "Bullish":
                    if sub.low <= sl: inval = True; break
                else:
                    if sub.high >= sl: inval = True; break
            if not inval:
                candidates.append(p)
        if not candidates:
            fvg_ptr += 1; continue
        best = min(candidates, key=lambda f: (f.bottom, f.formed_at)) if anchor.direction == "Bullish" \
            else max(candidates, key=lambda f: (f.top, -f.formed_at))
        if best.formed_at in entered_fvg_ts:
            fvg_ptr += 1; continue

        entry = best.top if best.direction == "Bullish" else best.bottom
        sl = (min(best.c1.low, best.c2.low, best.c3.low) if best.direction == "Bullish"
              else max(best.c1.high, best.c2.high, best.c3.high))

        entered = False
        k = fvg_idx + 1
        while k < n_ltf:
            ck = candles_ltf[k]
            if best.direction == "Bullish":
                if ck.low <= sl and ck.high < entry:
                    entered_fvg_ts.add(best.formed_at); break
                if ck.low <= entry: entered = True; break
            else:
                if ck.high >= sl and ck.low > entry:
                    entered_fvg_ts.add(best.formed_at); break
                if ck.high >= entry: entered = True; break
            k += 1
        if not entered:
            fvg_ptr += 1; continue

        entry_ts = candles_ltf[k].timestamp
        outcome = simulate_trade(best.direction, entry, sl, candles_ltf[k:], ltf_dur)
        entered_fvg_ts.add(best.formed_at)

        # ---- feature vector ----
        touch_idx = bisect.bisect_left(ltf_ts, best_touch)
        ltf_age = fvg_idx - touch_idx                      # LTF candles since 4H touch
        touch_age = k - touch_idx                          # LTF candles since touch -> entry
        htf_age = num_4h - bisect.bisect_right(htf_close_ts, anchor.close_timestamp)
        if anchor.direction == "Bullish":
            dist = (best.bottom - anchor.bottom) / anchor.bottom * 100.0
        else:
            dist = (anchor.top - best.top) / anchor.top * 100.0
        c2 = best.c2
        c2_rng = max(c2.high - c2.low, 1e-9)
        c2_body = abs(c2.close - c2.open)
        c2_mom = (c2.close - c2.open) if best.direction == "Bullish" else (c2.open - c2.close)
        momentum = c2_body / c2_rng >= 0.5 and c2_mom > 0
        gap = best.gap_pct
        risk_pct = abs(entry - sl) / entry * 100.0

        executed.append({
            "direction": best.direction, "entry": entry, "sl": sl, "entry_ts": entry_ts,
            **outcome,
            "ltf_age": ltf_age, "touch_age": touch_age, "htf_age": htf_age,
            "dist_from_4h_pct": round(dist, 4), "gap_pct": round(gap, 4),
            "momentum": momentum, "risk_pct": round(risk_pct, 4),
            "session": _ny_session(cur_fvg.close_timestamp),
            "entry_session": _ny_session(entry_ts),
        })
        fvg_ptr += 1

    return executed


# ------------------------------------------------------------------------------
# Marginal analysis
# ------------------------------------------------------------------------------

def agg(trades, key_fn=None, label_fn=None, buckets=None):
    """Group trades into buckets by a feature and report metrics."""
    groups = {}
    for t in trades:
        b = label_fn(t) if label_fn else str(key_fn(t))
        groups.setdefault(b, []).append(t)
    rows = []
    for b, ts in groups.items():
        n = len(ts)
        wins = sum(1 for t in ts if t["hit2"])
        net = sum(t["2R"] for t in ts)
        gw = sum(t["2R"] for t in ts if t["hit2"])
        gl = sum(1.0 for t in ts if not t["hit2"])
        pf = gw / gl if gl > 0 else (999.0 if gw > 0 else 0.0)
        rows.append({"bucket": b, "trades": n, "win_rate_2r": round(wins / n * 100, 1),
                     "net_2r": round(net, 2), "pf_2r": round(pf, 2)})
    rows.sort(key=lambda r: (r["net_2r"], r["trades"], r["pf_2r"]), reverse=True)
    return rows


def marginal_ltf_age(trades):
    def label(t):
        a = t["ltf_age"]
        if a <= 3: return "0-3 (freshest)"
        if a <= 6: return "4-6"
        if a <= 12: return "7-12"
        if a <= 24: return "13-24"
        return "25+ (stale)"
    return agg(trades, label_fn=label)


def marginal_dist(trades):
    def label(t):
        d = t["dist_from_4h_pct"]
        if d <= 0.5: return "<=0.5%"
        if d <= 1.0: return "0.5-1%"
        if d <= 2.0: return "1-2%"
        if d <= 5.0: return "2-5%"
        return "5%+"
    return agg(trades, label_fn=label)


def marginal_gap(trades):
    def label(t):
        g = t["gap_pct"]
        if g < 0.05: return "<0.05%"
        if g <= 0.1: return "0.05-0.1%"
        if g <= 0.3: return "0.1-0.3%"
        if g <= 0.6: return "0.3-0.6%"
        return "0.6%+"
    return agg(trades, label_fn=label)


def marginal_htf_age(trades):
    def label(t):
        a = t["htf_age"]
        if a <= 5: return "0-5 4H candles"
        if a <= 10: return "6-10"
        if a <= 20: return "11-20"
        return "21+"
    return agg(trades, label_fn=label)


def marginal_momentum(trades):
    return agg(trades, label_fn=lambda t: "momentum" if t["momentum"] else "no-momentum")


def marginal_session(trades):
    return agg(trades, label_fn=lambda t: "NY-session" if t["session"] else "non-NY")


def marginal_touch_age(trades):
    def label(t):
        a = t["touch_age"]
        if a <= 3: return "0-3"
        if a <= 6: return "4-6"
        if a <= 12: return "7-12"
        return "13+"
    return agg(trades, label_fn=label)


def marginal_risk(trades):
    def label(t):
        r = t["risk_pct"]
        if r < 0.3: return "<0.3%"
        if r < 0.5: return "0.3-0.5%"
        if r < 1.0: return "0.5-1%"
        return "1%+"
    return agg(trades, label_fn=label)


# ------------------------------------------------------------------------------
# Result / report writing
# ------------------------------------------------------------------------------

def fmt_rows(rows, indent="    "):
    lines = []
    for r in rows:
        lines.append(f"{indent}{r['bucket']:<16} trades={r['trades']:>4}  WR2R={r['win_rate_2r']:>5.1f}%  "
                     f"net2R={r['net_2r']:>+6.1f}  PF={r['pf_2r']:>5}")
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--ltf", default="5m")
    args = ap.parse_args()

    symbol = args.symbol.upper()
    prov = get_market_data_provider()
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.days * 24 * 3600 * 1000
    warm_start = start_ms - 14 * 24 * 3600 * 1000

    print(f"Fetching {args.days} days {symbol} {args.ltf}...")
    candles_4h = await fetch_candles(prov, symbol, "4h", warm_start, now_ms)
    candles_ltf = await fetch_candles(prov, symbol, args.ltf, start_ms, now_ms)
    print(f"4H: {len(candles_4h)} candles | {args.ltf}: {len(candles_ltf)} candles")

    print("Running base simulation (close invalidation, min_gap 0.05, 2R target)...")
    trades = run_base_simulation(candles_4h, candles_ltf, args.ltf, start_ms, BaseConfig())
    print(f"Executed trades (baseline): {len(trades)}")

    sections = {
        "ltf_fvg_age": marginal_ltf_age(trades),
        "touch_age": marginal_touch_age(trades),
        "dist_from_4h": marginal_dist(trades),
        "gap_pct": marginal_gap(trades),
        "htf_age": marginal_htf_age(trades),
        "momentum": marginal_momentum(trades),
        "session": marginal_session(trades),
        "risk_pct": marginal_risk(trades),
    }

    with open("enhanced_backtest_results.json", "w") as f:
        json.dump({
            "symbol": symbol, "days": args.days, "ltf": args.ltf,
            "baseline_trades": len(trades),
            "trades": [
                {k: t[k] for k in ("direction", "ltf_age", "touch_age", "htf_age",
                                   "dist_from_4h_pct", "gap_pct", "momentum", "session",
                                   "risk_pct", "2R", "hit2")}
                for t in trades
            ],
            "marginal": {k: v for k, v in sections.items()},
        }, f, indent=2)

    # ---- report ----
    lines = []
    lines.append(f"# Enhanced Backtest — Bias & Parameter Research")
    lines.append(f"\nSymbol **{symbol}** · {args.days} days · LTF **{args.ltf}** · close invalidation · 2R target")
    lines.append(f"Baseline executed trades: **{len(trades)}**\n")

    lines.append("## 1. LTF FVG age (candles between 4H touch and LTF FVG c3 close)")
    lines.append("Freshest gaps, formed immediately after the zone is touched, should carry the strongest imbalance.")
    lines.append("```")
    lines.append(fmt_rows(sections["ltf_fvg_age"]))
    lines.append("```\n")

    lines.append("## 2. Touch-to-entry age (LTF candles from 4H touch until entry fill)")
    lines.append("```")
    lines.append(fmt_rows(sections["touch_age"]))
    lines.append("```\n")

    lines.append("## 3. Distance of LTF FVG from 4H anchor zone")
    lines.append("Closer-to-zone LTF FVGs coincide with the HTF imbalance and should retrace better.")
    lines.append("```")
    lines.append(fmt_rows(sections["dist_from_4h"]))
    lines.append("```\n")

    lines.append("## 4. LTF FVG gap size band")
    lines.append("```")
    lines.append(fmt_rows(sections["gap_pct"]))
    lines.append("```\n")

    lines.append("## 5. 4H (HTF) anchor FVG age at time of LTF FVG formation")
    lines.append("```")
    lines.append(fmt_rows(sections["htf_age"]))
    lines.append("```\n")

    lines.append("## 6. Momentum impulse candle (c2) filter")
    lines.append("```")
    lines.append(fmt_rows(sections["momentum"]))
    lines.append("```\n")

    lines.append("## 7. Session filter (NY 13-22 UTC)")
    lines.append("```")
    lines.append(fmt_rows(sections["session"]))
    lines.append("```\n")

    lines.append("## 8. Risk per trade (SL distance as % of price)")
    lines.append("```")
    lines.append(fmt_rows(sections["risk_pct"]))
    lines.append("```")

    report = "\n".join(lines)
    with open("enhanced_backtest_findings.md", "w") as f:
        f.write(report)
    print(report)
    print("\nWrote enhanced_backtest_results.json and enhanced_backtest_findings.md")


if __name__ == "__main__":
    asyncio.run(main())
