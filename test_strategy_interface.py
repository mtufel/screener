"""
Tests for the strategy-framework interface:
- strategy-agnostic ledger fields (round-trip, backward-compat)
- daemon resolves active strategy by name via registry
- strategy params are merged correctly into daemon cfg
"""

import pytest

from extreme_trade_tracker import TrackedExtremeTrade, ExtremeTradeTracker
from strategies import get_strategy


# ==============================================================================
# Ledger fields
# ==============================================================================

def test_tracked_trade_has_strategy_and_strategy_params_fields():
    fields = {f.name for f in TrackedExtremeTrade.__dataclass_fields__.values()}
    assert "strategy" in fields
    assert "strategy_params" in fields


def test_tracked_trade_strategy_default_is_extreme_fvg():
    t = TrackedExtremeTrade(
        symbol="BTC", direction="Bullish", entry_price=60000,
        stop_loss=59000, risk_r=1000, risk_pct=1.67,
        tp_1r=61000, tp_2r=62000, tp_3r=63000, completion_target="2R",
    )
    assert t.strategy == "extreme_fvg"
    assert t.strategy_params == {}


def test_tracked_trade_accepts_custom_strategy():
    t = TrackedExtremeTrade(
        symbol="ETH", direction="Bearish", entry_price=3000,
        stop_loss=2900, risk_r=100, risk_pct=3.33,
        tp_1r=3100, tp_2r=3200, tp_3r=3300, completion_target="2R",
        strategy="strategy3_video",
        strategy_params={"ltf_timeframe": "5m", "target_bias": "momentum"},
    )
    assert t.strategy == "strategy3_video"
    assert t.strategy_params == {"ltf_timeframe": "5m", "target_bias": "momentum"}


def test_tracked_trade_to_dict_includes_new_fields():
    t = TrackedExtremeTrade(
        symbol="SOL", direction="Bullish", entry_price=200,
        stop_loss=190, risk_r=10, risk_pct=5.0,
        tp_1r=210, tp_2r=220, tp_3r=230, completion_target="2R",
        strategy="strategy3_video",
        strategy_params={"gap_ceiling": 0.003},
    )
    d = t.to_dict()
    assert d["strategy"] == "strategy3_video"
    assert d["strategy_params"] == {"gap_ceiling": 0.003}


def test_tracked_trade_from_dict_old_record_has_default_strategy():
    """Old persisted records without strategy/strategy_params fields
    must load with defaults (backward compatibility)."""
    old = {
        "symbol": "BTC", "direction": "Bullish", "entry_price": 60000,
        "stop_loss": 59000, "risk_r": 1000, "risk_pct": 1.67,
        "tp_1r": 61000, "tp_2r": 62000, "tp_3r": 63000, "completion_target": "2R",
    }
    t = TrackedExtremeTrade.from_dict(old)
    assert t.strategy == "extreme_fvg"
    assert t.strategy_params == {}


def test_tracked_trade_from_dict_new_record():
    d = {
        "symbol": "BTC", "direction": "Bullish", "entry_price": 60000,
        "stop_loss": 59000, "risk_r": 1000, "risk_pct": 1.67,
        "tp_1r": 61000, "tp_2r": 62000, "tp_3r": 63000, "completion_target": "2R",
        "strategy": "strategy3_video",
        "strategy_params": {"momentum_impulse_required": True},
    }
    t = TrackedExtremeTrade.from_dict(d)
    assert t.strategy == "strategy3_video"
    assert t.strategy_params == {"momentum_impulse_required": True}


# ==============================================================================
# Strategy resolution
# ==============================================================================

def test_get_filtered_trades_accepts_strategy_filter():
    from unittest.mock import MagicMock, patch
    from extreme_trade_tracker import ExtremeTradeTracker

    with patch("extreme_trade_tracker.PERSISTENCE_FILE", None):
        tracker = ExtremeTradeTracker()

    # No trades in empty tracker
    result = tracker.get_filtered_trades(strategy="extreme_fvg")
    assert result["status"] == "success"
    assert result["filters"]["strategy"] == "extreme_fvg"
    assert result["filters"]["state"] is None
    # Empty result = no trades matches the filter
    assert result["pagination"]["total"] == 0


