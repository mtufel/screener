"""
FastAPI facade module — import this (or run `uvicorn main:app`) exactly as before.

DELEGATION ARCHITECTURE (post Phase 3 split):
  app_config.py      leaf: env constants, runtime `state`, dirs, logging
  screener_cycle.py  scan-cycle orchestrator, payloads, alert dispatch, lifespan
  dashboard_ws.py    WS manager singleton + /ws/extreme-live
  api/system.py      system router (root, health, status, config, test-telegram)
  api/extreme.py     extreme router (scan, backtest, status, toggle, config, chart, history)

PATCH-SURFACE CONTRACT: qa_harness.core.install_patches, qa_live_sim.py, and pytest
suites patch services as attributes of THIS module (`main.redis_client = sink`,
`patch("main.send_extreme_telegram_alert", ...)`), and tests do
`from main import app, state, execute_extreme_screener_cycle`. All names below are
re-exported; service call sites in submodules read through this namespace at call time
(see screener_cycle._svc and the function-level `import main` idiom).
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# app_config import comes FIRST: its import runs load_dotenv() and
# logging.basicConfig before the rest of the graph reads environment variables.
from app_config import STATIC_DIR, state

from dashboard_ws import DashboardWSManager, dashboard_ws_manager
from dashboard_ws import router as _ws_router
from screener_cycle import (
    execute_extreme_screener_cycle,
    extreme_screener_background_worker,
    lifespan,
    send_extreme_telegram_alert,
)
from screener_cycle import _register_services_in_main

from api.system import router as _system_router
from api.extreme import router as _extreme_router

# ==============================================================================
# FASTAPI APP DEFINITION (assembled here from the split routers)
# ==============================================================================
app = FastAPI(
    title="Crypto Fair Value Gap (FVG) Screener",
    description="Extreme LTF FVG screener, live ledger, Telegram alerts, and web dashboard.",
    version="2.3.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(_system_router)
app.include_router(_extreme_router)
app.include_router(_ws_router)

# ------------------------------------------------------------------------------
# Legacy name re-exports (name-compatibility with the pre-split main.py) and
# publication of the QA-patchable service targets onto this namespace.
# ------------------------------------------------------------------------------
from hyperliquid_client import hyperliquid_client, lookup_mid  # noqa: F401
from telegram_client import send_telegram_alert  # noqa: F401
from session_filter import SessionFilterConfig  # noqa: F401

# Config constants remain importable from main (e.g. `from main import EXTREME_SESSIONS`)
from app_config import (  # noqa: F401,E402
    COINS_WHITELIST,
    ENABLE_STRATEGY_2,
    EXTREME_COMPLETION_TARGET,
    EXTREME_ENTRY_SESSIONS,
    EXTREME_ENTRY_SESSION_FILTER_ENABLED,
    EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED,
    EXTREME_LTF_TIMEFRAME,
    EXTREME_MIN_GAP_PCT,
    EXTREME_SCAN_INTERVAL_SECONDS,
    EXTREME_SESSIONS,
    EXTREME_SESSION_FILTER_ENABLED,
    EXTREME_USE_CLOSE_INVALIDATION,
    EXTREME_WEEKDAY_FILTER_ENABLED,
    IST,
    TEMPLATES_DIR,
)

# Patch targets: tests/harness patch these as `main.<name>` (see module docstring).
_register_services_in_main()  # publishes send_extreme_telegram_alert, redis_client,
                              # get_market_data_provider, market_data_provider,
                              # get_last_n_candles (+ dashboard_ws_manager)

# `from main import X` compatibility for names tests/harness import directly.
from screener_cycle import _active_trade_setup_payload, _extreme_setup_payload  # noqa: F401,E402
from screener_cycle import _fetch_recent_candles_map, _runtime_extreme_config  # noqa: F401,E402
from screener_cycle import _dispatch_trade_alert, _broadcast_trade_event       # noqa: F401,E402
from screener_cycle import _trade_alert_message, _generate_event_chart         # noqa: F401,E402
from screener_cycle import _position_size_snippet, _extreme_anchor_payload     # noqa: F401,E402
from screener_cycle import _extreme_target_fvg_payload, _DISPATCH_LOG_LABELS    # noqa: F401,E402
from dashboard_ws import websocket_extreme_live  # noqa: F401,E402

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
