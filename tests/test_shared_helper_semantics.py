"""
Regression pins for the shared helpers that became single sources of truth
during the Phase 1-2 refactor (extract-method dedup of hand-copied logic):

  strategy_extreme_fvg._three_candle_fvg  — FVG formation (was duplicated 4x)
  strategy_extreme_fvg._is_fvg_invalidated — breach semantics wick vs close (was duplicated 4x)
  hyperliquid_client._absorb_candles      — chunked-candle dedup, first-writer-wins (was duplicated 3x)
  extreme_trade_tracker._resolve_tp_target — completion-target -> (price, R multiple)

If one of these tests fails after a refactor, the refactor changed observable
strategy semantics, not just structure.
"""

from types import SimpleNamespace

from hyperliquid_client import _absorb_candles
from extreme_trade_tracker import _resolve_tp_target
from strategy_extreme_fvg import Candle, FVG, _is_fvg_invalidated, _three_candle_fvg


def mk_c(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


# --------------------------------------------------------------------------- #
# _three_candle_fvg — formation rules
# --------------------------------------------------------------------------- #
# Window used below: c1.high=100 / c3.low=110 -> bullish gap [100, 110].
BULL_C1 = mk_c(1000, 95.0, 100.0, 94.0, 99.0)
BULL_C2 = mk_c(1300, 99.0, 120.0, 98.0, 118.0)
BULL_C3 = mk_c(1600, 118.0, 125.0, 110.0, 120.0)
# c1.low=110 / c3.high=100 -> bearish gap [100, 110].
BEAR_C1 = mk_c(2000, 115.0, 116.0, 110.0, 112.0)
BEAR_C2 = mk_c(2300, 112.0, 111.0, 90.0, 92.0)
BEAR_C3 = mk_c(2600, 92.0, 100.0, 85.0, 88.0)


def test_three_candle_fvg_bullish_boundaries():
    fvg = _three_candle_fvg(BULL_C1, BULL_C2, BULL_C3, "15m")
    assert fvg is not None
    assert fvg.direction == "Bullish"
    assert fvg.bottom == BULL_C1.high == 100.0
    assert fvg.top == BULL_C3.low == 110.0
    assert fvg.formed_at == BULL_C3.timestamp
    assert fvg.timeframe == "15m"
    # Source candles must be retained (SL resolution depends on c1/c2/c3 wicks).
    assert (fvg.c1, fvg.c2, fvg.c3) == (BULL_C1, BULL_C2, BULL_C3)


def test_three_candle_fvg_bearish_boundaries():
    fvg = _three_candle_fvg(BEAR_C1, BEAR_C2, BEAR_C3, "4h")
    assert fvg is not None
    assert fvg.direction == "Bearish"
    assert fvg.top == BEAR_C1.low == 110.0
    assert fvg.bottom == BEAR_C3.high == 100.0
    assert fvg.formed_at == BEAR_C3.timestamp
    assert fvg.timeframe == "4h"


def test_three_candle_fvg_no_gap_returns_none():
    # c3 overlaps c1 entirely: neither c3.low > c1.high nor c3.high < c1.low.
    overlap = mk_c(3000, 96.0, 105.0, 90.0, 102.0)
    assert _three_candle_fvg(BULL_C1, BULL_C2, overlap, "15m") is None


def test_three_candle_fvg_formation_is_strict_inequality():
    # Touching the boundary is NOT a gap: c3.low == c1.high and c3.high == c1.low.
    touch_high = mk_c(3100, 99.0, 105.0, 100.0, 104.0)   # c3.low == c1.high
    assert _three_candle_fvg(BULL_C1, BULL_C2, touch_high, "15m") is None
    touch_low = mk_c(3200, 95.0, 110.0, 85.0, 88.0)      # c3.high == c1.low
    assert _three_candle_fvg(BEAR_C1, BEAR_C2, touch_low, "4h") is None


def test_three_candle_fvg_derived_metrics():
    fvg = _three_candle_fvg(BULL_C1, BULL_C2, BULL_C3, "15m")
    assert fvg.width == 10.0
    assert 0.0 < fvg.gap_pct < 100.0  # (10 / 105) * 100


# --------------------------------------------------------------------------- #
# _is_fvg_invalidated — wick vs close breach semantics
# --------------------------------------------------------------------------- #
def _bullish_fvg() -> FVG:
    return _three_candle_fvg(BULL_C1, BULL_C2, BULL_C3, "15m")


def _bearish_fvg() -> FVG:
    return _three_candle_fvg(BEAR_C1, BEAR_C2, BEAR_C3, "4h")


def test_bullish_wick_invalidation():
    fvg = _bullish_fvg()
    breach = [mk_c(5000, 105.0, 106.0, 95.0, 105.0)]   # wick below 100, close above
    assert _is_fvg_invalidated(fvg, breach, use_close_invalidation=False) is True


def test_bullish_wick_dip_is_not_close_invalidation():
    fvg = _bullish_fvg()
    dip = [mk_c(5000, 105.0, 106.0, 95.0, 105.0)]      # wick below, close above
    assert _is_fvg_invalidated(fvg, dip, use_close_invalidation=True) is False


def test_bullish_close_invalidation():
    fvg = _bullish_fvg()
    close_below = [mk_c(5000, 101.0, 106.0, 101.0, 99.5)]  # wick above, close below
    assert _is_fvg_invalidated(fvg, close_below, use_close_invalidation=True) is True
    assert _is_fvg_invalidated(fvg, close_below, use_close_invalidation=False) is False


def test_bullish_boundary_equality_is_not_invalidation():
    fvg = _bullish_fvg()
    exact = [mk_c(5000, 105.0, 106.0, 100.0, 105.0)]   # low == bottom (strict <)
    assert _is_fvg_invalidated(fvg, exact, use_close_invalidation=False) is False


def test_bearish_wick_and_close_invalidation_mirror():
    fvg = _bearish_fvg()
    wick_breach = [mk_c(5000, 105.0, 115.0, 104.0, 105.0)]  # high above 110, close below
    assert _is_fvg_invalidated(fvg, wick_breach, use_close_invalidation=False) is True
    assert _is_fvg_invalidated(fvg, wick_breach, use_close_invalidation=True) is False
    close_breach = [mk_c(5000, 109.0, 109.0, 104.0, 110.5)]  # wick below, close above
    assert _is_fvg_invalidated(fvg, close_breach, use_close_invalidation=True) is True
    assert _is_fvg_invalidated(fvg, close_breach, use_close_invalidation=False) is False


def test_bearish_boundary_equality_is_not_invalidation():
    fvg = _bearish_fvg()
    exact = [mk_c(5000, 105.0, 110.0, 104.0, 105.0)]   # high == top (strict >)
    assert _is_fvg_invalidated(fvg, exact, use_close_invalidation=False) is False


def test_live_price_breach_uses_wick_semantics():
    bull = _bullish_fvg()
    assert _is_fvg_invalidated(bull, [], use_close_invalidation=True, current_price=99.9) is True
    assert _is_fvg_invalidated(bull, [], use_close_invalidation=True, current_price=100.0) is False
    bear = _bearish_fvg()
    assert _is_fvg_invalidated(bear, [], use_close_invalidation=True, current_price=110.1) is True
    assert _is_fvg_invalidated(bear, [], use_close_invalidation=True, current_price=110.0) is False


def test_zero_live_price_is_sentinel_not_breach():
    # current_price defaults to 0.0 meaning "no live price". Without the sentinel,
    # a bullish FVG would be invalidated by every 0-price call.
    assert _is_fvg_invalidated(_bullish_fvg(), [], use_close_invalidation=False, current_price=0.0) is False


def test_candle_breach_and_live_price_are_either_sufficient():
    fvg = _bullish_fvg()
    breach = [mk_c(5000, 105.0, 106.0, 95.0, 105.0)]
    assert _is_fvg_invalidated(fvg, breach, use_close_invalidation=False, current_price=120.0) is True
    assert _is_fvg_invalidated(fvg, [], use_close_invalidation=False, current_price=95.0) is True


def test_no_subsequent_candles_and_no_price_means_active():
    assert _is_fvg_invalidated(_bullish_fvg(), [], use_close_invalidation=False) is False
    assert _is_fvg_invalidated(_bearish_fvg(), [], use_close_invalidation=True) is False


# --------------------------------------------------------------------------- #
# _absorb_candles — chunked fetch dedup
# --------------------------------------------------------------------------- #
def test_absorb_candles_keys_by_timestamp():
    m = {}
    _absorb_candles(m, [{"t": 100, "c": 1.0}, {"t": 200, "c": 2.0}])
    assert m == {100: {"t": 100, "c": 1.0}, 200: {"t": 200, "c": 2.0}}


def test_absorb_candles_first_occurrence_wins():
    # Earlier chunk data is authoritative: later chunks must NOT overwrite.
    m = {100: {"t": 100, "o": 1.0}}
    _absorb_candles(m, [{"t": 100, "o": 9.9}, {"t": 300, "o": 3.0}])
    assert m[100] == {"t": 100, "o": 1.0}
    assert m[300] == {"t": 300, "o": 3.0}


def test_absorb_candles_missing_t_defaults_to_zero():
    m = {}
    _absorb_candles(m, [{"o": 1.0}, {"t": 5, "o": 2.0}])
    assert m[0] == {"o": 1.0}
    assert m[5] == {"t": 5, "o": 2.0}
    _absorb_candles(m, [{"t": 0, "o": 42.0}])
    assert m[0] == {"o": 1.0}  # first t=0 still wins


def test_absorb_candles_empty_chunk_is_noop_and_mutates_in_place():
    m = {7: {"t": 7, "o": 1.0}}
    assert _absorb_candles(m, []) is None
    assert m == {7: {"t": 7, "o": 1.0}}


# --------------------------------------------------------------------------- #
# _resolve_tp_target — completion target resolution
# --------------------------------------------------------------------------- #
def _trade(target, tp1=101.0, tp2=102.0, tp3=103.0):
    return SimpleNamespace(completion_target=target, tp_1r=tp1, tp_2r=tp2, tp_3r=tp3)


def test_resolve_tp_target_known_targets():
    assert _resolve_tp_target(_trade("1R")) == (101.0, 1.0)
    assert _resolve_tp_target(_trade("2R")) == (102.0, 2.0)
    assert _resolve_tp_target(_trade("3R")) == (103.0, 3.0)


def test_resolve_tp_target_unknown_falls_back_to_3r():
    assert _resolve_tp_target(_trade("5R")) == (103.0, 3.0)
    assert _resolve_tp_target(_trade(None)) == (103.0, 3.0)


def test_resolve_tp_target_returns_floats():
    price, mult = _resolve_tp_target(_trade("2R"))
    assert isinstance(price, float)
    assert isinstance(mult, float)


def test_resolve_tp_target_reads_configured_target_prices():
    # Prices come from the trade object, not hard-coded R math.
    assert _resolve_tp_target(_trade("1R", tp1=250.25)) == (250.25, 1.0)
