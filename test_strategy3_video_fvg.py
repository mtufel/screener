"""
Tests for Strategy 3 (Video FVG):
  - Engine pure functions (calc_entry_params, find_4h_fvgs, etc.)
  - Adapter registration, defaults, and method call correctness
  - Report / trade dataclass shapes

Mirrors test_strategy_adapter_equivalence.py patterns.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from strategies import get_strategy, list_strategy_names, registry_snapshot

# Engine pure-function imports
from strategy_video_fvg import (
    Candle,
    VideoFVGSetup,
    _three_candle_fvg,
    _is_strong_body,
    calc_entry_params,
    find_4h_fvgs,
    select_anchor_4h,
    is_htf_respected,
    _htf_respect_details,
    find_first_ltf_fvg,
)


# ==============================================================================
# Engine: calc_entry_params — spec scenario
# ==============================================================================

def _c(o, h, l, c, t=0):
    return Candle(timestamp=t, open=o, high=h, low=l, close=c, volume=0.0)


class TestCalcEntryParams:
    def test_bullish_spec_scenario(self):
        """Bullish FVG bottom=10000, top=10050, wick-low=9980 → entry=10000, SL=9980, TP3R=10060."""
        fvg = {
            "direction": "Bullish",
            "top": 10050.0,
            "bottom": 10000.0,
            "c1": _c(10000, 10020, 9980, 10010, 1),
            "c2": _c(10010, 10030, 9990, 10020, 2),
            "c3": _c(10020, 10060, 10000, 10055, 3),
        }
        p = calc_entry_params(fvg, "Bullish")
        assert p["entry_price"] == 10000.0
        assert p["stop_loss"] == 9980.0
        assert p["risk_r"] == 20.0
        assert abs(p["tp_1r"] - 10020.0) < 1e-9
        assert abs(p["tp_2r"] - 10040.0) < 1e-9
        assert abs(p["tp_3r"] - 10060.0) < 1e-9

    def test_bearish_spec_scenario(self):
        """Bearish FVG top=60000, wick-high=60100 → entry=60000, SL=60100, TP3R=59700."""
        fvg = {
            "direction": "Bearish",
            "top": 60000.0,
            "bottom": 59950.0,
            "c1": _c(60000, 60100, 59980, 59990, 1),
            "c2": _c(59990, 59900, 59950, 59960, 2),
            "c3": _c(59960, 60000, 59800, 59850, 3),
        }
        p = calc_entry_params(fvg, "Bearish")
        assert p["entry_price"] == 60000.0
        assert p["stop_loss"] == 60100.0
        assert p["risk_r"] == 100.0
        assert abs(p["tp_3r"] - 59700.0) < 1e-9

    def test_zero_risk_returns_zero(self):
        """Entry == SL (all wick lows equal to entry) → risk_r == 0.0 with no min-risk bump."""
        fvg = {
            "direction": "Bullish",
            "top": 10050.0,
            "bottom": 10000.0,
            "c1": _c(10000, 10020, 10000, 10010, 1),
            "c2": _c(10010, 10030, 10010, 10020, 2),
            "c3": _c(10020, 10060, 10000, 10055, 3),
        }
        p = calc_entry_params(fvg, "Bullish")
        assert p["risk_r"] == 0.0  # no guard here (guard lives in backtest simulation)


# ==============================================================================
# Engine: 4H FVG detection
# ==============================================================================

class TestFind4HFVGs:
    def test_bullish_fvg_detected(self):
        """c3.low > c1.high → Bullish FVG formed."""
        c1 = _c(100, 105, 99, 103, 1)
        c2 = _c(103, 107, 102, 105, 2)
        c3 = _c(105, 110, 106, 108, 3)   # low=106 > high=105
        result = _three_candle_fvg(c1, c2, c3, "4h")
        assert result is not None
        assert result["direction"] == "Bullish"
        assert result["bottom"] == 105.0
        assert result["top"] == 106.0

    def test_bearish_fvg_detected(self):
        """c3.high < c1.low → Bearish FVG formed."""
        c1 = _c(100, 101, 95, 96, 1)
        c2 = _c(96, 98, 94, 95, 2)
        c3 = _c(92, 94, 90, 91, 3)   # high=94 < low=95
        result = _three_candle_fvg(c1, c2, c3, "4h")
        assert result is not None
        assert result["direction"] == "Bearish"
        assert result["top"] == 95.0
        assert result["bottom"] == 94.0

    def test_no_imbalance_returns_none(self):
        """Contiguous candles → no FVG."""
        c1 = _c(100, 105, 99, 103, 1)
        c2 = _c(103, 106, 100, 104, 2)
        c3 = _c(104, 108, 102, 106, 3)
        assert _three_candle_fvg(c1, c2, c3, "4h") is None

    def test_select_anchor_returns_most_recent(self):
        """select_anchor_4h returns the first in newest-first list (most recent)."""
        c1 = _c(100, 105, 99, 103, 100)
        c2 = _c(103, 107, 102, 105, 101)
        c3 = _c(105, 110, 106, 108, 102)  # FVG #1 [105,106] formed_at=102
        c4 = _c(108, 112, 107, 111, 103)
        c5 = _c(111, 122, 112, 120, 104)  # FVG #2 [110,112] formed_at=104 (more recent)
        fvgs = find_4h_fvgs([c1, c2, c3, c4, c5])
        anchor = select_anchor_4h(fvgs)
        assert anchor is not None
        assert anchor["formed_at"] == 104  # most recent


# ==============================================================================
# Engine: HTF respect confirmation
# ==============================================================================

class TestStrongBody:
    def test_strong_body_bullish_true(self):
        """Bullish candle with body >= threshold fraction of range returns True."""
        # O=100, H=105, L=98, C=104 → body=4, range=7, ratio≈57% → True at 50%
        c = _c(100, 105, 98, 104)
        assert _is_strong_body(c, "Bullish", threshold=0.5) is True

    def test_strong_body_bearish_true(self):
        c = _c(105, 106, 98, 99)   # body=6, range=8, ratio=75%
        assert _is_strong_body(c, "Bearish", threshold=0.5) is True

    def test_strong_body_below_threshold(self):
        c = _c(100, 105, 98, 101)  # body=1, range=7, ratio=14%
        assert _is_strong_body(c, "Bullish", threshold=0.5) is False


class TestHTFRespectConcrete:
    def test_htf_confirmed_bullish(self):
        """Bullish anchor: touch at c[1], strong bullish body at c[2] = confirmed."""
        H4 = 4 * 3600 * 1000  # 4h in ms

        # Anchor: bullish FVG [50, 51], formed at t=28800000, closes at t=43200000
        c_a1 = _c(48, 50, 47, 49, 0)
        c_a2 = _c(49, 51, 48, 50, H4)
        c_a3 = _c(50, 53, 51, 52, 2 * H4)   # low=51 > c_a1.high=50 → FVG [50,51]
        anchor = _three_candle_fvg(c_a1, c_a2, c_a3, "4h")
        assert anchor is not None and anchor["direction"] == "Bullish"

        # Touch candle at/after anchor close enters zone [50,51]
        c_touch = _c(52, 54, 50, 53, 3 * H4)      # low=50 ≤ top=51, high=54 ≥ bottom=50
        # Confirmation candle: strong bullish body (body=2, range=4 → 50%)
        c_confirm = _c(52, 55, 51, 54, 4 * H4)
        candles_4h = [c_a1, c_a2, c_a3, c_touch, c_confirm]

        confirmed, confirm_ts = _htf_respect_details(anchor, candles_4h, htf_confirm_body_pct=0.5)
        assert confirmed is True
        assert confirm_ts == 4 * H4 + H4  # c_confirm timestamp + 4h close


# ==============================================================================
# Engine: LTF first FVG
# ==============================================================================

class TestFindFirstLTF:
    def test_first_fvg_selected_not_extreme(self):
        """find_first_ltf_fvg returns the earliest qualifying FVG, not the deepest."""
        # Simplest valid FVG: gap above c1 high
        c1 = _c(100, 105, 99, 103, 50)
        c2 = _c(103, 107, 102, 105, 51)
        c3 = _c(105, 110, 106, 108, 52)  # bullish FVG [105, 106]
        ltf = [c1, c2, c3]
        result = find_first_ltf_fvg(ltf, "Bullish", min_gap_pct=0.1, formed_after=0, ltf_tf="5m")
        assert result is not None
        assert result["formed_at"] == 52

    def test_fvg_before_formed_after_excluded(self):
        """FVG closing before formed_after timestamp is excluded."""
        # c3 at t=52 closes at 52 + 5m = 300052ms; formed_after=400000 > close_ts → excluded
        c1 = _c(100, 105, 99, 103, 50)
        c2 = _c(103, 107, 102, 105, 51)
        c3 = _c(105, 110, 106, 108, 52)
        ltf = [c1, c2, c3]
        result = find_first_ltf_fvg(ltf, "Bullish", min_gap_pct=0.1, formed_after=400000, ltf_tf="5m")
        assert result is None

    def test_min_gap_filter_excludes_small_gaps(self):
        """FVG below min_gap_pct threshold is excluded."""
        c1 = _c(1000, 1001, 999, 1000.5, 50)
        c2 = _c(1000.5, 1001.5, 1000, 1001, 51)
        c3 = _c(1001, 1001.8, 1000.2, 1001.5, 52)  # gap is tiny
        ltf = [c1, c2, c3]
        result = find_first_ltf_fvg(ltf, "Bullish", min_gap_pct=1.0, formed_after=0, ltf_tf="5m")
        assert result is None


# ==============================================================================
# Registry & adapter
# ==============================================================================

def test_video_fvg_in_registry():
    assert "video_fvg" in list_strategy_names()


def test_registry_snapshot_has_video_fvg():
    snap = registry_snapshot()
    assert "video_fvg" in snap
    assert snap["video_fvg"] == "Video FVG (4H Anchor)"


def test_video_fvg_name_and_display():
    strat = get_strategy("video_fvg")
    assert strat.name == "video_fvg"
    assert strat.display_name == "Video FVG (4H Anchor)"


def test_video_fvg_default_params_match_design_doc():
    strat = get_strategy("video_fvg")
    params = strat.resolve_params({})
    assert params["completion_target"] == "3R"
    assert params["min_gap_pct"] == 0.03
    assert params["htf_confirm_body_pct"] == 0.5
    assert params["ltf_timeframe"] == "5m"
    assert params["session_filter"] is False
    assert params["weekday_filter"] is False


def test_video_fvg_resolve_params_runtime_overrides_win():
    strat = get_strategy("video_fvg")
    params = strat.resolve_params({"min_gap_pct": 0.05, "session_filter": True})
    assert params["min_gap_pct"] == 0.05
    assert params["session_filter"] is True
    # Unchanged defaults
    assert params["completion_target"] == "3R"
    assert params["htf_confirm_body_pct"] == 0.5


# ==============================================================================
# Adapter: find_setups method
# ==============================================================================

@pytest.mark.asyncio
async def test_adapter_find_setups_calls_engine():
    strat = get_strategy("video_fvg")
    mock_provider = MagicMock()
    params = {
        "ltf_timeframe": "5m",
        "min_gap_pct": 0.03,
        "htf_confirm_body_pct": 0.5,
        "completion_target": "3R",
        "session_filter": False,
        "weekday_filter": False,
        "sessions": "ALL",
    }
    with patch(
        "strategy_video_fvg.get_video_setup_for_symbol",
        new_callable=AsyncMock,
    ) as mock_engine:
        mock_engine.return_value = None
        await strat.find_setups("BTC", mock_provider, params)
        mock_engine.assert_called_once()
        call_kwargs = mock_engine.call_args.kwargs
        assert call_kwargs["symbol"] == "BTC"
        assert call_kwargs["min_gap_pct"] == 0.03
        assert call_kwargs["completion_target"] == "3R"


@pytest.mark.asyncio
async def test_adapter_find_setups_returns_list():
    strat = get_strategy("video_fvg")
    mock_provider = MagicMock()
    mock_setup = MagicMock()
    mock_setup.entry_price = 60000.0
    params = {
        "ltf_timeframe": "5m",
        "min_gap_pct": 0.03,
        "htf_confirm_body_pct": 0.5,
        "completion_target": "3R",
        "session_filter": False,
        "weekday_filter": False,
        "sessions": "ALL",
    }
    with patch(
        "strategy_video_fvg.get_video_setup_for_symbol",
        new_callable=AsyncMock,
    ) as mock_engine:
        mock_engine.return_value = mock_setup
        result = await strat.find_setups("ETH", mock_provider, params)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] is mock_setup


@pytest.mark.asyncio
async def test_adapter_find_setups_returns_empty_when_none():
    strat = get_strategy("video_fvg")
    mock_provider = MagicMock()
    params = {
        "ltf_timeframe": "5m",
        "min_gap_pct": 0.03,
        "htf_confirm_body_pct": 0.5,
        "completion_target": "3R",
        "session_filter": False,
        "weekday_filter": False,
        "sessions": "ALL",
    }
    with patch(
        "strategy_video_fvg.get_video_setup_for_symbol",
        new_callable=AsyncMock,
    ) as mock_engine:
        mock_engine.return_value = None
        result = await strat.find_setups("SOL", mock_provider, params)
        assert result == []


# ==============================================================================
# Adapter: backtest method
# ==============================================================================

@pytest.mark.asyncio
async def test_adapter_backtest_calls_engine():
    strat = get_strategy("video_fvg")
    mock_provider = MagicMock()
    mock_report = MagicMock()
    params = {
        "ltf_timeframe": "5m",
        "min_gap_pct": 0.03,
        "htf_confirm_body_pct": 0.5,
        "completion_target": "3R",
        "session_filter": False,
        "weekday_filter": False,
        "sessions": "ALL",
    }
    with patch(
        "backtest_video_fvg.run_video_fvg_backtest",
        new_callable=AsyncMock,
    ) as mock_engine:
        mock_engine.return_value = mock_report
        await strat.backtest("BTC", 30, mock_provider, params)
        mock_engine.assert_called_once()
        call_kwargs = mock_engine.call_args.kwargs
        assert call_kwargs["symbol"] == "BTC"
        assert call_kwargs["days"] == 30
        assert call_kwargs["min_gap_pct"] == 0.03
        assert call_kwargs["completion_target"] == "3R"


# ==============================================================================
# Report shape: VideoBacktestReport mirrors ExtremeBacktestReport
# ==============================================================================

def test_report_has_extreme_compatible_fields():
    from backtest_video_fvg import VideoBacktestReport, VideoHistoricalTrade

    report_fields = set(VideoBacktestReport.__dataclass_fields__)
    trade_fields = set(VideoHistoricalTrade.__dataclass_fields__)

    # Both reports must have these shared fields for API / ledger compatibility
    for field_name in [
        "symbol", "days", "ltf_timeframe", "min_gap_pct",
        "total_trades", "wins_1r", "wins_2r", "wins_3r", "losses",
        "net_pnl_1r", "net_pnl_2r", "net_pnl_3r",
        "profit_factor_1r", "profit_factor_2r", "profit_factor_3r",
        "max_drawdown_r", "avg_trade_duration_min", "avg_mfe_r",
        "trades",
    ]:
        assert field_name in report_fields, f"Missing field: {field_name}"

    # Per-trade record fields shared with ExtremeHistoricalTrade
    for field_name in [
        "symbol", "direction", "entry_timestamp", "entry_price",
        "stop_loss", "risk_r", "tp_1r", "tp_2r", "tp_3r",
        "hit_1r", "hit_2r", "hit_3r", "exit_timestamp", "exit_reason",
        "realized_r_1r", "realized_r_2r", "realized_r_3r",
        "mfe_r", "mae_r", "duration_minutes",
        "ltf_fvg_bottom", "ltf_fvg_top",
        "htf_fvg_bottom", "htf_fvg_top",
        "fvg_formation_timestamp",
    ]:
        assert field_name in trade_fields, f"Missing field: {field_name}"

    # Video-specific additive fields
    assert "htf_confirm_body_pct" in report_fields
    assert "completion_target" in report_fields
    assert "htf_confirm_timestamp" in trade_fields


# ==============================================================================
# VideoFVGSetup dataclass
# ==============================================================================

def test_setup_has_required_fields():
    from strategy_video_fvg import VideoFVGSetup

    required = [
        "symbol", "direction", "anchor", "ltf_fvg",
        "entry_price", "stop_loss", "risk_r",
        "tp_1r", "tp_2r", "tp_3r",
        "completion_target", "ltf_timeframe", "htf_confirmed",
        "risk_pct", "is_valid_risk",
    ]
    for field_name in required:
        assert hasattr(VideoFVGSetup, field_name) or field_name in VideoFVGSetup.__dataclass_fields__, field_name

    # Anchor is plain Dict (JSON-serialisable for ledger)
    setup = VideoFVGSetup(
        symbol="BTC",
        direction="Bullish",
        anchor={"bottom": 100, "top": 105},
        ltf_fvg={"bottom": 1000, "top": 1005},
        entry_price=1000.0,
        stop_loss=990.0,
        risk_r=10.0,
        tp_1r=1010.0,
        tp_2r=1020.0,
        tp_3r=1030.0,
    )
    assert isinstance(setup.anchor, dict)
    assert isinstance(setup.ltf_fvg, dict)
    assert setup.risk_pct == 1.0
    assert setup.is_valid_risk is True

# ==============================================================================
# Daemon / ledger wiring — _extreme_setup_payload accepts a VideoFVGSetup
# ==============================================================================

def test_setup_payload_accepts_video_fvg_setup():
    """The shared daemon payload builder must serialize a VideoFVGSetup (whose
    anchor/ltf_fvg are plain dicts) without crashing — covers the ledger/dashboard
    wiring for Strategy 3 (openspec task 5.2)."""
    from screener_cycle import _extreme_setup_payload
    from strategy_video_fvg import VideoFVGSetup

    setup = VideoFVGSetup(
        symbol="BTC",
        direction="Bullish",
        anchor={
            "direction": "Bullish",
            "bottom": 78000.0,
            "top": 80500.0,
            "formed_at": 1725372000000,  # some ms epoch
        },
        ltf_fvg={
            "direction": "Bullish",
            "bottom": 77000.0,
            "top": 77100.0,
            "formed_at": 1725454800000,
        },
        entry_price=77000.0,
        stop_loss=76900.0,
        risk_r=100.0,
        tp_1r=77100.0,
        tp_2r=77200.0,
        tp_3r=77300.0,
        completion_target="3R",
        ltf_timeframe="5m",
    )

    payload = _extreme_setup_payload("BTC", setup, curr_px=77050.0, strategy_name="video_fvg")

    assert payload["strategy"] == "video_fvg"
    assert payload["direction"] == "Bullish"
    assert payload["entry_price"] == 77000.0
    # Anchor block normalized from plain dict
    assert payload["anchor"]["bottom"] == 78000.0
    assert payload["anchor"]["top"] == 80500.0
    assert payload["anchor"]["direction"] == "Bullish"
    # Target-FVG block normalized from plain dict (width/gap_pct computed)
    assert payload["target_fvg"]["bottom"] == 77000.0
    assert payload["target_fvg"]["top"] == 77100.0
    assert payload["target_fvg"]["direction"] == "Bullish"
    assert payload["target_fvg"]["width"] == 100.0
    assert payload["target_fvg"]["gap_pct"] == pytest.approx(0.130, abs=0.001)


def test_ledger_records_and_filters_video_fvg_trade():
    """End-to-end ledger wiring (openspec task 5.1): a Strategy 3 setup payload
    fed through the shared trade tracker must be recorded with strategy='video_fvg'
    and returned by get_filtered_trades(strategy='video_fvg')."""
    from screener_cycle import _extreme_setup_payload
    from strategy_video_fvg import VideoFVGSetup
    from extreme_trade_tracker import extreme_trade_tracker

    setup = VideoFVGSetup(
        symbol="BTC", direction="Bullish",
        anchor={"direction": "Bullish", "bottom": 78000.0, "top": 80500.0, "formed_at": 1725372000000},
        ltf_fvg={"direction": "Bullish", "bottom": 77000.0, "top": 77100.0, "formed_at": 1725454800000},
        entry_price=77000.0, stop_loss=76900.0, risk_r=100.0,
        tp_1r=77100.0, tp_2r=77200.0, tp_3r=77300.0,
        completion_target="3R", ltf_timeframe="5m",
    )
    payload = _extreme_setup_payload("BTC", setup, curr_px=77050.0, strategy_name="video_fvg")
    assert payload["strategy"] == "video_fvg"

    # Guard: clear any name collision, then feed the payload through the ledger.
    extreme_trade_tracker.clear_history() if hasattr(extreme_trade_tracker, "clear_history") else None
    trades = extreme_trade_tracker.process_live_setups([payload], {"BTC": 77050.0, "BTCUSDT": 77050.0})
    assert isinstance(trades, list)

    filtered = extreme_trade_tracker.get_filtered_trades(strategy="video_fvg")
    # The filter exposes the requested strategy on the response.
    assert filtered["filters"]["strategy"] == "video_fvg"
    # The recorded video_fvg setup is returned by the filtered query.
    symbols = {t.get("symbol") for t in filtered.get("trades", [])}
    assert "BTC" in symbols
