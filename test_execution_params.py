"""
Strategy-based TDD tests for Strategy 2 Step 5: execution parameters + pipeline.

Rules under test (STRATEGIES.md):
- Entry: outer boundary of the extreme LTF FVG (Bullish: zone top, Bearish: zone bottom).
- Stop Loss: exact extreme wick across the THREE formation candles [c1, c2, c3].
- Targets: 1R / 2R / 3R from entry, where 1R = |entry - SL| (risk_r).
- End-to-end (get_extreme_setup_for_symbol) with a dummy client wires
  anchor -> discovery -> extreme selection -> parameters with no network.
"""

import pytest
import pytest_asyncio

from strategy_extreme_fvg import (
    Candle,
    FVG,
    TouchedAnchor,
    build_extreme_trade_setup,
    select_extreme_ltf_fvg,
)

FIVE_MIN_MS = 5 * 60 * 1000
FOUR_H_MS = 4 * 3600 * 1000


def mk_c(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


T0 = 1_700_000_000_000 - (1_700_000_000_000 % FIVE_MIN_MS)


# ==============================================================================
# Bullish / bearish parameter sets
# ==============================================================================

def test_bullish_full_execution_parameter_set():
    """Bullish: entry=zone top, SL=min(c1,c2,c3 lows), targets entry+1R/2R/3R."""
    c1 = mk_c(T0, 100.0, 101.0, 97.0, 100.5)   # wick low 97.0 <- extreme
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    anchor = _mk_bullish_anchor()
    fvg = FVG(direction="Bullish", top=103.0, bottom=101.0, c1=c1, c2=c2, c3=c3,
              formed_at=c3.timestamp, timeframe="5m")

    setup = build_extreme_trade_setup(symbol="TEST", anchor=anchor, ltf_fvg=fvg)

    assert setup.entry_price == pytest.approx(103.0)          # outer boundary (top)
    assert setup.stop_loss == pytest.approx(97.0)             # extreme wick across c1..c3
    assert setup.risk_r == pytest.approx(6.0)
    assert setup.tp_1r == pytest.approx(109.0)
    assert setup.tp_2r == pytest.approx(115.0)
    assert setup.tp_3r == pytest.approx(121.0)
    assert setup.is_valid_risk is True
    assert setup.risk_pct == pytest.approx((6.0 / 103.0) * 100.0)


def test_bearish_full_execution_parameter_set():
    """Bearish: entry=zone bottom, SL=max(c1,c2,c3 highs), targets entry-1R/2R/3R."""
    c1 = mk_c(T0, 100.0, 103.0, 99.0, 99.5)   # wick high 103.0 <- extreme
    c2 = mk_c(T0 + FIVE_MIN_MS, 99.5, 100.0, 94.0, 94.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 94.5, 97.0, 93.0, 93.5)
    anchor = _mk_bearish_anchor()
    fvg = FVG(direction="Bearish", top=99.0, bottom=95.0, c1=c1, c2=c2, c3=c3,
              formed_at=c3.timestamp, timeframe="5m")

    setup = build_extreme_trade_setup(symbol="TEST", anchor=anchor, ltf_fvg=fvg)

    assert setup.entry_price == pytest.approx(95.0)           # outer boundary (bottom)
    assert setup.stop_loss == pytest.approx(103.0)            # extreme wick across c1..c3
    assert setup.risk_r == pytest.approx(8.0)
    assert setup.tp_1r == pytest.approx(87.0)
    assert setup.tp_2r == pytest.approx(79.0)
    assert setup.tp_3r == pytest.approx(71.0)
    assert setup.is_valid_risk is True


def _mk_bullish_anchor():
    h1 = mk_c(T0, 2400.0, 2402.0, 2390.0, 2395.0)
    h2 = mk_c(T0 + FOUR_H_MS, 2395.0, 2450.0, 2394.0, 2445.0)
    h3 = mk_c(T0 + 2 * FOUR_H_MS, 2445.0, 2460.0, 2415.0, 2455.0)
    fvg = FVG(direction="Bullish", top=2415.0, bottom=2402.0, c1=h1, c2=h2, c3=h3,
              formed_at=h3.timestamp, timeframe="4h")
    return TouchedAnchor(fvg=fvg, first_touch_timestamp=h3.timestamp + FOUR_H_MS,
                         most_recent_touch_timestamp=h3.timestamp + FOUR_H_MS,
                         is_currently_inside=False, touch_timeframe="4h")


def _mk_bearish_anchor():
    h1 = mk_c(T0, 2500.0, 2510.0, 2498.0, 2505.0)
    h2 = mk_c(T0 + FOUR_H_MS, 2505.0, 2506.0, 2450.0, 2455.0)
    h3 = mk_c(T0 + 2 * FOUR_H_MS, 2455.0, 2485.0, 2440.0, 2470.0)
    fvg = FVG(direction="Bearish", top=2498.0, bottom=2485.0, c1=h1, c2=h2, c3=h3,
              formed_at=h3.timestamp, timeframe="4h")
    return TouchedAnchor(fvg=fvg, first_touch_timestamp=h3.timestamp + FOUR_H_MS,
                         most_recent_touch_timestamp=h3.timestamp + FOUR_H_MS,
                         is_currently_inside=False, touch_timeframe="4h")


# ==============================================================================
# SL wick scan must cover ALL THREE formation candles
# ==============================================================================

def test_sl_wick_extreme_from_c1():
    """c1 carries the extreme wick -> SL = c1.low."""
    c1 = mk_c(T0, 100.0, 101.0, 96.0, 100.5)   # lowest low here
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    fvg = FVG(direction="Bullish", top=103.0, bottom=101.0, c1=c1, c2=c2, c3=c3,
              formed_at=c3.timestamp, timeframe="5m")
    setup = build_extreme_trade_setup(symbol="TEST", anchor=_mk_bullish_anchor(), ltf_fvg=fvg)
    assert setup.stop_loss == pytest.approx(96.0)


def test_sl_wick_extreme_from_c2():
    """c2 carries the extreme wick -> SL = c2.low (scan covers the middle candle)."""
    c1 = mk_c(T0, 100.0, 101.0, 99.0, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 95.5, 105.5)  # extreme wick
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    fvg = FVG(direction="Bullish", top=103.0, bottom=101.0, c1=c1, c2=c2, c3=c3,
              formed_at=c3.timestamp, timeframe="5m")
    setup = build_extreme_trade_setup(symbol="TEST", anchor=_mk_bullish_anchor(), ltf_fvg=fvg)
    assert setup.stop_loss == pytest.approx(95.5)


def test_sl_wick_extreme_from_c2_bearish():
    """Bearish: the middle candle c2 can carry the extreme high wick -> SL = c2.high.

    Note c3 can NEVER carry the bearish extreme (c3.high < c1.low <= c1.high),
    but c2 is unconstrained relative to c1.
    """
    c1 = mk_c(T0, 100.5, 102.0, 100.0, 100.5)   # zone top = c1.low = 100.0
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 104.0, 94.0, 94.5)  # extreme high 104.0
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 94.5, 98.5, 93.0, 93.5)  # gap [98.5..100.0]
    fvg = FVG(direction="Bearish", top=100.0, bottom=98.5, c1=c1, c2=c2, c3=c3,
              formed_at=c3.timestamp, timeframe="5m")
    setup = build_extreme_trade_setup(symbol="TEST", anchor=_mk_bearish_anchor(), ltf_fvg=fvg)
    assert setup.entry_price == pytest.approx(98.5)   # outer boundary (bottom)
    assert setup.stop_loss == pytest.approx(104.0)    # extreme wick from c2
    assert setup.risk_r == pytest.approx(5.5)


