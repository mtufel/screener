# Strategy 2: ⚡ Extreme LTF FVG Strategy Specification Delta

## MODIFIED Requirements

### Requirement: Immutable Active Trade Ledger & Stale Pending Invalidation
* **Single Active Position per Symbol**: While a trade is in `TRADE_ACTIVE`, entry price, stop loss, targets, and FVG anchor are strictly locked and immutable.
* **Alias Resolution**: Symbol aliases (e.g. `GOLD` $\rightarrow$ `PAXG`) MUST resolve to valid live mid-prices for floating $R$ and MFE tracking.
* **Stale Pending Invalidation**: `PENDING_RETRACE` setups that breach stop loss or break 4H anchor boundaries before entry fill MUST transition to `INVALIDATED` and archive to history.
* **Candle Extreme TP/SL Resolution**: Closed candles formed strictly post-entry (`timestamp >= entry_timestamp`) evaluate `candle.high` and `candle.low` to trigger `COMPLETED_TP` (+2.0R) or `STOPPED_OUT` (-1.0R).

#### Scenario: Pending setup breached before fill
* **GIVEN** a setup is registered in `PENDING_RETRACE` with entry at $60,000 and stop loss at $59,000
* **WHEN** live market price drops to $58,500 without touching $60,000
* **THEN** the setup transitions to `INVALIDATED`, emits `SETUP_INVALIDATED`, and moves to history.

#### Scenario: Alias symbol mid-price resolution
* **GIVEN** an active trade on symbol `GOLD` with entry at $2,500
* **WHEN** Hyperliquid mid-prices return `{"PAXG": 2530.0}`
* **THEN** the trade tracker resolves `GOLD` via `PAXG`, updating floating $R$ to `+1.5R` instead of remaining frozen at `0.00R`.
