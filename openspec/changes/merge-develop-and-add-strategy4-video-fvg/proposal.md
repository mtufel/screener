# OpenSpec Proposal: Merge develop and register Video FVG as Strategy 4

## Problem Statement
The upstream `develop` branch merged PR #33, which integrated the unified strategy framework, the Replay Engine, and Strategy 3 (`liquidity_sweep_fvg`). Concurrently, this repository developed the Video FVG strategy (initially numbered Strategy 3). Merging `develop` into this repository introduces merge conflicts and requires re-indexing Video FVG as **Strategy 4** (`video_fvg`) while ensuring all strategies (Strategy 2, 3, and 4) have explicit descriptions across the registry, UI, and documentation.

## Proposed Solution
1. **Merge `origin/develop`**:
   - Resolve merge conflicts across API endpoints, config, cycle scanner, trade tracker, and templates.
   - Retain all Strategy 3 (`liquidity_sweep_fvg`) and replay engine capabilities.
   - Retain bias-filter configurations for Strategy 2.
2. **Promote Video FVG to Strategy 4**:
   - Register `Strategy4VideoFVG` (`strategies/strategy4_video_fvg.py`) under registry name `video_fvg`.
   - Set display name: `Video FVG (4H Anchor)`.
   - Set description: `4H FVG Bias + HTF Respect Confirmation + First LTF FVG Entry + 3R Target`.
3. **Multi-Strategy Descriptions**:
   - Update `strategies/__init__.py` to import `strategy2_extreme`, `strategy3_liquidity_sweep`, and `strategy4_video_fvg`.
   - Update `STRATEGIES.md` with full specifications for Strategy 2, Strategy 3, and Strategy 4, including a side-by-side comparison matrix.
   - Ensure `/api/strategies` and the UI dropdown display clear descriptions for every registered strategy.
4. **Validation**:
   - Migrate and expand test suite to `test_strategy4_video_fvg.py`, `test_strategy_registry.py`, and `test_strategy_api_routes.py`.
   - Ensure 100% test suite pass rate (`pytest -v`).

## Impact
- Strategy 2 (`extreme_fvg`), Strategy 3 (`liquidity_sweep_fvg`), and Strategy 4 (`video_fvg`) coexist seamlessly in the pluggable registry.
- Zero regressions in existing SOT suites.
