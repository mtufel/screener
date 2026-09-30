# Specification: Strategy 4 Video FVG

## Capability: Video FVG Pluggable Strategy

### Requirements & Invariants

#### REQ-S4-01: Registry Identity
- The strategy must register under name `"video_fvg"`.
- The display name must be `"Video FVG (4H Anchor)"`.
- The description must be non-empty and describe the core model: `"4H FVG Bias + HTF Respect Confirmation + First LTF FVG Entry + 3R Target"`.

#### REQ-S4-02: Default Parameters
The strategy must declare the following default parameters:
- `ltf_timeframe`: `"5m"`
- `min_gap_pct`: `0.03`
- `completion_target`: `"3R"`
- `htf_confirm_body_pct`: `0.5`
- `session_filter`: `False`
- `weekday_filter`: `False`
- `sessions`: `"ALL"`

#### REQ-S4-03: Multi-Timeframe Algorithmic Flow
- **4H Anchor**: Identified as the most recently closed, non-invalidated 4H FVG.
- **HTF Respect**: The 4H candle touching the zone must close with `|close - open| / (high - low) >= htf_confirm_body_pct` in the bias direction.
- **First LTF FVG**: The first 5m FVG forming after the HTF confirmation close is selected as the trade setup.
- **Targets**: 1R/2R telemetry and 3R primary target.

#### REQ-S4-04: API and UI Integration
- `/api/strategies` must include Strategy 4 in the response list.
- `/api/video_fvg/info` must return the metadata, description, and default parameters.
- `/api/video_fvg/status` must return the runtime status and ledger summary.
