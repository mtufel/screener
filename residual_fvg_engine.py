"""
Modular Object-Oriented Residual FVG Engine.

Provides clean OOP abstractions for:
1. `ResidualFVGEngine`: Pure factory and mathematical domain model for residual Fair Value Gaps.
2. `TradeLifecycleEvaluator`: State machine evaluating trade progression, adverse wick tracking
   from trade active to trade close, and seamless residual setup transition.
"""

from typing import Any, Dict, List, Literal, Optional, Tuple, TYPE_CHECKING
import copy

if TYPE_CHECKING:
    from strategy_extreme_fvg import Candle, FVG


class ResidualFVGEngine:
    """
    Pure OOP Domain Engine for Fair Value Gap shrinkage and residual calculations.
    """

    @staticmethod
    def create_residual(
        fvg: "FVG",
        deepest_wick: float,
        min_gap_pct: float = 0.05,
    ) -> Optional["FVG"]:
        """
        Creates a new immutable residual FVG resulting from partial mitigation.
        - Bullish: top shrinks down to deepest_wick (lowest adverse wick during trade).
        - Bearish: bottom rises up to deepest_wick (highest adverse wick during trade).
        - Structural Stop Loss invariant: min(c1.l, c2.l, c3.l) / max(c1.h, c2.h, c3.h) is 100% preserved.
        - Returns None if full breach occurred or if residual gap_pct < min_gap_pct.
        """
        if fvg is None:
            return None

        # Deepest wick cannot be 0.0 or inverted
        if deepest_wick <= 0.0:
            return None

        from strategy_extreme_fvg import FVG

        direction = fvg.direction
        orig_top = fvg.original_top if fvg.original_top is not None else fvg.top
        orig_bottom = fvg.original_bottom if fvg.original_bottom is not None else fvg.bottom

        if direction == "Bullish":
            # For Bullish: adverse price moves downwards into the gap.
            # Residual gap is between bottom and deepest_wick.
            new_top = min(fvg.top, deepest_wick)
            new_bottom = fvg.bottom

            # If deepest wick reached or breached bottom, FVG is fully mitigated
            if new_top <= new_bottom:
                return None

            midpoint = (new_top + new_bottom) / 2.0
            gap_pct = ((new_top - new_bottom) / midpoint) * 100.0 if midpoint > 0 else 0.0
            if gap_pct < min_gap_pct:
                return None

            return FVG(
                direction="Bullish",
                top=new_top,
                bottom=new_bottom,
                c1=fvg.c1,
                c2=fvg.c2,
                c3=fvg.c3,
                formed_at=fvg.formed_at,
                is_valid=True,
                timeframe=fvg.timeframe,
                lifecycle_state="PENDING_RETRACE",
                entry_timestamp=None,
                floating_r=0.0,
                original_top=orig_top,
                original_bottom=orig_bottom,
                mitigation_count=fvg.mitigation_count + 1,
                deepest_wick_penetration=deepest_wick,
            )
        else:
            # For Bearish: adverse price moves upwards into the gap.
            # Residual gap is between deepest_wick and top.
            new_top = fvg.top
            new_bottom = max(fvg.bottom, deepest_wick)

            # If deepest wick reached or breached top, FVG is fully mitigated
            if new_bottom >= new_top:
                return None

            midpoint = (new_top + new_bottom) / 2.0
            gap_pct = ((new_top - new_bottom) / midpoint) * 100.0 if midpoint > 0 else 0.0
            if gap_pct < min_gap_pct:
                return None

            return FVG(
                direction="Bearish",
                top=new_top,
                bottom=new_bottom,
                c1=fvg.c1,
                c2=fvg.c2,
                c3=fvg.c3,
                formed_at=fvg.formed_at,
                is_valid=True,
                timeframe=fvg.timeframe,
                lifecycle_state="PENDING_RETRACE",
                entry_timestamp=None,
                floating_r=0.0,
                original_top=orig_top,
                original_bottom=orig_bottom,
                mitigation_count=fvg.mitigation_count + 1,
                deepest_wick_penetration=deepest_wick,
            )


