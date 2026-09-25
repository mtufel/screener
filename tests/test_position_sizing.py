"""
Unit tests for the Position Sizing & Quantity Calculation Engine.
"""

import pytest
from position_sizing import (
    PositionSizeConfig,
    PositionSizeResult,
    PositionSizingEngine,
)


def test_fixed_usd_risk_bullish_calculation():
    """BTC Bullish setup: Entry 60000, SL 59500 (risk_r = 500), Risk $100 -> Qty 0.20 BTC."""
    config = PositionSizeConfig(
        enabled=True,
        risk_mode="fixed_amount",
        risk_amount_usd=100.0,
    )
    result = PositionSizingEngine.calculate(
        entry_price=60000.0,
        stop_loss=59500.0,
        symbol="BTCUSDT",
        config=config,
    )
    assert result is not None
    assert result.quantity == pytest.approx(0.2)
    assert result.risk_usd == pytest.approx(100.0)
    assert result.notional_usd == pytest.approx(12000.0)
    assert result.loss_at_sl_usd == pytest.approx(-100.0)
    assert result.pnl_1r_usd == pytest.approx(100.0)
    assert result.pnl_2r_usd == pytest.approx(200.0)
    assert result.pnl_3r_usd == pytest.approx(300.0)
    assert "0.2000 BTC" in result.quantity_formatted


def test_percent_equity_risk_bearish_calculation():
    """ETH Bearish setup: Entry 3000, SL 3060 (risk_r = 60), Equity $50,000 @ 1.5% ($750 risk)."""
    config = PositionSizeConfig(
        enabled=True,
        risk_mode="percent_equity",
        account_equity_usd=50000.0,
        risk_percent=1.5,
    )
    result = PositionSizingEngine.calculate(
        entry_price=3000.0,
        stop_loss=3060.0,
        symbol="ETH",
        config=config,
    )
    assert result is not None
    assert result.risk_usd == pytest.approx(750.0)
    # Qty = 750 / 60 = 12.5 ETH
    assert result.quantity == pytest.approx(12.5)
    assert result.notional_usd == pytest.approx(37500.0)
    assert result.loss_at_sl_usd == pytest.approx(-750.0)
    assert result.pnl_2r_usd == pytest.approx(1500.0)
    assert "12.50 ETH" in result.quantity_formatted


def test_tighter_sl_yields_larger_quantity_with_constant_risk():
    """Tighter SL gives larger quantity, but total dollar risk at SL remains constant."""
    config = PositionSizeConfig(risk_amount_usd=100.0)

    res_wide = PositionSizingEngine.calculate(entry_price=100.0, stop_loss=90.0, config=config)
    res_tight = PositionSizingEngine.calculate(entry_price=100.0, stop_loss=98.0, config=config)

    assert res_wide is not None and res_tight is not None
    # Wide risk_r = 10 -> Qty = 10 units ($1,000 notional)
    assert res_wide.quantity == pytest.approx(10.0)
    # Tight risk_r = 2 -> Qty = 50 units ($5,000 notional)
    assert res_tight.quantity == pytest.approx(50.0)

    # Invariant: Both risk exactly $100 at SL
    assert res_wide.quantity * 10.0 == pytest.approx(100.0)
    assert res_tight.quantity * 2.0 == pytest.approx(100.0)


def test_disabled_engine_returns_none():
    """When position sizing is disabled, calculate returns None."""
    config = PositionSizeConfig(enabled=False)
    result = PositionSizingEngine.calculate(
        entry_price=100.0,
        stop_loss=95.0,
        config=config,
    )
    assert result is None


def test_edge_case_zero_sl_distance_returns_none():
    """When entry == stop_loss, avoid division by zero and return None."""
    config = PositionSizeConfig(enabled=True, risk_amount_usd=100.0)
    result = PositionSizingEngine.calculate(
        entry_price=100.0,
        stop_loss=100.0,
        config=config,
    )
    assert result is None


def test_edge_case_invalid_prices_returns_none():
    """When entry <= 0 or risk <= 0, return None."""
    config = PositionSizeConfig(enabled=True, risk_amount_usd=0.0)
    assert PositionSizingEngine.calculate(entry_price=100.0, stop_loss=90.0, config=config) is None
    assert PositionSizingEngine.calculate(entry_price=0.0, stop_loss=90.0) is None
    assert PositionSizingEngine.calculate(entry_price=-50.0, stop_loss=-60.0) is None


def test_formatting_magnitudes():
    """Verify precision rules for small, medium, and large quantities."""
    assert PositionSizingEngine.format_quantity(1542.84, "DOGE") == "1,542.8 DOGE"
    assert PositionSizingEngine.format_quantity(254.678, "SOL") == "254.68 SOL"
    assert PositionSizingEngine.format_quantity(15.456, "ETH") == "15.46 ETH"
    assert PositionSizingEngine.format_quantity(2.4567, "BTC") == "2.457 BTC"
    assert PositionSizingEngine.format_quantity(0.123456, "BTC") == "0.1235 BTC"
    assert PositionSizingEngine.format_quantity(0.0054321, "PAXG") == "0.005432 PAXG"


def test_telegram_snippet_formatting():
    """Verify formatted Telegram snippet string."""
    config = PositionSizeConfig(risk_amount_usd=100.0)
    result = PositionSizingEngine.calculate(
        entry_price=60000.0,
        stop_loss=59500.0,
        symbol="BTCUSDT",
        config=config,
    )
    snippet = PositionSizingEngine.format_telegram_snippet(result, "BTCUSDT")
    assert "<b>📦 Position Size:</b> <code>0.2000 BTC</code>" in snippet
    assert "$12,000.00 Notional @ $100.00 Risk" in snippet
