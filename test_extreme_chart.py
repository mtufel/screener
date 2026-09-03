"""
Unit and Integration tests for Strategy 2 TradingView chart generation and API endpoint.
"""

import pytest
from fastapi.testclient import TestClient

from chart_generator import generate_extreme_setup_chart
from strategy_extreme_fvg import Candle
from main import app


def test_generate_extreme_setup_chart_direct():
    candles = [
        Candle(timestamp=1000 + i * 900000, open=50000 + i * 10, high=50050 + i * 10, low=49950 + i * 10, close=50020 + i * 10, volume=100.0)
        for i in range(40)
    ]
    img_bytes = generate_extreme_setup_chart(
        symbol="BTC",
        direction="Bullish",
        candles_ltf=candles,
        htf_fvg_bottom=49900.0,
        htf_fvg_top=50100.0,
        htf_first_touch_ist="03-Sep 08:30 AM IST",
        ltf_fvg_bottom=50050.0,
        ltf_fvg_top=50150.0,
        ltf_fvg_formed_ts=1000 + 20 * 900000,
        entry_price=50150.0,
        stop_loss=49950.0,
        tp_1r=50350.0,
        tp_2r=50550.0,
        tp_3r=50750.0,
        state="PENDING_RETRACE",
        floating_r=0.0,
        ltf_timeframe="15m",
    )
    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 1000
    assert img_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_api_extreme_chart_endpoint():
    client = TestClient(app)
    params = {
        "symbol": "BTC",
        "direction": "Bullish",
        "ltf": "15m",
        "entry_price": "60000.0",
        "stop_loss": "59500.0",
        "tp_1r": "60500.0",
        "tp_2r": "61000.0",
        "tp_3r": "61500.0",
        "htf_bottom": "59000.0",
        "htf_top": "60200.0",
        "ltf_bottom": "59800.0",
        "ltf_top": "60000.0",
        "ltf_formed_ts": "1700000000000",
        "htf_first_touch_ist": "03-Sep 08:00 AM IST",
        "state": "TRADE_ACTIVE",
        "floating_r": "1.45",
    }
    resp = client.get("/api/extreme/chart", params=params)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"
