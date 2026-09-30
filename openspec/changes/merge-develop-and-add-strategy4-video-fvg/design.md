# OpenSpec Design: Merge develop and register Video FVG as Strategy 4

## System Architecture

The trading platform provides a pluggable strategy framework (`strategies/registry.py`) where strategies inherit from `BaseStrategy` (`strategies/base.py`) and declare self-contained defaults, setup finders, and backtesters:

```mermaid
flowchart TD
    subgraph Registry ["Strategy Registry"]
        S2["Strategy 2: Extreme LTF FVG (extreme_fvg)"]
        S3["Strategy 3: Liquidity-Sweep FVG (liquidity_sweep_fvg)"]
        S4["Strategy 4: Video FVG (video_fvg)"]
    end

    subgraph API ["FastAPI Endpoints"]
        R1["GET /api/strategies"]
        R2["POST /api/{strategy}/activate"]
        R3["GET /api/{strategy}/scan"]
        R4["GET /api/{strategy}/backtest"]
        R5["GET /api/{strategy}/status"]
        R6["GET /api/{strategy}/info"]
    end

    subgraph UI ["Unified Dashboard"]
        Dropdown["Strategy Dropdown"]
        DescBadge["Strategy Description & Mode Badge"]
        Params["Dynamic Param Form"]
    end

    Registry --> API
    API --> UI
```

### Strategy Registration Table
1. **Strategy 2 (`extreme_fvg`)**:
   - Class: `Strategy2Extreme` (`strategies/strategy2_extreme.py`)
   - Display: `Extreme LTF FVG`
   - Description: `4H Touched Anchor + Extreme LTF Outer Boundary + State Machine`
2. **Strategy 3 (`liquidity_sweep_fvg`)**:
   - Class: `Strategy3LiquiditySweepFVG` (`strategies/strategy3_liquidity_sweep.py`)
   - Display: `Liquidity-Sweep FVG`
   - Description: `4H FVG Bias + Liquidity Sweep Gate + LTF FVG Entry + Liquidity-First Target`
3. **Strategy 4 (`video_fvg`)**:
   - Class: `Strategy4VideoFVG` (`strategies/strategy4_video_fvg.py`)
   - Display: `Video FVG (4H Anchor)`
   - Description: `4H FVG Bias + HTF Respect Confirmation + First LTF FVG Entry + 3R Target`

### Invariant Guarantees
- `get_strategy("video_fvg")` resolves `Strategy4VideoFVG`.
- `list_strategy_names()` includes `["extreme_fvg", "liquidity_sweep_fvg", "video_fvg"]`.
- `GET /api/strategies` emits `name`, `display_name`, and `description` for all three strategies.
