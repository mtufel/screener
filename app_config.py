"""
Application configuration leaf module: environment constants, runtime `state`
dict, filesystem directories, and logging setup.

Imported by every other module (including the main.py facade) BEFORE anything
that reads environment variables, so `.env` is loaded exactly once here.
"""

import logging
import os
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv()

# IST Timezone (UTC + 5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# ==============================================================================
# LOGGING CONFIGURATION
# ==============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fvg-screener")

# ==============================================================================
# CONSTANTS & CONFIG
# ==============================================================================
COINS_WHITELIST = os.getenv("COINS_WHITELIST", "BTC,ETH,SOL").strip()

ENABLE_STRATEGY_2 = os.getenv("ENABLE_STRATEGY_2", os.getenv("STRATEGY_2_ENABLED", "true")).strip().lower() in ("true", "1", "yes")
EXTREME_SCAN_INTERVAL_SECONDS = int(os.getenv("EXTREME_SCAN_INTERVAL_SECONDS", "30"))
EXTREME_LTF_TIMEFRAME = os.getenv("EXTREME_LTF_TIMEFRAME", "5m")
EXTREME_COMPLETION_TARGET = os.getenv("EXTREME_COMPLETION_TARGET", "2R")
EXTREME_MIN_GAP_PCT = float(os.getenv("EXTREME_MIN_GAP_PCT", "0.05"))
EXTREME_USE_CLOSE_INVALIDATION = os.getenv("EXTREME_USE_CLOSE_INVALIDATION", "true").strip().lower() in ("true", "1", "yes")
EXTREME_SESSION_FILTER_ENABLED = os.getenv("EXTREME_SESSION_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes")
EXTREME_WEEKDAY_FILTER_ENABLED = os.getenv("EXTREME_WEEKDAY_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes")
EXTREME_ENTRY_SESSION_FILTER_ENABLED = os.getenv("EXTREME_ENTRY_SESSION_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes")
EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED = os.getenv("EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes")
EXTREME_SESSIONS = os.getenv("EXTREME_SESSIONS", "ALL").strip()
EXTREME_ENTRY_SESSIONS = os.getenv("EXTREME_ENTRY_SESSIONS", "ALL").strip()
# Bias-filter params (live defaults from research backtest marginal analysis)
EXTREME_MAX_DIST_FROM_4H_PCT = float(os.getenv("EXTREME_MAX_DIST_FROM_4H_PCT", "2.0"))
EXTREME_REQUIRE_MOMENTUM = os.getenv("EXTREME_REQUIRE_MOMENTUM", "false").strip().lower() in ("true", "1", "yes")
EXTREME_MAX_GAP_PCT = float(os.getenv("EXTREME_MAX_GAP_PCT", "0.3"))
EXTREME_MAX_LTF_FVG_AGE_CANDLES = int(os.getenv("EXTREME_MAX_LTF_FVG_AGE_CANDLES", "9999"))

state: Dict[str, Any] = {
    "strategy_2_enabled": ENABLE_STRATEGY_2,
    "is_running": ENABLE_STRATEGY_2,
    "coins_whitelist": COINS_WHITELIST,
    "universe_count": 0,
    "last_scan_time": None,
    "last_scan_time_ist": None,
    "last_scan_results_count": 0,
    "activated_count": 0,
    "pending_count": 0,
    "last_scan_setups": [],
    "total_scans_completed": 0,
    "background_task": None,
    "monitor_task": None,
    "extreme_is_running": ENABLE_STRATEGY_2,
    "extreme_interval_seconds": EXTREME_SCAN_INTERVAL_SECONDS,
    "extreme_ltf": EXTREME_LTF_TIMEFRAME,
    "extreme_target": EXTREME_COMPLETION_TARGET,
    "extreme_min_gap": EXTREME_MIN_GAP_PCT,
    "extreme_use_close": EXTREME_USE_CLOSE_INVALIDATION,
    "extreme_session_filter": EXTREME_SESSION_FILTER_ENABLED,
    "extreme_weekday_filter": EXTREME_WEEKDAY_FILTER_ENABLED,
    "extreme_entry_session_filter": EXTREME_ENTRY_SESSION_FILTER_ENABLED,
    "extreme_entry_weekday_filter": EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED,
    "extreme_sessions": EXTREME_SESSIONS,
    "extreme_entry_sessions": EXTREME_ENTRY_SESSIONS,
    # Bias filter state (mirrors env defaults from research)
    "extreme_max_dist_from_4h_pct": EXTREME_MAX_DIST_FROM_4H_PCT,
    "extreme_require_momentum": EXTREME_REQUIRE_MOMENTUM,
    "extreme_max_gap_pct": EXTREME_MAX_GAP_PCT,
    "extreme_max_ltf_fvg_age_candles": EXTREME_MAX_LTF_FVG_AGE_CANDLES,
    "extreme_last_scan_time_ist": None,
    "extreme_setups": [],
    "extreme_active_count": 0,
    "extreme_pending_count": 0,
    "extreme_total_cycles": 0,
    "extreme_background_task": None,
    "data_provider": os.getenv("DATA_PROVIDER", "binance").strip().lower(),
    "fallback_data_provider": os.getenv("FALLBACK_DATA_PROVIDER", "hyperliquid").strip().lower(),
}

# Dashboard asset directories (charts are written by the alert pipeline)
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
os.makedirs(STATIC_DIR / "charts", exist_ok=True)
