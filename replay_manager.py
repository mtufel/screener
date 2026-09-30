"""
Replay manager (replay_manager.py).

Runs the LIVE screener execution path against historical data: each replay step
advances a virtual clock to the next closed-LTF-candle boundary and calls
``execute_extreme_screener_cycle`` with (a) the replay data provider, (b) an
isolated in-memory trade tracker, and (c) a replay config dict. Only the data
feed and the clock change — orchestration, setup generation, and the trade
state machine are the production ones (openspec change
`unified-strategy-ui-replay-backtest`, D3/D4/D5).

Watch mode: runs execute as managed asyncio tasks with speed control
(virtual-minutes per real-second; MAX = as fast as possible), pause/resume/
abort, and per-step progress snapshots consumed by the /ws/replay channel.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from clock import VirtualClock, install_virtual_clock
from extreme_trade_tracker import ExtremeTradeTracker
from market_data_provider import get_market_data_provider
from replay_provider import ReplayMarketDataProvider, build_replay_dataset
from session_filter import SessionFilterConfig

logger = logging.getLogger("replay-manager")

SPEED_MAX = "MAX"  # unlimited: process boundaries as fast as the loop allows


class ReplayManager:
    """Owns replay runs keyed by replay_id (single active run per process in v1)."""

    def __init__(self):
        self._runs: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ start

    async def start(
        self,
        strategy: str,
        symbols: List[str],
        days: int = 14,
        ltf_timeframe: str = "5m",
        params: Optional[Dict[str, Any]] = None,
        speed: Any = SPEED_MAX,
    ) -> str:
        """Builds the dataset and launches the replay task; returns the replay_id."""
        from strategies import get_strategy, list_strategy_names

        get_strategy(strategy)  # validate; raises KeyError with available list

        active = self._active_run()
        if active:
            await self.abort(active["replay_id"])

        replay_id = uuid.uuid4().hex[:12]
        run: Dict[str, Any] = {
            "replay_id": replay_id,
            "strategy": strategy,
            "symbols": [s.strip().upper() for s in symbols if s.strip()],
            "days": int(days),
            "ltf_timeframe": ltf_timeframe,
            "params": dict(params or {}),
            "speed": speed,
            "status": "PREPARING",
            "error": None,
            "clock": None,
            "tracker": None,
            "provider": None,
            "task": None,
            "cycles": 0,
            "events": [],
            "started_at": datetime.utcnow().isoformat() + "Z",
            "finished_at": None,
            "_pause_event": asyncio.Event(),
        }
        run["_pause_event"].set()
        self._runs[replay_id] = run

        run["task"] = asyncio.create_task(self._run(replay_id))
        return replay_id

    # ---------------------------------------------------------------- control

    def _active_run(self) -> Optional[Dict[str, Any]]:
        for run in self._runs.values():
            if run["status"] in ("PREPARING", "RUNNING"):
                return run
        return None

    def _get(self, replay_id: str) -> Dict[str, Any]:
        run = self._runs.get(replay_id)
        if not run:
            raise KeyError(f"Unknown replay_id {replay_id!r}")
        return run

    async def pause(self, replay_id: str) -> Dict[str, Any]:
        run = self._get(replay_id)
        if run["status"] == "RUNNING":
            run["_pause_event"].clear()
            run["status"] = "PAUSED"
            await self._emit(run, "replay_progress")
        return self.snapshot(replay_id)

    async def resume(self, replay_id: str) -> Dict[str, Any]:
        run = self._get(replay_id)
        if run["status"] == "PAUSED":
            run["status"] = "RUNNING"
            run["_pause_event"].set()
            await self._emit(run, "replay_progress")
        return self.snapshot(replay_id)

    async def abort(self, replay_id: str) -> Dict[str, Any]:
        run = self._get(replay_id)
        if run["status"] in ("PREPARING", "RUNNING", "PAUSED"):
            run["status"] = "ABORTING"
            run["_pause_event"].set()  # unblock a paused loop so it can exit
            task = run.get("task")
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=10)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
            run["status"] = "ABORTED"
            run["finished_at"] = datetime.utcnow().isoformat() + "Z"
            await self._emit(run, "replay_complete")
        return self.snapshot(replay_id)

    # ---------------------------------------------------------------- queries

    def snapshot(self, replay_id: str) -> Dict[str, Any]:
        run = self._get(replay_id)
        tracker = run.get("tracker")
        clock = run.get("clock")
        snap: Dict[str, Any] = {
            "replay_id": replay_id,
            "status": run["status"],
            "strategy": run["strategy"],
            "symbols": run["symbols"],
            "days": run["days"],
            "ltf_timeframe": run["ltf_timeframe"],
            "speed": run["speed"],
            "params": run["params"],
            "virtual_time_ms": clock.now_ms() if clock else None,
            "virtual_time_ist": (
                datetime.fromtimestamp(clock.now_ms() / 1000.0).strftime("%d-%b %I:%M %p IST")
                if clock else None
            ),
            "cycles": run["cycles"],
            "error": run["error"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "events": list(run["events"][-50:]),
        }
        if tracker is not None:
            snap["ledger_summary"] = tracker.get_summary()
            snap["open_trades"] = [
                t.to_dict() for t in tracker.active_trades.values()
            ]
        return snap

    def report(self, replay_id: str) -> Dict[str, Any]:
        run = self._get(replay_id)
        snap = self.snapshot(replay_id)
        tracker = run.get("tracker")
        closed = []
        invalidated = []
        if tracker is not None:
            # Align the closed_trades list with the ledger summary: metrics
            # (win rate, net R) count only entered trades that resolved to
            # TP/SL, so the list must exclude never-entered INVALIDATED
            # setups — otherwise the UI shows a table longer than the
            # "closed trades" metric card on the same screen.
            closed = [
                t.to_dict() for t in tracker.history
                if t.state in ("COMPLETED_TP", "STOPPED_OUT")
            ]
            invalidated = [t.to_dict() for t in tracker.history if t.state == "INVALIDATED"]
        snap["report"] = {
            "engine": "replay",
            "strategy": run["strategy"],
            "status": run["status"],
            "cycles_run": run["cycles"],
            "closed_trades": closed,
            "invalidated_setups": invalidated,
            "metrics": tracker.get_summary() if tracker else {},
            "effective_params": run["params"],
        }
        return snap

    # ---------------------------------------------------------------- runner

    async def _run(self, replay_id: str) -> None:
        from strategies import get_strategy

        run = self._get(replay_id)
        try:
            # 1. Dataset (real time; no virtual clock installed yet)
            real_provider = get_market_data_provider()
            dataset = await build_replay_dataset(
                symbols=run["symbols"],
                days=run["days"],
                ltf_timeframe=run["ltf_timeframe"],
                provider=real_provider,
            )
            provider = ReplayMarketDataProvider(dataset, ltf_timeframe=run["ltf_timeframe"])
            run["provider"] = provider

            # 2. Isolated tracker (temp storage under data/replay/, never the live ledger).
            # Redis persistence is disabled for this instance (task 3.2): replay
            # trades must never reach the live 'extreme_trades' Redis namespace.
            tracker = ExtremeTradeTracker(storage_path=f"data/replay/{replay_id}.json")
            tracker.redis_persistence_enabled = False
            tracker.active_trades = {}
            tracker.history = []
            run["tracker"] = tracker

            # 3. Replay config (framework precedence: request params > strategy defaults)
            strategy = get_strategy(run["strategy"])
            params = strategy.resolve_params(run["params"])
            run["params"] = params
            ltf = params.get("ltf_timeframe", run["ltf_timeframe"])
            sessions_str = str(params.get("sessions", "ALL"))
            entry_sessions_str = str(params.get("entry_sessions", "ALL"))
            cfg = {
                "active_strategy": run["strategy"],
                "shadow_strategies": [],
                "coin_list": run["symbols"],
                "ltf": ltf,
                "target": params.get("completion_target", "2R"),
                "min_gap": params.get("min_gap_pct", 0.05),
                "use_close": params.get("use_close_invalidation", False),
                "sess_filter": params.get("session_filter", False),
                "wkday_filter": params.get("weekday_filter", False),
                "entry_sess_filter": params.get("entry_session_filter", False),
                "entry_wkday_filter": params.get("entry_weekday_filter", False),
                "sessions_str": sessions_str,
                "entry_sessions_str": entry_sessions_str,
                "session_config": SessionFilterConfig.from_legacy(
                    session_filter=params.get("session_filter", False),
                    weekday_filter=params.get("weekday_filter", False),
                    entry_session_filter=params.get("entry_session_filter", False),
                    entry_weekday_filter=params.get("entry_weekday_filter", False),
                    sessions=sessions_str,
                    entry_sessions=entry_sessions_str,
                ),
            }

            # 4. Step boundaries under the virtual clock
            clock = VirtualClock(start_ms=self._first_boundary(dataset, ltf))
            run["clock"] = clock
            # Run until the last LTF candle has fully CLOSED (open ts + duration)
            # so the final candle is scanned before the run completes.
            from strategy_extreme_fvg import TIMEFRAME_MS as _TF_MS

            ltf_dur = _TF_MS.get(ltf, 300_000)
            end_ms = max(
                (max(c.timestamp for c in series["ltf"]) + ltf_dur for series in dataset.values()),
                default=clock.now_ms(),
            )
            speed = run["speed"]

            import screener_cycle
            import strategy_extreme_fvg as _sef

            run["status"] = "RUNNING"
            await self._emit(run, "replay_progress")

            with install_virtual_clock(clock):
                prev_persist_flag = _sef.htf_cache_persistence_paused
                _sef.htf_cache_persistence_paused = True
                try:
                    while clock.now_ms() < end_ms:
                        # Pause gate (stops virtual-time advance while paused)
                        await run["_pause_event"].wait()
                        if run["status"] != "RUNNING":
                            break

                        # Real-time pacing for watchable speeds
                        if speed != SPEED_MAX:
                            await asyncio.sleep(self._step_delay_seconds(ltf, speed))

                        for sym in run["symbols"]:
                            # Replay isolation: per-symbol HTF re-bootstrap as-of virtual now
                            _sef.htf_fvg_cache.invalidate_cache(sym)

                        await screener_cycle.execute_extreme_screener_cycle(
                            cfg_override=cfg,
                            provider_override=provider,
                            tracker_override=tracker,
                            broadcast=False,
                            persist_results=False,
                            dispatch_alerts=False,
                            cycle_label=":Replay",
                        )
                        run["cycles"] += 1

                        next_boundary = provider.next_close_boundary(ltf)
                        if next_boundary is None:
                            break
                        clock.advance_to(next_boundary)
                        # Process the boundary that just became closed so the
                        # dataset's final candles are seen before completion.
                        # At MAX speed the next loop iteration already runs the
                        # cycle at this same boundary, so the extra pass is
                        # redundant work (~2x cycle cost); watchable speeds keep
                        # it so each boundary is scanned before its pacing sleep.
                        if speed != SPEED_MAX:
                            await screener_cycle.execute_extreme_screener_cycle(
                                cfg_override=cfg,
                                provider_override=provider,
                                tracker_override=tracker,
                                broadcast=False,
                                persist_results=False,
                                dispatch_alerts=False,
                                cycle_label=":Replay",
                            )
                            run["cycles"] += 1

                        await self._collect_events(run, tracker)
                        await self._emit(run, "replay_progress")
                        await asyncio.sleep(0)  # yield to the event loop
                finally:
                    _sef.htf_cache_persistence_paused = prev_persist_flag

            if run["status"] == "RUNNING":
                run["status"] = "COMPLETED"
            run["finished_at"] = datetime.utcnow().isoformat() + "Z"
            await self._collect_events(run, tracker)
            await self._emit(run, "replay_complete")
        except asyncio.CancelledError:
            run["status"] = "ABORTED"
            run["finished_at"] = datetime.utcnow().isoformat() + "Z"
            raise
        except Exception as exc:
            logger.exception("Replay %s failed: %s", replay_id, exc)
            run["status"] = "FAILED"
            run["error"] = str(exc)
            run["finished_at"] = datetime.utcnow().isoformat() + "Z"
            try:
                await self._emit(run, "replay_error")
            except Exception:
                pass

    @staticmethod
    def _first_boundary(dataset: Dict[str, Dict[str, Any]], ltf: str) -> int:
        """Virtual-time start: the first LTF close after the warmup window."""
        from strategy_extreme_fvg import TIMEFRAME_MS

        dur = TIMEFRAME_MS.get(ltf, 300_000)
        starts = [min(c.timestamp for c in series["ltf"]) for series in dataset.values()]
        return (min(starts) + dur) if starts else int(time.time() * 1000)

    @staticmethod
    def _step_delay_seconds(ltf: str, speed: Any) -> float:
        """Real seconds to pause per LTF boundary for watchable speeds (1x = realtime)."""
        from strategy_extreme_fvg import TIMEFRAME_MS

        try:
            multiplier = float(speed)
        except (TypeError, ValueError):
            multiplier = 1.0
        if multiplier <= 0:
            multiplier = 1.0
        period_min = TIMEFRAME_MS.get(ltf, 300_000) / 60_000.0
        return max(0.0, period_min / multiplier)

    async def _collect_events(self, run: Dict[str, Any], tracker: ExtremeTradeTracker) -> None:
        """Appends ledger state-changes to the run's event feed (bounded)."""
        count = run.get("_last_history_len", 0)
        if len(tracker.history) > count:
            for t in tracker.history[: len(tracker.history) - count]:
                run["events"].append({
                    "virtual_time_ms": run["clock"].now_ms() if run.get("clock") else None,
                    "trade": t.to_dict(),
                })
            run["events"] = run["events"][-200:]
            run["_last_history_len"] = len(tracker.history)

    async def _emit(self, run: Dict[str, Any], msg_type: str) -> None:
        """Broadcasts a replay frame over the dashboard WS manager (best-effort)."""
        try:
            import main

            payload = {"type": msg_type, **self.snapshot(run["replay_id"])}
            await main.dashboard_ws_manager.broadcast_replay(payload)
        except Exception as exc:
            logger.debug("Replay WS emit failed: %s", exc)


# Process-wide singleton (mirrors dashboard_ws_manager / extreme_trade_tracker patterns)
replay_manager = ReplayManager()
