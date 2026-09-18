"""Shared domain types for Strategy 2 (after Strategy 1 removal)."""
from datetime import timezone, timedelta
from typing import List, Optional, Dict, Any

IST = timezone(timedelta(hours=5, minutes=30))

TIMEFRAME_MS = {
    "1m": 60 * 1000, "3m": 3 * 60 * 1000, "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000, "30m": 30 * 60 * 1000, "1h": 3600 * 1000,
    "4h": 4 * 3600 * 1000, "1d": 24 * 3600 * 1000,
}

class Candle:
    def __init__(self, timestamp: int, open: float, high: float, low: float,
                 close: float, volume: float = 0, vwap: Optional[float] = None):
        self.timestamp = timestamp; self.open = open; self.high = high
        self.low = low; self.close = close; self.volume = volume; self.vwap = vwap
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Candle":
        return cls(d.get("t") or d.get("timestamp"), d.get("o") or d.get("open"),
                   d.get("h") or d.get("high"), d.get("l") or d.get("low"),
                   d.get("c") or d.get("close"), d.get("v") or d.get("volume", 0))
    def to_dict(self) -> Dict[str, Any]:
        return {"t": self.timestamp, "o": self.open, "h": self.high,
                "l": self.low, "c": self.close, "v": self.volume}

class FVG:
    def __init__(self, top: float, bottom: float, formed_at: int,
                 direction: str = "bullish", htf_anchor: Optional[float] = None,
                 gap_pct: float = 0.0):
        self.top = top; self.bottom = bottom; self.formed_at = formed_at
        self.direction = direction; self.htf_anchor = htf_anchor; self.gap_pct = gap_pct
