"""Obico AI print-failure detection service.

Polls a self-hosted Obico ML API with snapshots from each monitored printer
while a print is running, smooths scores over time, and dispatches a configured
action (notify / pause / pause_and_off) when a sustained failure is detected.

See `obico_smoothing.py` for the per-print EWM + rolling-mean math.
"""

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.settings import Settings
from backend.app.services.obico_smoothing import (
    PrintState,
    classify,
    score_from_detections,
    thresholds,
)

logger = logging.getLogger(__name__)

HISTORY_MAX = 50
HEALTH_TIMEOUT = 5.0
DETECTION_TIMEOUT = 30.0


class ObicoDetectionService:
    """Singleton service that polls the ML API and acts on sustained failures."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        # printer_id -> PrintState (reset when a new print starts)
        self._states: dict[int, PrintState] = {}
        # printer_id -> task_name active when state was created (used to detect new prints)
        self._state_keys: dict[int, str] = {}
        # printer_id -> last classification ("safe"/"warning"/"failure")
        self._last_class: dict[int, str] = {}
        # printer_id -> whether an action has already been fired for the current print
        self._action_fired: dict[int, bool] = {}
        # Global detection event log (most-recent-first)
        self._history: deque = deque(maxlen=HISTORY_MAX)
        self._last_error: str | None = None

    # ---- lifecycle ----

    async def start(self):
        if self._task is not None:
            return
        logger.info("Starting Obico detection service")
        self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("Stopped Obico detection service")

    # ---- settings ----

    async def _load_settings(self) -> dict:
        keys = [
            "obico_enabled",
            "obico_ml_url",
            "obico_sensitivity",
            "obico_action",
            "obico_poll_interval",
            "obico_enabled_printers",
            "external_url",
        ]
        async with async_session() as db:
            result = await db.execute(select(Settings).where(Settings.key.in_(keys)))
            rows = {r.key: r.value for r in result.scalars().all()}

        enabled_printers_raw = rows.get("obico_enabled_printers", "")
        if enabled_printers_raw:
            try:
                enabled_printers = set(json.loads(enabled_printers_raw))
            except json.JSONDecodeError:
                enabled_printers = set()
        else:
            enabled_printers = None  # None = all printers

        return {
            "enabled": rows.get("obico_enabled", "false").lower() == "true",
            "ml_url": (rows.get("obico_ml_url") or "").rstrip("/"),
            "sensitivity": rows.get("obico_sensitivity", "medium"),
            "action": rows.get("obico_action", "notify"),
            "poll_interval": int(rows.get("obico_poll_interval", "10")),
            "enabled_printers": enabled_printers,
            "external_url": (rows.get("external_url") or "").rstrip("/"),
        }

    # ---- main loop ----

    async def _loop(self):
        """Poll active printers while enabled. Adjusts interval from settings each cycle."""
        while True:
            try:
                settings = await self._load_settings()
                interval = max(5, settings.get("poll_interval", 10))
                if not settings["enabled"] or not settings["ml_url"]:
                    await asyncio.sleep(interval)
                    continue
                if not settings["external_url"]:
                    # Without a reachable base URL, the ML API can't fetch snapshots.
                    self._last_error = "external_url not set — ML API cannot reach snapshot endpoint"
                    await asyncio.sleep(interval)
                    continue

                await self._poll_once(settings)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Obico detection loop error: %s", e)
                self._last_error = str(e)
                await asyncio.sleep(30)

    async def _poll_once(self, settings: dict):
        # Late import to avoid cycles at module load time
        from backend.app.services.printer_manager import printer_manager

        statuses = printer_manager.get_all_statuses()
        for printer_id, status in list(statuses.items()):
            if settings["enabled_printers"] is not None and printer_id not in settings["enabled_printers"]:
                continue
            if not printer_manager.is_connected(printer_id):
                continue
            if not status or getattr(status, "state", None) != "RUNNING":
                # Reset state when not printing so the next print starts fresh
                self._states.pop(printer_id, None)
                self._state_keys.pop(printer_id, None)
                self._action_fired.pop(printer_id, None)
                continue

            await self._check_printer(printer_id, status, settings)

    async def _check_printer(self, printer_id: int, status, settings: dict):
        task_name = getattr(status, "task_name", None) or getattr(status, "subtask_name", "") or ""
        key = f"{task_name}"
        if self._state_keys.get(printer_id) != key:
            self._states[printer_id] = PrintState()
            self._state_keys[printer_id] = key
            self._action_fired[printer_id] = False

        snapshot_url = f"{settings['external_url']}/api/v1/printers/{printer_id}/camera/snapshot"
        ml_url = f"{settings['ml_url']}/p/"

        try:
            async with httpx.AsyncClient(timeout=DETECTION_TIMEOUT) as client:
                resp = await client.get(ml_url, params={"img": snapshot_url})
                resp.raise_for_status()
                payload = resp.json()
        except Exception as e:
            self._last_error = f"ML API call failed for printer {printer_id}: {e}"
            logger.warning(self._last_error)
            return

        detections = payload.get("detections", []) if isinstance(payload, dict) else []
        current_p = score_from_detections(detections)
        state = self._states[printer_id]
        score = state.update(current_p)
        verdict = classify(score, settings["sensitivity"])
        self._last_class[printer_id] = verdict

        # Log every non-safe sample — safe samples would flood history
        if verdict != "safe" or detections:
            self._history.appendleft(
                {
                    "printer_id": printer_id,
                    "task_name": task_name,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "current_p": round(current_p, 4),
                    "score": round(score, 4),
                    "class": verdict,
                    "detections": len(detections),
                }
            )

        if verdict == "failure" and not self._action_fired.get(printer_id):
            self._action_fired[printer_id] = True
            await self._dispatch_action(printer_id, settings["action"], task_name, score)

    async def _dispatch_action(self, printer_id: int, action: str, task_name: str, score: float):
        from backend.app.services.obico_actions import execute_action

        logger.warning(
            "Obico: failure detected on printer %s (task=%r score=%.3f) — action=%s",
            printer_id,
            task_name,
            score,
            action,
        )
        try:
            await execute_action(printer_id, action, task_name, score)
        except Exception as e:
            self._last_error = f"Action dispatch failed: {e}"
            logger.error(self._last_error)

    # ---- queries ----

    def get_status(self) -> dict:
        low, high = thresholds("medium")
        return {
            "is_running": self._task is not None and not self._task.done(),
            "last_error": self._last_error,
            "per_printer": {
                pid: {
                    "class": self._last_class.get(pid, "safe"),
                    "frame_count": state.frame_count,
                    "score": round(state.ewm_mean, 4),
                }
                for pid, state in self._states.items()
            },
            "thresholds": {"low": low, "high": high},
            "history": list(self._history),
        }

    async def test_connection(self, url: str) -> dict:
        """Ping the ML API health endpoint. Returns {ok, status_code, body, error}."""
        target = f"{url.rstrip('/')}/hc/"
        try:
            async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
                resp = await client.get(target)
            body = resp.text.strip()
            return {
                "ok": resp.status_code == 200 and body.lower() == "ok",
                "status_code": resp.status_code,
                "body": body,
                "error": None,
            }
        except Exception as e:
            return {"ok": False, "status_code": None, "body": None, "error": str(e)}


obico_detection_service = ObicoDetectionService()
