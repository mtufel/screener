# 🚀 Crypto Fair Value Gap (FVG) Day-Trading Screener

A production-ready cryptocurrency day-trading screener built with **FastAPI**, **Hyperliquid's Public API**, and **Telegram Bot Alerts**.

The engine scans perpetual markets for multi-timeframe Fair Value Gaps (4H Higher Timeframe + 15m Lower Timeframe), checks price containment, calculates stop-loss reference levels, scores setups, and broadcasts the top ranked opportunities directly to your Telegram channel or group.

---

## 📌 Strategy Overview

For full technical and algorithmic specifications, see **[STRATEGIES.md](STRATEGIES.md)**.

Strategy 1 (2-stage standard FVG) has been removed. The product runs **Strategy 2 only**.

### ⚡ Strategy 2: Extreme LTF FVG Strategy
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
├── requirements.txt            # Python dependencies
├── app_config.py               # Leaf module: env constants, runtime state, logging
├── hyperliquid_client.py       # Async Hyperliquid client (Token Bucket, 429 Cooldown)
├── market_data/                # Pluggable data providers (Hyperliquid, Binance, CCXT)
├── candle_store.py             # Canonical candle persistence + TIMEFRAME_MS
├── strategy_extreme_fvg.py     # Extreme LTF FVG engine & state machine
├── extreme_trade_tracker.py    # Immutable Active Trade Ledger
├── live_screener_extreme.py    # Standalone single-cycle scanner
├── backtest_extreme_fvg.py     # Extreme backtester engine
├── chart_generator.py          # High-contrast TradingView-style candlestick chart generator
├── telegram_client.py          # Telegram alert dispatcher & photo attachments
├── screener_cycle.py           # Scan-cycle orchestrator, alert dispatch, daemon loop
├── dashboard_ws.py             # WebSocket manager + /ws/extreme-live
├── api/                        # Routers: system.py (health/status/config), extreme.py (scan/backtest/trades)
├── main.py                     # FastAPI app facade (run with: uvicorn main:app)
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
* Open `http://localhost:8000/` in your browser to verify the dashboard loads.
* Open `http://localhost:8000/health` to view operational metrics and latest scan results.
* Trigger a manual scan anytime via `curl -X POST http://localhost:8000/api/extreme/scan`.
* Live trade events stream over the WebSocket at `ws://localhost:8000/ws/extreme-live`.

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

All core strategy parameters are centralized in `strategy_extreme_fvg.py` and configurable via `.env`:

### 1. Change Timeframes & Lookback
In `.env`:
```ini
HTF_TIMEFRAME="4h"              # Higher timeframe: "1h", "4h", "1d"
EXTREME_LTF_TIMEFRAME="15m"     # Lower timeframe used for post-touch discovery
LOOKBACK_CANDLES=50             # Number of historical HTF candles inspected
MAX_HTF_RETRACE_CANDLES=6       # HTF freshness: candles since FVG formation
USE_CLOSE_BASED_INVALIDATION=true
```

### 2. Change Scan Interval & Alert Count
In `.env`:
```ini
SCAN_INTERVAL_MINUTES=15    # Scan frequency in minutes
TOP_N_ALERTS=10             # Number of highest-scoring setups to alert
```

### 3. Enable Session Filtering (London / NY / Asia)
In `.env`:
```ini
SESSION_FILTER_ENABLED=true   # Restrict fills/scans to major crypto sessions
```

### 4. Choose the Market Data Provider
In `.env`:
```ini
DATA_PROVIDER=hyperliquid            # hyperliquid | binance | ccxt
FALLBACK_DATA_PROVIDER=binance       # Used when the primary provider fails
```

