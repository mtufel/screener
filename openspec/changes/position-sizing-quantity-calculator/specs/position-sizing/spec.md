# OpenSpec Specification: Position Sizing & Quantity Calculation

## Invariants & Testable Requirements

### Requirement 1: Risk-to-Quantity Inverse Relation
- For any setup with $Entry$ and $SL$ ($|Entry - SL| > 0$), `quantity` MUST equal $\frac{\text{risk\_usd}}{\text{risk\_r}}$.
- Tighter SL distance MUST yield a proportionally larger quantity, while wider SL distance MUST yield a smaller quantity, such that total monetary risk at Stop Loss remains strictly constant ($\text{quantity} \times |Entry - SL| == \text{risk\_usd}$).

### Requirement 2: Fixed vs Percentage Equity Modes
- In `fixed_amount` mode, `risk_usd` MUST equal `config.risk_amount_usd`.
- In `percent_equity` mode, `risk_usd` MUST equal $\text{config.account_equity\_usd} \times \frac{\text{config.risk\_percent}}{100}$.

### Requirement 3: Edge Case Safety & Invalidation Protection
- If $|Entry - SL| \le 0$ or $Entry \le 0$, `PositionSizingEngine.calculate()` MUST return `None` or safe 0 values without raising exceptions (ZeroDivisionError protection).
- If `config.enabled == False`, `calculate()` MUST return `None`.

### Requirement 4: Notional & Multi-Target PnL Consistency
- `notional_usd` MUST equal $\text{quantity} \times Entry$.
- `loss_at_sl_usd` MUST equal $-\text{risk\_usd}$.
- `pnl_1r_usd`, `pnl_2r_usd`, and `pnl_3r_usd` MUST equal $+1.0 \times \text{risk\_usd}$, $+2.0 \times \text{risk\_usd}$, and $+3.0 \times \text{risk\_usd}$ respectively.

### Requirement 5: Symbol Precision Formatting
- Formatted quantity string MUST correctly represent asset scale without floating-point representation artifacts.
