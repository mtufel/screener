"""
Replay market-data provider (replay_provider.py).

Implements ``BaseMarketDataProvider`` over a pre-fetched historical dataset so
the LIVE screener cycle can be executed against past data with zero lookahead:
candles are only served once their close time is at or before the (virtual)
current time. This is the "only the data feed changes" seam between live and
backtest (openspec change `unified-strategy-ui-replay-backtest`, D2).
"""

import logging
from datetime import timezone
from typing import Any, Dict, List, Optional

from clock import now_ms
from market_data.base import BaseMarketDataProvider
from strategy_extreme_fvg import Candle, TIMEFRAME_MS


def _candle_to_dict(c: Candle) -> dict:
    """Serializes a Candle to the wire format (t/o/h/l/c/v) used across the codebase."""
    return {"t": c.timestamp, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume}

logger = logging.getLogger("replay-provider")

# Warmup margin fetched before the simulation window so 4H anchors, swing
# maps, and engine caches are warm at simulation start.
WARMUP_DAYS_4H = 20
WARMUP_MS_LTF = 6 * 3_600_000  # 6h of extra LTF candles


async def build_replay_dataset(
    symbols: List[str],
    days: int,
    ltf_timeframe: str,
    provider: BaseMarketDataProvider,
) -> Dict[str, Dict[str, List[Candle]]]:
    """Fetches historical candles for the replay window (plus warmup) once up front.

    Returns ``{symbol: {"ltf": [Candle...], "4h": [Candle...]}}`` sorted
    oldest-first. Raises RuntimeError when a symbol has insufficient data so the
    caller can fail the replay before the virtual clock is installed.
    """
    end_ms = now_ms()
    start_ms = end_ms - days * 86_400_000
    dataset: Dict[str, Dict[str, List[Candle]]] = {}
    for sym in symbols:
        raw_4h = await _fetch_range(
            provider, sym, "4h", start_ms - WARMUP_DAYS_4H * 86_400_000, end_ms
        )
        raw_ltf = await _fetch_range(
            provider, sym, ltf_timeframe, start_ms - WARMUP_MS_LTF, end_ms
        )
        if len(raw_4h) < 30 or len(raw_ltf) < 30:
            raise RuntimeError(
                f"Insufficient historical data for {sym} "
                f"(4h={len(raw_4h)}, {ltf_timeframe}={len(raw_ltf)}); cannot build replay dataset"
            )
        dataset[sym] = {
            "4h": sorted(raw_4h, key=lambda c: c.timestamp),
            "ltf": sorted(raw_ltf, key=lambda c: c.timestamp),
        }
        logger.info(
            "[ReplayDataset] %s: %d 4H candles, %d %s candles (%.1fd window)",
            sym, len(raw_4h), len(raw_ltf), ltf_timeframe, days,
        )
    return dataset


async def _fetch_range(
    provider: BaseMarketDataProvider,
    symbol: str,
    interval: str,
    start_ms: float,
    end_ms: float,
) -> List[Candle]:
    raw = await provider.get_historical_candles_range(
        coin=symbol,
        interval=interval,
        start_time_ms=int(start_ms),
        end_time_ms=int(end_ms),
    )
    return [Candle.from_dict(c) if isinstance(c, dict) else c for c in (raw or [])]


class ReplayMarketDataProvider(BaseMarketDataProvider):
    """Serves the pre-fetched dataset strictly as-of the current (virtual) time."""

    def __init__(self, dataset: Dict[str, Dict[str, List[Candle]]], ltf_timeframe: str = "5m"):
        self._dataset = dataset
        self._ltf_timeframe = ltf_timeframe

    # -- BaseMarketDataProvider interface -------------------------------------

    @property
    def name(self) -> str:
        return "replay"

    def resolve_symbol(self, raw_symbol: str) -> str:
        return raw_symbol.strip().upper()

    async def get_last_n_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        n: int = 200,
    ) -> List[Dict[str, Any]]:
        candles = self._candles_asof(symbol, timeframe)
        return [_candle_to_dict(c) for c in candles[-n:]]

    async def get_historical_candles_range(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Dict[str, Any]]:
        """Serves in-memory candles in range, capped at the current time (chart modal path)."""
        candles = self._candles_asof(coin, interval)
        return [
            _candle_to_dict(c)
            for c in candles
            if c.timestamp >= start_time_ms and c.timestamp <= end_time_ms
        ]

    async def get_all_mids(self) -> Dict[str, float]:
        """Synthesizes mids from each symbol's replay-head close (the same value the engines compute as current_price)."""
        mids: Dict[str, float] = {}
        for sym in self._dataset:
            close = self.head_close(sym)
            if close > 0:
                mids[sym] = close
        return mids

    async def get_universe_coins(self, min_volume: float = 0.0) -> List[str]:
        return sorted(self._dataset.keys())

    async def close(self):
        return None

    # -- Replay-specific helpers ----------------------------------------------

    def _candles_asof(self, symbol: str, timeframe: str) -> List[Candle]:
        series = self._dataset.get(symbol.strip().upper())
        if not series:
            return []
        key = "4h" if timeframe.strip().lower() in ("4h", "240m") else "ltf"
        candles = series.get(key, [])
        if not candles:
            return []
        dur = TIMEFRAME_MS.get(
            timeframe.strip().lower(),
            TIMEFRAME_MS.get(self._ltf_timeframe, 300_000),
        )
        cutoff = now_ms()
        # Only candles that have fully closed by the current time (no lookahead).
        return [c for c in candles if (c.timestamp + dur) <= cutoff]

    def head_close(self, symbol: str) -> float:
        candles = self._candles_asof(symbol, self._ltf_timeframe)
        return candles[-1].close if candles else 0.0

    def next_close_boundary(self, timeframe: str) -> Optional[int]:
        """Earliest close time strictly after the current time across all symbols (drives the replay loop)."""
        key = "4h" if timeframe.strip().lower() in ("4h", "240m") else "ltf"
        dur = TIMEFRAME_MS.get(timeframe.strip().lower(), 300_000)
        cutoff = now_ms()
        boundaries = [
            c.timestamp + dur
            for series in self._dataset.values()
            for c in series.get(key, [])
            if (c.timestamp + dur) > cutoff
        ]
        return min(boundaries) if boundaries else None