def test_get_summary_accepts_strategy_filter():
    from unittest.mock import MagicMock, patch
    from extreme_trade_tracker import ExtremeTradeTracker

    with patch("extreme_trade_tracker.PERSISTENCE_FILE", None):
        tracker = ExtremeTradeTracker()

    # Empty tracker summary with strategy filter
    summary = tracker.get_summary(strategy="extreme_fvg")
    assert summary["total_tracked_trades"] == 0

    # Strategy filter with non-existent strategy returns 0
    summary2 = tracker.get_summary(strategy="nonexistent_strategy")
    assert summary2["total_tracked_trades"] == 0
    assert summary2["total_closed_trades"] == 0


def test_get_summary_returns_unfiltered_when_no_strategy():
    from unittest.mock import patch
    from extreme_trade_tracker import ExtremeTradeTracker

    with patch("extreme_trade_tracker.PERSISTENCE_FILE", None):
        tracker = ExtremeTradeTracker()

    summary = tracker.get_summary()
    assert "total_tracked_trades" in summary
    assert "total_closed_trades" in summary


def _seed_multi_strategy_tracker(tracker):
    """Seeds active + closed trades across two strategies."""
    ext = TrackedExtremeTrade(
        symbol="BTC", direction="Bullish", entry_price=60000,
        stop_loss=59000, risk_r=1000, risk_pct=1.67,
        tp_1r=61000, tp_2r=62000, tp_3r=63000, completion_target="2R",
        state="TRADE_ACTIVE", trade_id="btc-ext-1", strategy="extreme_fvg",
        strategy_params={"min_gap_pct": 0.05, "session_filter": True},
    )
    video = TrackedExtremeTrade(
        symbol="ETH", direction="Bearish", entry_price=3000,
        stop_loss=2900, risk_r=100, risk_pct=3.33,
        tp_1r=3100, tp_2r=3200, tp_3r=3300, completion_target="2R",
        state="PENDING_RETRACE", trade_id="eth-vid-1", strategy="strategy3_video",
        strategy_params={"momentum_impulse_required": True},
    )
    closed_ext = TrackedExtremeTrade(
        symbol="SOL", direction="Bullish", entry_price=200,
        stop_loss=190, risk_r=10, risk_pct=5.0,
        tp_1r=210, tp_2r=220, tp_3r=230, completion_target="2R",
        state="COMPLETED_TP", realized_r=2.0, trade_id="sol-ext-1",
        strategy="extreme_fvg", strategy_params={},
    )
    tracker.active_trades = {"btc-ext-1": ext, "eth-vid-1": video}
    tracker.history = [closed_ext]


def test_get_filtered_trades_filters_by_strategy_with_multi_strategy_data():
    from unittest.mock import patch
    from extreme_trade_tracker import ExtremeTradeTracker

    with patch("extreme_trade_tracker.PERSISTENCE_FILE", None):
        tracker = ExtremeTradeTracker()
    _seed_multi_strategy_tracker(tracker)

    # extreme_fvg -> sees BTC active + SOL closed
    res = tracker.get_filtered_trades(strategy="extreme_fvg")
    symbols = {t["symbol"] for t in res["trades"]}
    assert symbols == {"BTC", "SOL"}

    # strategy3_video -> sees ETH pending only
    res_vid = tracker.get_filtered_trades(strategy="strategy3_video")
    assert [t["symbol"] for t in res_vid["trades"]] == ["ETH"]
    assert res_vid["trades"][0]["strategy"] == "strategy3_video"

    # no filter -> sees all 3
    res_all = tracker.get_filtered_trades()
    assert len(res_all["trades"]) == 3


def test_get_summary_filters_by_strategy_with_multi_strategy_data():
    from unittest.mock import patch
    from extreme_trade_tracker import ExtremeTradeTracker

    with patch("extreme_trade_tracker.PERSISTENCE_FILE", None):
        tracker = ExtremeTradeTracker()
    _seed_multi_strategy_tracker(tracker)

    # extreme_fvg: 1 active, 1 closed (win)
    s_ext = tracker.get_summary(strategy="extreme_fvg")
    assert s_ext["total_closed_trades"] == 1
    assert s_ext["wins"] == 1
    assert s_ext["active_now"] == 1

    # strategy3_video: 0 closed, 1 pending, 0 active
    s_vid = tracker.get_summary(strategy="strategy3_video")
    assert s_vid["total_closed_trades"] == 0
    assert s_vid["active_now"] == 0
    assert s_vid["total_tracked_trades"] == 1


def test_daemon_cfg_contains_active_strategy():
    from screener_cycle import _runtime_extreme_config
    cfg = _runtime_extreme_config()
    assert "active_strategy" in cfg
    assert cfg["active_strategy"] == "extreme_fvg"
