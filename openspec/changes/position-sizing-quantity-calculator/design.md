# OpenSpec Design: Configurable Position Sizing & Quantity Calculation Engine

## Architecture & Class Design

### 1. Dedicated Module: `position_sizing.py`
```
┌─────────────────────────────────────────────────────────────┐
│                      position_sizing.py                     │
│                                                             │
│   ┌─────────────────────┐       ┌───────────────────────┐   │
│   │ PositionSizeConfig  │       │  PositionSizeResult   │   │
│   ├─────────────────────┤       ├───────────────────────┤   │
│   │ enabled: bool       │       │ quantity: float       │   │
│   │ risk_mode: str      │       │ quantity_formatted    │   │
│   │ risk_amount_usd     │       │ notional_usd: float   │   │
│   │ account_equity_usd  │       │ risk_usd: float       │   │
│   │ risk_percent: float │       │ pnl_1r_usd: float     │   │
│   └──────────┬──────────┘       │ pnl_2r_usd: float     │   │
│              │                  │ pnl_3r_usd: float     │   │
│              ▼                  └───────────▲───────────┘   │
│   ┌─────────────────────────────────────────┴───────────┐   │
│   │                PositionSizingEngine                 │   │
│   ├─────────────────────────────────────────────────────┤   │
│   │ + calculate(entry, sl, symbol, config)              │   │
│   │ + format_telegram_snippet(result, symbol)           │   │
│   │ + format_quantity_precision(quantity, symbol)      │   │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 2. Sizing Formulae
1. **SL Distance**:
   $$\text{risk\_r} = |Entry - SL|$$
2. **Planned Risk (USD)**:
   $$\text{risk\_usd} = \begin{cases} \text{risk\_amount\_usd} & \text{if mode} = \text{"fixed\_amount"} \\ \text{account\_equity\_usd} \times \frac{\text{risk\_percent}}{100} & \text{if mode} = \text{"percent\_equity"} \end{cases}$$
3. **Quantity**:
   $$\text{quantity} = \frac{\text{risk\_usd}}{\text{risk\_r}}$$
4. **Notional Value**:
   $$\text{notional\_usd} = \text{quantity} \times Entry$$
5. **Expected PnL**:
   - $\text{loss\_at\_sl\_usd} = -\text{risk\_usd}$
   - $\text{pnl\_1r\_usd} = +1.0 \times \text{risk\_usd}$
   - $\text{pnl\_2r\_usd} = +2.0 \times \text{risk\_usd}$
   - $\text{pnl\_3r\_usd} = +3.0 \times \text{risk\_usd}$

### 3. Precision Rules
Dynamic precision formatting based on magnitude:
- $\text{quantity} \ge 100$: 1 decimal place (e.g. `150.0 DOGE`)
- $10 \le \text{quantity} < 100$: 2 decimal places (e.g. `25.50 SOL`)
- $1 \le \text{quantity} < 10$: 3 decimal places (e.g. `2.450 ETH`)
- $0.01 \le \text{quantity} < 1$: 4 decimal places (e.g. `0.1523 BTC`)
- $\text{quantity} < 0.01$: up to 6 significant digits.
