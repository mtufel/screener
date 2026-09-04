# Strategy 2: ⚡ Extreme LTF FVG Strategy Specification Delta

## MODIFIED Requirements

### Requirement: Execution Parameters & Target Matrix
The system SHALL place the entry price at the outer FVG boundary, stop loss at the extreme 3-candle wick $\min(c_1.l, c_2.l, c_3.l)$ for Bullish and $\max(c_1.h, c_2.h, c_3.h)$ for Bearish, and calculate 1R, 2R (Primary $\star$), and 3R targets.

* **Backtest Fill-Candle Evaluation**: The backtest forward-simulation MUST include the entry/fill candle itself in exit resolution, evaluating the fill candle's high/low against TP and stop levels exactly as the live ledger does (candles where `timestamp >= entry_timestamp`).

#### Scenario: Bullish parameter calculation
- **WHEN** a Bullish LTF FVG is selected with top at $60,000 and 3-candle lowest wick at $59,000
- **THEN** the entry price SHALL be $60,000, stop loss SHALL be $59,000 (Risk: $1,000)
- **AND** targets SHALL be set at $61,000 (1R), $62,000 (2R), and $63,000 (3R).

#### Scenario: Backtest same-bar fill and TP resolution
* **GIVEN** a Bullish LTF FVG with entry at $100 and 2R target at $120
* **WHEN** the candle that first touches $100 (fill candle) also reaches a high of $125
* **THEN** the backtest SHALL resolve the trade as a 2R win (`hit_2r = True`), not skip the fill candle and defer resolution to subsequent candles.

#### Scenario: Backtest same-bar fill and SL resolution
* **GIVEN** a Bullish LTF FVG with entry at $100 and stop at $90
* **WHEN** the fill candle's low breaches $90
* **THEN** the backtest SHALL resolve the trade as a loss (`STOPPED_OUT`) using the fill candle's extreme.

---

### Requirement: Immutable Active Trade Ledger & Stale Pending Invalidation
* **Single Active Position per Symbol**: While a trade is in `TRADE_ACTIVE`, entry price, stop loss, targets, and FVG anchor are strictly locked and immutable.
* **Alias Resolution**: Symbol aliases (e.g. `GOLD` $\rightarrow$ `PAXG`) MUST resolve to valid live mid-prices for floating $R$ and MFE tracking.
* **Stale Pending Invalidation**: `PENDING_RETRACE` setups that breach stop loss or break 4H anchor boundaries before entry fill MUST transition to `INVALIDATED` and archive to history.
* **Candle Extreme TP/SL Resolution**: Closed candles formed strictly post-entry (`timestamp >= entry_timestamp`) evaluate `candle.high` and `candle.low` to trigger `COMPLETED_TP` (+2.0R) or `STOPPED_OUT` (-1.0R).
* **Pending Same-Bar Fill Completion**: A `PENDING_RETRACE` setup that fills entry and reaches its completion target within the same closed candle MUST transition to `COMPLETED_TP`, emit `TP_HIT`, and archive to history — even though the scanner classifies the FVG as completed and no longer emits the setup.

#### Scenario: Pending setup breached before fill
* **GIVEN** a setup is registered in `PENDING_RETRACE` with entry at $60,000 and stop loss at $59,000
* **WHEN** live market price drops to $58,500 without touching $60,000
* **THEN** the setup transitions to `INVALIDATED`, emits `SETUP_INVALIDATED`, and moves to history.

#### Scenario: Alias symbol mid-price resolution
* **GIVEN** an active trade on symbol `GOLD` with entry at $2,500
* **WHEN** Hyperliquid mid-prices return `{"PAXG": 2530.0}`
* **THEN** the trade tracker resolves `GOLD` via `PAXG`, updating floating $R$ to `+1.5R` instead of remaining frozen at `0.00R`.

#### Scenario: Pending setup fills and completes in one candle
* **GIVEN** a `PENDING_RETRACE` trade on BTC with entry at $100.25, stop at $100.05, and 2R target at $100.65
* **WHEN** the next scanner cycle receives no setup for BTC (FVG already classified `COMPLETED`) but recent candles include a single closed candle with `low <= 100.25` and `high >= 100.65`
* **THEN** the pending trade transitions to `COMPLETED_TP` with `realized_r = +2.0`, emits `TP_HIT`, and moves to history.
