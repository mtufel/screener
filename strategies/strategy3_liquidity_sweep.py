"""
Strategy 3 adapter — Liquidity-Sweep FVG behind ``BaseStrategy``.

Registry name: ``liquidity_sweep_fvg``. Implements the video model
(4H FVG bias → liquidity sweep → LTF FVG entry → liquidity target) on top of
the shared 4H machinery, with data-proven gates. Strategy 2 files are untouched.
"""

from datetime import datetime
from typing import Any, Dict, List

from strategies.base import BaseStrategy
from strategies.registry import register


class _SetupAttrView:
    """Attribute view over the S3 engine's setup dict.

    The daemon payload builder (screener_cycle._extreme_setup_payload) reads
    setups via attribute access, matching S2's ``ExtremeTradeSetup`` dataclass.
    The S3 engine returns a plain dict, so the adapter wraps it once here.
    """

    def __init__(self, data: Dict[str, Any]):
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        try:
            return object.__getattribute__(self, "_data")[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    @property
    def risk_pct(self) -> float:
        return self._data.get("risk_pct", 0.0)

    @property
    def entry_time_ist(self) -> Any:
        """IST clock-close string of the entry bar, mirroring S2's setup property."""
        entry_ts = self._data.get("entry_timestamp")
        if not entry_ts:
            return None
        from candle_store import TIMEFRAME_MS
        from app_config import IST
        dur = TIMEFRAME_MS.get(self._data.get("ltf_timeframe", "5m"), 5 * 60 * 1000)
        return datetime.fromtimestamp((entry_ts + dur) / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")


def _normalize_setup(raw: Dict[str, Any]) -> _SetupAttrView:
    """Wraps an S3 engine setup dict and fills daemon-required defaults.

    The engine emits signal-level fields only; the ledger expects lifecycle
    defaults (state, risk_pct) present on S2's setup dataclass.
    """
    view = _SetupAttrView(raw)
    raw.setdefault("state", "PENDING_RETRACE")
    raw.setdefault("entry_timestamp", None)
    raw.setdefault("floating_r", 0.0)
    # The engine dict does not carry this field, but the daemon payload builder
    # (_extreme_setup_payload) reads it off the setup object.
    raw.setdefault("completion_target", "2R")
    entry_price = float(raw.get("entry_price") or 0.0)
    raw.setdefault(
        "risk_pct",
        (float(raw.get("risk_r", 0.0)) / entry_price * 100.0) if entry_price > 0 else 0.0,
    )
    # The ledger tracks tp_1r/tp_2r/tp_3r for display and 1R MFE logic; the
    # engine precomputes the same grid under "targets".
    targets = raw.get("targets") or {}
    for mult, key in ((1, "tp_1r"), (2, "tp_2r"), (3, "tp_3r")):
        raw.setdefault(f"tp_{mult}r", targets.get(f"{mult}R"))
    return view


@register
class Strategy3LiquiditySweepFVG(BaseStrategy):
    """Adapter for ``strategy_liquidity_sweep_fvg`` engine functions."""

    name = "liquidity_sweep_fvg"
    display_name = "Liquidity-Sweep FVG"
    description = "4H FVG Bias + Liquidity Sweep Gate + LTF FVG Entry + Liquidity-First Target"

    default_params = {
        # Core scanning params (mirror S2 defaults)
        "ltf_timeframe": "5m",
        "min_gap_pct": 0.05,
        "completion_target": "2R",
        "use_close_invalidation": False,
        # Gates
        "require_sweep": True,
        "sweep_max_age_h": 2.0,
        "gap_band_exclude": "0.10,0.20",
        "anchor_age_guard": True,
        # Entry-session gate: NY killzone 13:00-16:00 UTC carried all profit.
        "entry_session_filter": True,
        "entry_sessions": "NY_KZ",
        # Take-profit
        "tp_mode": "LIQUIDITY",
        "min_rr_for_liquidity": 1.5,
        "fallback_target_r": 2.0,
    }

    async def find_setups(self, symbol: str, provider: Any, params: Dict[str, Any]) -> List[Any]:
        from session_filter import SessionFilterConfig
        from strategy_liquidity_sweep_fvg import get_liquidity_sweep_setup_for_symbol

        session_config = SessionFilterConfig.from_legacy(
            entry_session_filter=params.get("entry_session_filter", False),
            entry_sessions=params.get("entry_sessions", "ALL"),
        )

        setup = await get_liquidity_sweep_setup_for_symbol(
            symbol=symbol,
            ltf_timeframe=params.get("ltf_timeframe", "5m"),
            client=provider,
            use_close_invalidation=params.get("use_close_invalidation", False),
            min_gap_pct=params.get("min_gap_pct", 0.05),
            completion_target=params.get("completion_target", "2R"),
            require_sweep=params.get("require_sweep", True),
            sweep_max_age_h=params.get("sweep_max_age_h", 2.0),
            gap_band_exclude=params.get("gap_band_exclude", "0.10,0.20"),
            anchor_age_guard=params.get("anchor_age_guard", True),
            tp_mode=params.get("tp_mode", "LIQUIDITY"),
            min_rr_for_liquidity=params.get("min_rr_for_liquidity", 1.5),
            fallback_target_r=params.get("fallback_target_r", 2.0),
            session_config=session_config,
        )
        return [_normalize_setup(setup)] if setup is not None else []

    async def backtest(self, symbol: str, days: int, provider: Any, params: Dict[str, Any]) -> Any:
        from backtest_liquidity_sweep_fvg import run_liquidity_sweep_backtest

        return await run_liquidity_sweep_backtest(
            symbol=symbol,
            days=days,
            ltf_timeframe=params.get("ltf_timeframe", "5m"),
            use_close_invalidation=params.get("use_close_invalidation", False),
            min_gap_pct=params.get("min_gap_pct", 0.05),
            require_sweep=params.get("require_sweep", True),
            sweep_max_age_h=params.get("sweep_max_age_h", 2.0),
            gap_band_exclude=params.get("gap_band_exclude", "0.10,0.20"),
            anchor_age_guard=params.get("anchor_age_guard", True),
            tp_mode=params.get("tp_mode", "LIQUIDITY"),
            min_rr_for_liquidity=params.get("min_rr_for_liquidity", 1.5),
            fallback_target_r=params.get("fallback_target_r", 2.0),
            entry_sessions=params.get("entry_sessions", "NY_KZ"),
            entry_weekday_only=params.get("entry_weekday_only", True),
            client=provider,
        )