class TradeLifecycleEvaluator:
    """
    OOP State Machine evaluating the lifecycle of candidate and residual FVGs.
    Tracks state transitions, floating R, and adverse wick strictly from trade active to trade close.
    """

    def __init__(
        self,
        fvg: "FVG",
        completion_target: Literal["1R", "2R", "3R"] = "2R",
        min_gap_pct: float = 0.05,
        partial_mitigation: bool = True,
    ):
        self.initial_fvg = fvg
        self.active_fvg = fvg
        self.completion_target = completion_target
        self.min_gap_pct = min_gap_pct
        self.partial_mitigation = partial_mitigation

        self.state: str = "PENDING_RETRACE"
        self.entry_timestamp: Optional[int] = None
        self.exit_timestamp: Optional[int] = None
        self.deepest_adverse_wick: Optional[float] = None
        self.floating_r: float = 0.0
        self.mult: float = 1.0 if completion_target == "1R" else (2.0 if completion_target == "2R" else 3.0)

    @property
    def direction(self) -> str:
        return self.active_fvg.direction

    @property
    def stop_loss(self) -> float:
        """Structural Stop Loss invariant derived from the base 3-candle sequence."""
        c1, c2, c3 = self.active_fvg.c1, self.active_fvg.c2, self.active_fvg.c3
        if self.direction == "Bullish":
            return min(c1.low, c2.low, c3.low)
        else:
            return max(c1.high, c2.high, c3.high)

    @property
    def entry_price(self) -> float:
        """Limit entry price at the outer boundary of the currently active FVG."""
        return self.active_fvg.top if self.direction == "Bullish" else self.active_fvg.bottom

    @property
    def risk_r(self) -> float:
        """Distance from entry price to invariant structural Stop Loss."""
        if self.direction == "Bullish":
            return max(0.0, self.entry_price - self.stop_loss)
        else:
            return max(0.0, self.stop_loss - self.entry_price)

    @property
    def tp_target(self) -> float:
        """Profit target price based on completion_target multiplier."""
        if self.direction == "Bullish":
            return self.entry_price + self.mult * self.risk_r
        else:
            return self.entry_price - self.mult * self.risk_r

    def process_candle(self, c: "Candle") -> bool:
        """
        Processes a single candle forward in chronological time.
        Updates state and continuously tracks adverse wick from active to close.
        Returns True if lifecycle should continue evaluating subsequent candles, False if terminal.
        """
        if self.risk_r <= 0:
            self.state = "INVALIDATED"
            return False

        sl = self.stop_loss
        entry_px = self.entry_price
        tp = self.tp_target
        is_bullish = (self.direction == "Bullish")

        if self.state == "PENDING_RETRACE":
            if is_bullish:
                # Pre-entry invalidation
                if c.low <= sl and c.high < entry_px:
                    self.state = "INVALIDATED"
                    return False

                # Entry touch
                if c.low <= entry_px:
                    self.state = "TRADE_ACTIVE"
                    self.entry_timestamp = c.timestamp
                    # Initialize adverse wick from entry fill to candle low
                    self.deepest_adverse_wick = min(entry_px, c.low)

                    # Same candle SL
                    if c.low <= sl:
                        self.state = "STOPPED_OUT"
                        self.exit_timestamp = c.timestamp
                        self.floating_r = -1.0
                        return False

                    # Same candle TP
                    if c.high >= tp:
                        self.deepest_adverse_wick = min(self.deepest_adverse_wick, c.low)
                        self.exit_timestamp = c.timestamp
                        self.floating_r = self.mult
                        return self._handle_tp_reached()
            else:
                # Bearish pre-entry invalidation
                if c.high >= sl and c.low > entry_px:
                    self.state = "INVALIDATED"
                    return False

                # Entry touch
                if c.high >= entry_px:
                    self.state = "TRADE_ACTIVE"
                    self.entry_timestamp = c.timestamp
                    # Initialize adverse wick from entry fill to candle high
                    self.deepest_adverse_wick = max(entry_px, c.high)

                    # Same candle SL
                    if c.high >= sl:
                        self.state = "STOPPED_OUT"
                        self.exit_timestamp = c.timestamp
                        self.floating_r = -1.0
                        return False

                    # Same candle TP
                    if c.low <= tp:
                        self.deepest_adverse_wick = max(self.deepest_adverse_wick, c.high)
                        self.exit_timestamp = c.timestamp
                        self.floating_r = self.mult
                        return self._handle_tp_reached()

            return True

        elif self.state == "TRADE_ACTIVE":
            if is_bullish:
                # Track adverse wick while active
                self.deepest_adverse_wick = min(
                    self.deepest_adverse_wick if self.deepest_adverse_wick is not None else entry_px,
                    c.low,
                )

                # SL check
                if c.low <= sl:
                    self.state = "STOPPED_OUT"
                    self.exit_timestamp = c.timestamp
                    self.floating_r = -1.0
                    return False

                # TP check (candle close incorporated in deepest_adverse_wick above)
                if c.high >= tp:
                    self.exit_timestamp = c.timestamp
                    self.floating_r = self.mult
                    return self._handle_tp_reached()
            else:
                # Bearish track adverse wick while active
                self.deepest_adverse_wick = max(
                    self.deepest_adverse_wick if self.deepest_adverse_wick is not None else entry_px,
                    c.high,
                )

                # SL check
                if c.high >= sl:
                    self.state = "STOPPED_OUT"
                    self.exit_timestamp = c.timestamp
                    self.floating_r = -1.0
                    return False

                # TP check (candle close incorporated in deepest_adverse_wick above)
                if c.low <= tp:
                    self.exit_timestamp = c.timestamp
                    self.floating_r = self.mult
                    return self._handle_tp_reached()

            return True

        return False

    def _handle_tp_reached(self) -> bool:
        """Handles TP resolution, generating residual FVG if enabled."""
        if not self.partial_mitigation:
            self.state = "COMPLETED"
            return False

        # Compute residual FVG from deepest adverse wick measured active-to-close
        adv_wick = self.deepest_adverse_wick if self.deepest_adverse_wick is not None else self.entry_price
        residual = ResidualFVGEngine.create_residual(
            fvg=self.active_fvg,
            deepest_wick=adv_wick,
            min_gap_pct=self.min_gap_pct,
        )

        if residual is not None:
            # Transition active FVG to residual, state resets to PENDING_RETRACE for remaining candles
            self.active_fvg = residual
            self.state = "PENDING_RETRACE"
            self.entry_timestamp = None
            self.deepest_adverse_wick = None
            self.floating_r = 0.0
            return True
        else:
            self.state = "COMPLETED"
            return False

    def process_live_price(self, current_price: float):
        """Evaluates latest live ticker price against active trade state."""
        if current_price <= 0.0:
            return

        import time
        sl = self.stop_loss
        entry_px = self.entry_price
        tp = self.tp_target
        r_dist = self.risk_r
        is_bullish = (self.direction == "Bullish")

        if self.state == "PENDING_RETRACE":
            if is_bullish:
                if current_price <= sl:
                    self.state = "INVALIDATED"
                elif current_price <= entry_px:
                    self.state = "TRADE_ACTIVE"
                    self.entry_timestamp = int(time.time() * 1000)
                    self.deepest_adverse_wick = min(entry_px, current_price)
                    self.floating_r = (current_price - entry_px) / r_dist if r_dist > 0 else 0.0
            else:
                if current_price >= sl:
                    self.state = "INVALIDATED"
                elif current_price >= entry_px:
                    self.state = "TRADE_ACTIVE"
                    self.entry_timestamp = int(time.time() * 1000)
                    self.deepest_adverse_wick = max(entry_px, current_price)
                    self.floating_r = (entry_px - current_price) / r_dist if r_dist > 0 else 0.0

        elif self.state == "TRADE_ACTIVE":
            if is_bullish:
                self.deepest_adverse_wick = min(
                    self.deepest_adverse_wick if self.deepest_adverse_wick is not None else entry_px,
                    current_price,
                )
                if current_price <= sl:
                    self.state = "STOPPED_OUT"
                    self.floating_r = -1.0
                elif current_price >= tp:
                    self.floating_r = self.mult
                    self._handle_tp_reached()
                else:
                    self.floating_r = (current_price - entry_px) / r_dist if r_dist > 0 else 0.0
            else:
                self.deepest_adverse_wick = max(
                    self.deepest_adverse_wick if self.deepest_adverse_wick is not None else entry_px,
                    current_price,
                )
                if current_price >= sl:
                    self.state = "STOPPED_OUT"
                    self.floating_r = -1.0
                elif current_price <= tp:
                    self.floating_r = self.mult
                    self._handle_tp_reached()
                else:
                    self.floating_r = (entry_px - current_price) / r_dist if r_dist > 0 else 0.0

    def evaluate(
        self,
        subsequent_candles: List["Candle"],
        current_price: float = 0.0,
    ) -> Tuple[str, Optional[int], float, Optional["FVG"]]:
        """
        Evaluates the full sequence of candles and live price.
        Returns: (state, entry_timestamp, floating_r, active_fvg)
        """
        for c in subsequent_candles:
            if not self.process_candle(c):
                break

        if self.state in ("PENDING_RETRACE", "TRADE_ACTIVE") and current_price > 0.0:
            self.process_live_price(current_price)

        return (
            self.state,
            self.entry_timestamp,
            self.floating_r,
            self.active_fvg if self.state in ("PENDING_RETRACE", "TRADE_ACTIVE") else None,
        )
