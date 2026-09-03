# 🚀 Crypto Fair Value Gap (FVG) Day-Trading Screener

A production-ready cryptocurrency day-trading screener built with **FastAPI**, **Hyperliquid's Public API**, and **Telegram Bot Alerts**.

The engine scans perpetual markets for multi-timeframe Fair Value Gaps (4H Higher Timeframe + 15m Lower Timeframe), checks price containment, calculates stop-loss reference levels, scores setups, and broadcasts the top ranked opportunities directly to your Telegram channel or group.

---

## 📌 Dual Strategy Suite Overview

For full technical and algorithmic specifications, see **[STRATEGIES.md](STRATEGIES.md)**.

### 🏛️ Strategy 1: 2-Stage Standard Multi-Timeframe FVG
* **Phase 1 (4H Macro Anchor)**: Checks if price is inside or recently retraced into an active 4H FVG zone. Supports `ANY_VALID`, `RECENT_FORMED`, and `TOUCH_WINDOW` modes with wick/close invalidation.
* **Phase 2 (15m/5m Micro Confirmation)**: Identifies matching LTF FVG and ranks opportunities via composite scoring (Tightness + Center Proximity).
* **Stop Loss Reference**: Extreme wick of the 3 candles forming the LTF FVG.

### ⚡ Strategy 2: ⚡ Extreme LTF FVG Strategy
* **4H Touch Anchor**: Pinpoints the exact timestamp when price first touched an active 4H FVG post-close (`first_touch_timestamp`).
* **Post-Touch LTF Discovery**: Scans LTF FVGs (15m) formed strictly post-touch with a minimum gap threshold ($\ge 0.05\%$).
* **#1 Extreme Ranking**: Selects the deepest FVG closest to the 4H zone (Lowest for Longs, Highest for Shorts).
* **Execution Parameters**: Entry at outer FVG boundary, Stop Loss at extreme 3-candle wick, with 1R, 2R (Primary $\star$), and 3R targets.
* **Immutable Active Trade Ledger**: Automatically tracks live floating $R$, MFE, and resolves TP/SL hits via candle extremes.

---

## 🛠️ Project Structure

```
crypto-fvg-screener/
├── .env.example                # Configuration template
├── .env                        # Local environment variables
├── requirements.txt            # Python dependencies
├── hyperliquid_client.py       # Async Hyperliquid client (Token Bucket, 429 Cooldown)
├── strategy.py                 # Strategy 1 (Standard 2-Stage FVG math & scoring)
├── strategy_extreme_fvg.py     # Strategy 2 (Extreme LTF FVG engine & state machine)
├── extreme_trade_tracker.py    # Strategy 2 Immutable Active Trade Ledger
├── backtest.py                 # Strategy 1 backtester engine
├── backtest_extreme_fvg.py     # Strategy 2 Extreme backtester engine
├── chart_generator.py          # High-contrast TradingView-style candlestick chart generator
├── telegram_client.py          # Telegram alert dispatcher & photo attachments
├── main.py                     # FastAPI app, dual background daemons & Web UI
├── templates/
│   └── index.html              # Real-time Web Dashboard interface
├── STRATEGIES.md               # Complete Strategy Architecture & Math Specs
└── README.md                   # Setup & operational documentation
```

---

## 🖥️ Interactive Web Dashboard UI

The application includes a real-time web dashboard accessible in any browser:
* **URL**: `http://localhost:8000/` or `http://localhost:8000/dashboard`
* **Features**:
  * **Live Setup Cards**: Shows qualified 4H + 15m setups with prices, gap ranges, and scores.
  * **Interactive Scan Trigger**: "Scan Now" button runs an immediate scan without waiting for the 15-minute timer.
  * **Telegram Alert Tester**: Test Telegram delivery directly from the UI with 1-click.
  * **Direct Trade Links**: Jump straight to Hyperliquid charts (`https://app.hyperliquid.xyz/trade/<SYMBOL>`).
  * **Auto-Refresh**: Live data synchronization every 10 seconds.