def test_completion_target_parameterization():
    """completion_target feeds the setup metadata (tracker resolves at that multiple)."""
    c1 = mk_c(T0, 100.0, 101.0, 97.0, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    fvg = FVG(direction="Bullish", top=103.0, bottom=101.0, c1=c1, c2=c2, c3=c3,
              formed_at=c3.timestamp, timeframe="5m")

    setup = build_extreme_trade_setup(symbol="TEST", anchor=_mk_bullish_anchor(), ltf_fvg=fvg,
                                      completion_target="1R")
    assert setup.completion_target == "1R"
    assert setup.tp_1r == pytest.approx(109.0)  # ladder is target-independent


# ==============================================================================
# Extreme ranking inside the pipeline (select_extreme_ltf_fvg)
# ==============================================================================

def test_select_extreme_deepest_bottom_bullish():
    """Bullish selection = min by bottom (deepest gap wins)."""
    deep = FVG(direction="Bullish", top=101.0, bottom=99.0, c1=None, c2=None, c3=None,
               formed_at=T0, timeframe="5m")
    shallow = FVG(direction="Bullish", top=112.0, bottom=110.0, c1=None, c2=None, c3=None,
                  formed_at=T0 + FIVE_MIN_MS, timeframe="5m")
    assert select_extreme_ltf_fvg([shallow, deep], "Bullish") is deep


def test_select_extreme_highest_top_bearish():
    """Bearish selection = max by top (highest gap wins)."""
    lower = FVG(direction="Bearish", top=96.0, bottom=94.0, c1=None, c2=None, c3=None,
                formed_at=T0, timeframe="5m")
    higher = FVG(direction="Bearish", top=99.0, bottom=97.0, c1=None, c2=None, c3=None,
                 formed_at=T0 + FIVE_MIN_MS, timeframe="5m")
    assert select_extreme_ltf_fvg([lower, higher], "Bearish") is higher


def test_select_extreme_empty_pool_returns_none():
    assert select_extreme_ltf_fvg([], "Bullish") is None


# ==============================================================================
# End-to-end pipeline: anchor -> discovery -> extreme selection -> parameters
# ==============================================================================

class _DummyHyperliquidClient:
    """Offline stand-in returning pre-built candle dicts (no network)."""

    def __init__(self, candles_4h, candles_ltf):
        self._c4h = [{"t": c.timestamp, "o": c.open, "h": c.high, "l": c.low,
                      "c": c.close, "v": c.volume} for c in candles_4h]
        self._ltf = [{"t": c.timestamp, "o": c.open, "h": c.high, "l": c.low,
                      "c": c.close, "v": c.volume} for c in candles_ltf]

    async def get_last_n_candles(self, symbol, timeframe, n):
        return self._c4h if timeframe == "4h" else self._ltf


@pytest.mark.asyncio
async def test_get_extreme_setup_for_symbol_end_to_end(monkeypatch):
    """Full pipeline on dummy data: 4H touch anchor -> post-touch 5m gap -> setup params."""
    from strategy_extreme_fvg import get_extreme_setup_for_symbol, htf_fvg_cache

    htf_fvg_cache.invalidate_cache()

    # 4H: bullish extreme [2402..2415]; h4's low 2410 <= 2415 touches the zone
    h1 = mk_c(T0, 2400.0, 2402.0, 2390.0, 2395.0)
    h2 = mk_c(T0 + FOUR_H_MS, 2395.0, 2450.0, 2394.0, 2445.0)
    h3 = mk_c(T0 + 2 * FOUR_H_MS, 2445.0, 2460.0, 2415.0, 2455.0)
    h4 = mk_c(T0 + 3 * FOUR_H_MS, 2455.0, 2470.0, 2410.0, 2465.0)

    # 5m: l1 touches the anchor zone (low 2414 <= 2415) -> anchor first touch via LTF;
    # then a valid gap [2418..2429] forms (c1.high 2418 < c3.low 2429), never entered
    # afterwards, and the last close (2430.5) stays above entry so no live fill.
    base = h4.timestamp + FIVE_MIN_MS
    l1 = mk_c(base, 2412.0, 2418.0, 2414.0, 2416.0)                    # pre-impulse
    l2 = mk_c(base + FIVE_MIN_MS, 2416.0, 2427.0, 2416.0, 2426.0)      # impulse up
    l3 = mk_c(base + 2 * FIVE_MIN_MS, 2426.0, 2431.0, 2429.0, 2430.5)  # holds above gap
    # zone [2418..2429], entry 2429, SL min(2414, 2416, 2429) = 2414 -> risk 15

    dummy = _DummyHyperliquidClient([h1, h2, h3, h4], [l1, l2, l3])
    monkeypatch.setattr("strategy_extreme_fvg.hyperliquid_client", dummy)

    setup = await get_extreme_setup_for_symbol("E2E", ltf_timeframe="5m")

    assert setup is not None
    assert setup.direction == "Bullish"
    assert setup.state == "PENDING_RETRACE"          # gap never touched post-formation
    assert setup.entry_timestamp is None
    assert setup.entry_price == pytest.approx(2429.0)  # zone top (outer boundary)
    assert setup.stop_loss == pytest.approx(2414.0)    # extreme wick across l1..l3
    assert setup.risk_r == pytest.approx(15.0)
    assert setup.tp_1r == pytest.approx(2444.0)
    assert setup.tp_2r == pytest.approx(2459.0)
    assert setup.tp_3r == pytest.approx(2474.0)
    assert setup.anchor.fvg.bottom == pytest.approx(2402.0)
    assert setup.anchor.first_touch_timestamp == l1.timestamp
    assert len(setup.all_unmitigated_fvgs) == 1


def test_extreme_trade_setup_risk_pct_property():
    """risk_pct derives from risk_r over entry price."""
    c1 = mk_c(T0, 100.0, 101.0, 97.0, 100.5)
    c2 = mk_c(T0 + FIVE_MIN_MS, 100.5, 106.0, 100.0, 105.5)
    c3 = mk_c(T0 + 2 * FIVE_MIN_MS, 105.5, 107.0, 103.0, 106.5)
    fvg = FVG(direction="Bullish", top=103.0, bottom=101.0, c1=c1, c2=c2, c3=c3,
              formed_at=c3.timestamp, timeframe="5m")
    setup = build_extreme_trade_setup(symbol="TEST", anchor=_mk_bullish_anchor(), ltf_fvg=fvg)
    assert setup.risk_pct == pytest.approx((6.0 / 103.0) * 100.0)
    assert setup.is_valid_risk is True
