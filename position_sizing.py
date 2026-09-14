"""
Position Sizing & Quantity Calculation Engine.

Calculates recommended position quantity and notional value based on:
1. Entry Price and Stop Loss distance (|Entry - SL| = risk_r).
2. User-defined Risk Parameters:
   - Fixed USD risk (e.g. $100 per trade), OR
   - Percentage of Account Equity (e.g. 1% of $10,000 = $100).
"""

from dataclasses import dataclass, asdict
import os
from typing import Dict, Any, Optional, Literal


@dataclass
class PositionSizeConfig:
    """Configuration for position sizing calculations."""
    enabled: bool = True
    risk_mode: Literal["fixed_amount", "percent_equity"] = "fixed_amount"
    risk_amount_usd: float = 100.0
    account_equity_usd: float = 10000.0
    risk_percent: float = 1.0

    @classmethod
    def from_env(cls) -> "PositionSizeConfig":
        """Factory initializing config from environment variables."""
        enabled_str = os.getenv("POSITION_SIZING_ENABLED", "true").lower()
        enabled = enabled_str in ("1", "true", "yes")

        mode = os.getenv("POSITION_SIZING_RISK_MODE", "fixed_amount").lower()
        if mode not in ("fixed_amount", "percent_equity"):
            mode = "fixed_amount"

        try:
            risk_amount = float(os.getenv("POSITION_SIZING_RISK_AMOUNT_USD", "100.0"))
        except ValueError:
            risk_amount = 100.0

        try:
            equity = float(os.getenv("POSITION_SIZING_ACCOUNT_EQUITY_USD", "10000.0"))
        except ValueError:
            equity = 10000.0

        try:
            risk_pct = float(os.getenv("POSITION_SIZING_RISK_PERCENT", "1.0"))
        except ValueError:
            risk_pct = 1.0

        return cls(
            enabled=enabled,
            risk_mode=mode,  # type: ignore[arg-type]
            risk_amount_usd=max(0.0, risk_amount),
            account_equity_usd=max(0.0, equity),
            risk_percent=max(0.0, risk_pct),
        )


@dataclass
class PositionSizeResult:
    """Calculated position sizing results for a trade setup."""
    quantity: float
    quantity_formatted: str
    notional_usd: float
    risk_usd: float
    loss_at_sl_usd: float
    pnl_1r_usd: float
    pnl_2r_usd: float
    pnl_3r_usd: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quantity": round(self.quantity, 6),
            "quantity_formatted": self.quantity_formatted,
            "notional_usd": round(self.notional_usd, 2),
            "risk_usd": round(self.risk_usd, 2),
            "loss_at_sl_usd": round(self.loss_at_sl_usd, 2),
            "pnl_1r_usd": round(self.pnl_1r_usd, 2),
            "pnl_2r_usd": round(self.pnl_2r_usd, 2),
            "pnl_3r_usd": round(self.pnl_3r_usd, 2),
        }


class PositionSizingEngine:
    """
    Object-Oriented Engine for position sizing calculations and formatting.
    """

    @staticmethod
    def format_quantity(quantity: float, symbol: Optional[str] = None) -> str:
        """
        Formats quantity with dynamic, human-friendly precision.
        """
        if quantity <= 0:
            return "0.00"

        # Symbol clean up (e.g. BTCUSDT -> BTC)
        asset = ""
        if symbol:
            clean = symbol.upper().replace("USDT", "").replace("USD", "").replace("-PERP", "")
            asset = f" {clean}"

        if quantity >= 1000:
            return f"{quantity:,.1f}{asset}"
        elif quantity >= 100:
            return f"{quantity:,.2f}{asset}"
        elif quantity >= 10:
            return f"{quantity:.2f}{asset}"
        elif quantity >= 1:
            return f"{quantity:.3f}{asset}"
        elif quantity >= 0.01:
            return f"{quantity:.4f}{asset}"
        else:
            return f"{quantity:.6f}{asset}"

    @classmethod
    def calculate(
        cls,
        entry_price: float,
        stop_loss: float,
        symbol: Optional[str] = None,
        config: Optional[PositionSizeConfig] = None,
    ) -> Optional[PositionSizeResult]:
        """
        Calculates position size given entry price, stop loss, and risk configuration.
        Returns None if disabled or if invalid price inputs (e.g. entry <= 0 or SL distance <= 0).
        """
        active_config = config or PositionSizeConfig.from_env()

        if not active_config.enabled:
            return None

        if entry_price <= 0:
            return None

        risk_r = abs(entry_price - stop_loss)
        if risk_r <= 0:
            return None

        # Determine target dollar risk
        if active_config.risk_mode == "percent_equity":
            risk_usd = (active_config.account_equity_usd * (active_config.risk_percent / 100.0))
        else:
            risk_usd = active_config.risk_amount_usd

        if risk_usd <= 0:
            return None

        # Quantity = Risk USD / (SL Distance in USD per unit)
        quantity = risk_usd / risk_r
        notional_usd = quantity * entry_price

        # PnL targets in USD
        loss_at_sl_usd = -risk_usd
        pnl_1r_usd = risk_usd * 1.0
        pnl_2r_usd = risk_usd * 2.0
        pnl_3r_usd = risk_usd * 3.0

        qty_formatted = cls.format_quantity(quantity, symbol)

        return PositionSizeResult(
            quantity=quantity,
            quantity_formatted=qty_formatted,
            notional_usd=notional_usd,
            risk_usd=risk_usd,
            loss_at_sl_usd=loss_at_sl_usd,
            pnl_1r_usd=pnl_1r_usd,
            pnl_2r_usd=pnl_2r_usd,
            pnl_3r_usd=pnl_3r_usd,
        )

    @classmethod
    def format_telegram_snippet(
        cls,
        result: PositionSizeResult,
        symbol: Optional[str] = None,
    ) -> str:
        """Formats position sizing into a concise Telegram alert line."""
        if not result:
            return ""
        return (
            f"<b>📦 Position Size:</b> <code>{result.quantity_formatted}</code> "
            f"(${result.notional_usd:,.2f} Notional @ ${result.risk_usd:.2f} Risk)"
        )