---

## ⚡ Quick Start Guide

### 1. Clone & Navigate
```bash
cd crypto-fvg-screener
```

### 2. Create Virtual Environment & Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Set Up Telegram Bot Credentials

1. **Create a Bot**:
   - Open Telegram and message [@BotFather](https://t.me/BotFather).
   - Send `/newbot`, follow the prompts, and copy the `API Token` provided.
2. **Get your Chat ID**:
   - Message [@userinfobot](https://t.me/userinfobot) or add your bot to a channel/group and send a message.
   - You can also get the ID by visiting `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`.
3. **Configure `.env`**:
   Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```
   Edit `.env`:
   ```ini
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRstuVWXyz
   TELEGRAM_CHAT_ID=123456789
   SCAN_INTERVAL_MINUTES=15
   TOP_N_ALERTS=10
   ```

### 4. Run the Screener
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

When started:
* The screener runs an immediate initial scan on startup.
* Continues running scans automatically every `SCAN_INTERVAL_MINUTES` in the background.
* Open `http://localhost:8000/` in your browser to verify `{"status": "screener running"}`.
* Open `http://localhost:8000/health` to view operational metrics and latest scan results.
* Trigger a manual scan anytime via `http://localhost:8000/scan` or `curl -X POST http://localhost:8000/scan`.

---

## 📱 Telegram Alert Preview

```
🟢 BTC-PERP — Bullish
4H FVG: 96,200.00 – 96,800.00
15m FVG: 96,350.00 – 96,550.00
Price: 96,420.00
SL ref: ≤ 96,100.00 (15m FVG candle low)
Score: 0.78

🔴 ETH-PERP — Bearish
4H FVG: 2,750.00 – 2,820.00
15m FVG: 2,780.00 – 2,805.00
Price: 2,792.00
SL ref: ≥ 2,830.00 (15m FVG candle high)
Score: 0.81
```

---

## ⚙️ Customization & Adjustments

All core strategy parameters are centralized in `strategy.py` and configurable via `.env`:

### 1. Change Timeframes & Lookback
In `.env` or at top of `strategy.py`:
```python
HTF_TIMEFRAME = "4h"       # Higher timeframe: "1h", "4h", "1d"
LTF_TIMEFRAME = "15m"      # Lower timeframe: "5m", "15m", "1h"
LOOKBACK_CANDLES = 50      # Number of historical candles inspected
```

### 2. Change Scan Interval & Alert Count
In `.env`:
```ini
SCAN_INTERVAL_MINUTES=15    # Scan frequency in minutes
TOP_N_ALERTS=10             # Number of highest-scoring setups to alert
```

### 3. Adjust Scoring Weights
In `strategy.py`:
```python
WEIGHT_HTF_TIGHTNESS = 0.35    # Weight for 4H gap tightness
WEIGHT_LTF_TIGHTNESS = 0.35    # Weight for 15m gap tightness
WEIGHT_CENTER_PROXIMITY = 0.30 # Weight for price being centered in 4H FVG
```

### 4. Enable Session Filtering (London / NY / Asia)
In `strategy.py`, edit the `is_major_session` function:
```python
def is_major_session(timestamp_ms: Optional[int] = None) -> bool:
    # Example: Restrict scans to active London & New York session hours (08:00 - 21:00 UTC)
    now_utc = datetime.now(timezone.utc)
    return 8 <= now_utc.hour < 21
```

---

## 🚢 Production Deployment

### Option A: Railway / Render / Fly.io
1. Connect your repository.
2. Set Build Command: `pip install -r requirements.txt`
3. Set Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add Environment Variables from `.env` in the dashboard (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SCAN_INTERVAL_MINUTES`).

### Option B: Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 🧪 Testing

Run the automated test suite:
```bash
pytest test_screener.py -v
```