### 5. Strategy 3 — Liquidity-Sweep FVG (`liquidity_sweep_fvg`)
The video-model strategy (4H FVG bias → liquidity sweep → LTF FVG entry → liquidity target) runs as a second registered strategy. Validated knobs (see `strategy3_validation_report.html` and `STRATEGIES.md` §Strategy 3):
```ini
EXTREME_REQUIRE_SWEEP=true           # Require a fresh opposing-side pool sweep
EXTREME_SWEEP_MAX_AGE_H=2            # Sweep must occur within this many hours before FVG formation
EXTREME_ANCHOR_AGE_GUARD=true        # Skip 4H anchors aged 24–48h at fill (measured dead zone)
EXTREME_GAP_BAND_EXCLUDE=0.10,0.20   # Reject LTF FVGs whose gap % falls in this band
EXTREME_TP_MODE=LIQUIDITY            # LIQUIDITY (nearest pool ≥1.5R) or FIXED_R
EXTREME_MIN_RR_FOR_LIQUIDITY=1.5
EXTREME_TP_BUFFER_PCT=0.02
EXTREME_S3_ENTRY_SESSIONS=NY_KZ      # Entry fills restricted to 13:00–16:00 UTC
```
Run its backtester: `python backtest_liquidity_sweep_fvg.py --symbol BTC --days 90 --ltf 5m --invalidation close`

### 6. Shadow (Paper) Mode — Run S3 Alongside Strategy 2
```ini
EXTREME_SHADOW_STRATEGIES=liquidity_sweep_fvg   # comma-separated registry names; empty = off
```
When set, the daemon scans the listed strategies in the **same cycle** as the active strategy. Shadow setups/trades:
- are tracked in the shared ledger under their own isolation scope — they **never block** the active strategy's setups on the same symbol (and vice versa);
- appear in the dashboard **Live History** with a violet `SHADOW` badge; filter with the **Strategy** dropdown;
- are broadcast to the dashboard WebSocket and persisted like normal trades, but **never send Telegram alerts**;
- use the strategy's validated defaults (`strategies/strategy3_liquidity_sweep.py::default_params`), not the daemon's shared config.

Per-strategy stats: `GET /api/{strategy}/status` → `ledger_summary`, or `python -c "from extreme_trade_tracker import extreme_trade_tracker as t; print(t.get_summary(strategy='liquidity_sweep_fvg'))"`. Compare shadow vs active over ~2 weeks before promoting S3 (`EXTREME_ACTIVE_STRATEGY=liquidity_sweep_fvg`).

Note: removing a name from the list leaves its still-open paper trades frozen in the ledger (visible in history, no longer monitored); re-enable the name to resume tracking them.

### 7. Strategy 3 Dashboard Tab & Backtest Lab
The dashboard has a dedicated **Strategy 3** section with two inner tabs:

- **Live Shadow & Setup Scan** — one-click shadow-mode toggle, S3 shadow ledger (paper trades with TP mode / sweep pool / MFE per trade), and an on-demand setup scan across symbols (`GET /api/{strategy}/scan?symbols=BTC,ETH,SOL`).
- **Backtest Lab (Parameters)** — replay history with any combination of S3 knobs and compare runs side by side:
  - Core: symbol, days (14–90), LTF timeframe, invalidation mode, min gap %, entry session (NY killzone / London / ALL).
  - Gates: require fresh sweep (≤ 2h), anchor-age guard (skip 24–48h anchors), exclude gap band 0.10–0.20%.
  - Take-profit: liquidity-first vs fixed-R, min RR for a liquidity target, fallback R.
  - Results: win rate / net R / profit factor / max drawdown / MFE KPIs, a **gate-rejection breakdown** (why setups were filtered), the full executed-trade table, and a **variant comparison** — pin any run, change parameters, re-run, and A/B the rows.

The lab calls `GET /api/{strategy}/backtest` (same endpoint the CLI numbers come from), so UI results match `backtest_liquidity_sweep_fvg.py` exactly. Defaults in the UI are the validated config (NY killzone, sweep gate on, liquidity TP @ ≥1.5RR).

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

Run the full offline test suite (251 tests):
```bash
pytest -q
```
