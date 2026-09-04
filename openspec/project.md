# 🚀 Crypto Fair Value Gap (FVG) Screener & Trade Engine

## 📌 Project Overview
A production-ready cryptocurrency perpetuals day-trading engine and screener built with **FastAPI**, **Hyperliquid's Public API**, **Telegram Bot Alerts**, and an **Interactive Web Dashboard (IST-anchored)**.

The system continuously scans crypto perpetuals markets for multi-timeframe Fair Value Gaps (4H Higher Timeframe + 15m/5m Lower Timeframe), computes exact entry and stop-loss reference boundaries, enforces an **Immutable Active Trade Ledger**, and delivers real-time candlestick charts with visual annotations directly to Telegram and the browser.

---

## 🛠️ Technology Stack

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Backend Framework** | **FastAPI** (Python 3.9+) | RESTful API, asynchronous background daemons, server-rendered dashboard |
| **Market Data Feed** | **Hyperliquid Info API** (`https://api.hyperliquid.xyz/info`) | Live Level-1 perpetual pricing, universe discovery, and historical OHLCV klines |
| **Historical Fallback** | **Binance Kline API** | Secondary fallback for deep historical 5m/15m candles |
| **Chart Generation** | **Matplotlib / Agg backend** | High-contrast dark-themed candlestick charts with bounding boxes & entry markers |
| **Frontend UI** | **HTML5 + Tailwind CSS + FontAwesome + JS** | Responsive live screener cards, dual strategy tabs, backtester suite, trade log |
| **Alerting** | **Telegram Bot API** (httpx async) | Automated entry fills, take profit, and stop loss alerts with chart photo attachments |
| **Test Suite** | **pytest / pytest-asyncio** | Comprehensive unit, integration, and backtest test suite (87 tests, 100% offline) |

---

## 🧠 Core Domain Concepts & Strategy Architecture

The project implements two distinct trading strategies designed for crypto perpetuals with zero lookahead bias:

### 1. Fair Value Gap (FVG) Fundamentals
Evaluates rolling 3-candle sequences `[c1, c2, c3]` (`c1` oldest, `c2` impulse, `c3` newest):
* **Bullish FVG**: `c3.low > c1.high` $\rightarrow$ Gap range: `[c1.high, c3.low]`.
* **Bearish FVG**: `c3.high < c1.low` $\rightarrow$ Gap range: `[c3.high, c1.low]`.
* **Gap %**: `(Gap Width / Midpoint) * 100`.

---

### 2. Strategy 1: 2-Stage Standard Multi-Timeframe FVG
* **Phase 1 (4H Macro Anchor)**: Checks if live price is contained within an active 4H FVG zone. Supports `ANY_VALID`, `RECENT_FORMED`, and `TOUCH_WINDOW` modes with wick-based or close-based invalidation.
* **Phase 2 (15m/5m Micro Confirmation)**: Identifies a matching LTF FVG in the same direction and scores setups via composite formula:
  $$\text{Score} = 0.35 \times \text{Tightness}_{\text{4H}} + 0.35 \times \text{Tightness}_{\text{LTF}} + 0.30 \times \text{CenterProximity}$$
* **Stop Loss**: Extreme wick of the 3 candles forming the LTF FVG.

---

### 3. Strategy 2: ⚡ Extreme LTF FVG Strategy
A high-precision day-trading strategy executing strictly post-4H-touch:
1. **Incremental 4H FVG Cache (`HTFFVGCache`)**: $O(1)$ live tracking of active 4H FVGs without full historical rescanning.
2. **4H Touch Anchor Selection**: Selects the most recent touched 4H FVG (prioritizing zones currently containing price) and pinpoints the exact **First Touch Timestamp** (`first_touch_timestamp`).
3. **Post-Touch LTF FVG Discovery**: Scans LTF (15m) candles formed strictly post-first-touch with a minimum gap threshold ($\ge 0.05\%$).
4. **#1 Extreme Ranking**: Selects the deepest FVG closest to the 4H zone:
   * **Bullish (Long)**: Lowest price FVG ($\arg\min \text{bottom}$).
   * **Bearish (Short)**: Highest price FVG ($\arg\max \text{top}$).
5. **Execution Parameters**:
   * **Entry**: Outer boundary (`top` for Bullish, `bottom` for Bearish).
   * **Stop Loss**: Exact 3-candle extreme wick $\min(c_1.l, c_2.l, c_3.l)$ for Longs, $\max(c_1.h, c_2.h, c_3.h)$ for Shorts.
   * **Targets**: 1R, 2R (Primary $\star$), and 3R.
6. **Immutable Active Trade Ledger (`ExtremeTradeTracker`)**:
   * Stored in `data/extreme_live_trades.json`.
   * Once a trade is `TRADE_ACTIVE`, its entry price and SL are strictly locked.
   * Resolves exits using post-entry closed candle extremes (`high` / `low`).

---

## 📂 Repository File Structure

```
crypto-fvg-screener/
├── .agents/skills/             # OpenSpec agent skills
├── .cursor/                    # Cursor IDE rules & commands
├── .gemini/                    # Gemini CLI rules & commands
├── .claude/                    # Claude Code rules & commands
├── openspec/
│   ├── config.yaml             # OpenSpec project configuration
│   ├── project.md              # Project specifications & context (this file)
│   ├── specs/                  # OpenSpec core specifications
│   └── changes/                # OpenSpec proposal changes
├── data/
│   ├── active_trades.json      # Strategy 1 active trade ledger
│   └── extreme_live_trades.json# Strategy 2 Extreme immutable trade ledger
├── templates/
│   └── index.html              # Full Web Dashboard UI (Live & Backtester)
├── backtest.py                 # Strategy 1 backtest simulator
├── backtest_extreme_fvg.py     # Strategy 2 Extreme backtest simulator
├── chart_generator.py          # High-contrast TradingView candlestick chart generator
├── extreme_trade_tracker.py    # Strategy 2 Immutable Active Trade Ledger & State Machine
├── hyperliquid_client.py       # Async Hyperliquid client (Token Bucket, 429 Cooldown)
├── main.py                     # FastAPI web app, dual background daemons, REST API
├── strategy.py                 # Strategy 1 core engine & scoring
├── strategy_extreme_fvg.py     # Strategy 2 Extreme core engine & candidate filter
├── telegram_client.py          # Telegram bot alert dispatcher & chart photo poster
├── trade_tracker.py            # Strategy 1 trade tracker
├── STRATEGIES.md               # Deep mathematical & algorithmic specifications
├── README.md                   # Operational & setup guide
├── pyproject.toml              # Pytest configuration & warning filters
└── requirements.txt            # Python dependencies
```

---

## 🚀 Key Commands & Workflows

### 1. Running the Application
```bash
# Activate virtual environment
source .venv/bin/activate

# Launch FastAPI server with auto-reload
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
* **Dashboard**: Open `http://localhost:8000/` in browser.
* **API Documentation**: Open `http://localhost:8000/docs` (Swagger UI).

### 2. Running Automated Tests
```bash
# Run full offline test suite (87 tests)
pytest -v

# Run strategy-specific tests
pytest test_strategy_extreme_fvg.py test_extreme_trade_tracker.py -v
```

### 3. OpenSpec Commands
```bash
# View OpenSpec dashboard
openspec view

# List changes or specs
openspec list
openspec list --specs

# Propose a new change
openspec change
```
