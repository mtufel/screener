# OpenSpec Proposal: Configurable Position Sizing & Quantity Calculation Engine

## 1. Problem Statement
Traders executing FVG setups need to know the exact position quantity (e.g. 0.150 BTC, 2.45 ETH) to trade based on their predefined risk parameters (e.g. $100 fixed risk or 1% account risk) and the specific Stop Loss distance of the setup. Currently, setups output entry, SL, and risk in points/percentages, requiring manual calculation of order size.

## 2. Proposed Solution
Implement a dedicated, modular Object-Oriented `PositionSizingEngine` that dynamically calculates:
- Recommended Quantity of the base asset based on $|Entry - SL|$.
- Total Notional USD value of the position ($Quantity \times Entry$).
- Projected USD loss at Stop Loss and projected USD gains at Take Profit levels (1R, 2R, 3R).
- Formatted quantity with dynamic precision suitable for crypto assets.
- Expose this feature cleanly in Telegram alerts, Web UI trade cards, and API responses, with an enable/disable toggle.

## 3. Impact & Benefits
- Zero guesswork on trade execution size.
- Strictly adheres to user risk limits (e.g. never lose more than $100 on an SL hit).
- Pure OOP modular design: isolated in `position_sizing.py` without cluttering core strategy files.
- Fully configurable via `.env`, API parameters, and Web UI.
