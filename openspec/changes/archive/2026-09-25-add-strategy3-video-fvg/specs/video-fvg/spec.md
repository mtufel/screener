## Purpose

Strategy 3 — Video FVG — is a two-timeframe fair value gap system that uses a 4H FVG as a directional anchor and an LTF (1m / 5m / 15m) FVG as the entry trigger. Price respecting a 4H FVG signals alignment with institutional order flow; the first LTF FVG that forms in that direction provides a clean entry with stop at the FVG-formation wick and a 3:1 minimum risk-reward target.

## ADDED Requirements

### Requirement: 4H FVG Anchor Identification
The system SHALL identify the single most recent closed 4H Fair Value Gap as the directional anchor. The anchor FVG is defined as the gap between the wick-high of Candle 1 and the wick-low of Candle 3 in a qualifying 3-candle imbalance pattern on 4H candles. The anchor SHALL be the most recent such zone — no oldest-first selection; recency takes priority.

#### Scenario: Most recent 4H FVG selected as anchor
- **WHEN** the system scans 4H candles for FVG zones
- **THEN** the anchor SHALL be the most recently closed 4H FVG, not the oldest or deepest.

#### Scenario: Anchor remains fixed until breached
- **WHEN** a 4H FVG has been identified as the anchor
- **THEN** it SHALL remain the active anchor until price breaches it (Bullish: candle closes above anchor top; Bearish: candle closes below anchor bottom).

### Requirement: HTF Respect Confirmation
The system SHALL wait for price to return to the 4H anchor FVG and demonstrate an aggressive directional move away from it before proceeding to LTF setup detection. This return-and-rejection is the confirmation signal that the 4H zone is respected — indicating alignment with the higher-timeframe institutional flow.

#### Scenario: Bullish HTF respect and rejection
- **GIVEN** a Bullish 4H anchor FVG zone `[100.00 – 100.50]`
- **WHEN** price enters the zone and the next 4H candle closes with a strong bullish body (close near high)
- **THEN** the system SHALL record `htf_confirmed = True` and activate LTF FVG scanning for entries in the Bullish direction.

#### Scenario: Bearish HTF respect and rejection
- **GIVEN** a Bearish 4H anchor FVG zone `[100.00 – 99.50]`
- **WHEN** price enters the zone and the next 4H candle closes with a strong bearish body (close near low)
- **THEN** the system SHALL record `htf_confirmed = True` and activate LTF FVG scanning for entries in the Bearish direction.

### Requirement: LTF FVG Setup Formation After HTF Confirmation
After HTF respect is confirmed, the system SHALL scan the configured LTF (1m / 5m / 15m) for the first FVG that forms in the direction of the confirmed HTF anchor. The first such LTF FVG — not the deepest, not the most unmitigated — is the qualifying setup. This differs from Strategy 2's extreme-ranking approach; recency of formation after the confirmation event is the sole selection criterion.

#### Scenario: First bullish LTF FVG after HTF confirmation
- **WHEN** HTF respect is confirmed for a Bullish anchor
- **THEN** the system SHALL identify the first LTF FVG that forms in the Bullish direction after the confirmation timestamp
- **AND** that FVG SHALL become the active setup, regardless of its gap size relative to other LTF FVGs on the chart.

#### Scenario: LTF FVG minimum gap filter
- **WHEN** scanning for LTF FVG setups
- **THEN** the system SHALL apply a configurable minimum gap threshold (default 0.03%) to filter out sub-tick gaps.

### Requirement: Entry, Stop Loss, and Take Profit Calculation
The system SHALL calculate entry, stop loss, and take profit levels from the qualifying LTF FVG:

- **Entry**: outer FVG boundary — for Bullish at the FVG bottom; for Bearish at the FVG top.
- **Stop loss**: wick-low of the candlestick that forms the LTF FVG for Bullish (NOT below it — AT the low); for Bearish at the wick-high of the formation candle.
- **Take profit**: minimum 3:1 risk-reward ratio from entry, targeting entry + 3x risk in the direction of the trade.

#### Scenario: Bullish entry parameters
- **GIVEN** a Bullish LTF FVG with bottom at $60,000, top at $60,100, and formation candle wick-low at $59,850
- **WHEN** parameters are calculated
- **THEN** entry SHALL be $60,000, stop loss SHALL be $59,850 (Risk: $150), and 1R target SHALL be $60,150, 2R at $60,300, 3R at $60,450.

#### Scenario: Stop loss is at formation wick, not beyond
- **GIVEN** the formation candle wick-low is $59,850 but the FVG bottom is $60,000
- **WHEN** stop loss is placed
- **THEN** the stop loss SHALL be $59,850 (at the wick), not below it — at the formation wick exactly.

### Requirement: Minimum 3:1 Take Profit Target
The system SHALL set a minimum 3:1 risk-reward target for all Strategy 3 trades. The 2R and 1R levels are calculated as intermediate milestones but the primary completion target is 3R.

#### Scenario: 3R target set as default completion target
- **WHEN** a Strategy 3 setup is generated
- **THEN** the completion target SHALL default to `"3R"` — the backtest and ledger SHALL resolve `COMPLETED_TP` when the 3R target is reached.

### Requirement: HTF FVG Anchor Metadata in Trade Record
Every trade opened by Strategy 3 SHALL record the 4H anchor FVG metadata (bottom, top, formed timestamp, direction) in addition to the LTF FVG metadata, so the ledger captures the full two-timeframe confirmation context for later analysis.

#### Scenario: Trade record contains full two-TF context
- **WHEN** a Strategy 3 trade is opened and recorded
- **THEN** the ledger record SHALL contain the 4H anchor FVG block alongside the LTF FVG block, enabling per-anchor performance analysis post-archive.

### Requirement: Multi-Timeframe LTF Support (1m, 5m, 15m)
The system SHALL support full-pipeline execution of Strategy 3 across 1m, 5m, and 15m lower timeframes for real-time scanning, background daemon monitoring, and historical backtesting.

#### Scenario: 15m timeframe backtest
- **WHEN** a Strategy 3 backtest is run with `ltf=15m`
- **THEN** LTF FVG setups SHALL be evaluated on 15m bars and all results SHALL include `ltf_timeframe = "15m"` in serialized output.

### Requirement: Strategy Registration and Daemon Interoperability
Strategy 3 SHALL be registered in the strategy registry with `name="video_fvg"` and SHALL be runnable as the active daemon strategy by setting `EXTREME_ACTIVE_STRATEGY=video_fvg`. The daemon SHALL invoke `Strategy3VideoFVG.find_setups()` through the same orchestration loop used for Strategy 2, with no changes to `screener_cycle.py`.

#### Scenario: Strategy 3 selected as active daemon strategy
- **WHEN** `EXTREME_ACTIVE_STRATEGY=video_fvg` is set in the environment
- **THEN** the daemon SHALL resolve `video_fvg` from the registry and run `find_setups()` for each symbol, producing setups with `strategy="video_fvg"` in the ledger.

#### Scenario: Strategy 3 backtest via API
- **WHEN** a client calls `GET /api/video_fvg/backtest?symbol=BTC&days=30`
- **THEN** the system SHALL invoke `Strategy3VideoFVG.backtest()` and return the backtest report with the `video_fvg` strategy name and effective parameters.