"""Obico AI print-failure detection service.

Polls a self-hosted Obico ML API with snapshots from each monitored printer
while a print is running, smooths scores over time, and dispatches a configured
action (notify / pause / pause_and_off) when a sustained failure is detected.

See `obico_smoothing.py` for the per-print EWM + rolling-mean math.
"""

import asyncio
import json
import logging
import secrets
import time
from collections import deque
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.printer import Printer
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
SNAPSHOT_CAPTURE_TIMEOUT = 20  # seconds — we control this, not Obico
FRAME_CACHE_TTL = 30.0  # seconds — Obico usually fetches within 1s of receiving the URL

# Module-level one-shot frame cache. Obico's ML API is GET-only (/p/?img=URL) and
# fetches the URL itself with a hardcoded 5s read timeout. We capture locally first,
# stash the JPEG under a random nonce, and hand Obico a URL that serves the cached
# bytes instantly — so the 5s ceiling never races RTSP keyframe wait.
_frame_cache: dict[str, tuple[bytes, float]] = {}
_frame_cache_lock = asyncio.Lock()


def auth_headers(token: str | None) -> dict[str, str]:
    """Bearer header for the ML API, or nothing when no token is configured.

    Obico's ML API gates ``/p/`` behind ``ML_API_TOKEN`` (``ml_api/auth.py``):
    with the variable set it answers a bare 401 to any request whose
    ``Authorization`` header isn't ``Bearer <token>``, and with it unset it
    ignores the header entirely. Sending nothing when unconfigured keeps the
    request byte-identical to what shipped before the setting existed.
    """
    token = (token or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _prune_frame_cache() -> None:
    """Drop entries older than FRAME_CACHE_TTL. Called under the cache lock."""
    now = time.monotonic()
    expired = [k for k, (_b, ts) in _frame_cache.items() if now - ts > FRAME_CACHE_TTL]
    for k in expired:
        _frame_cache.pop(k, None)


async def stash_frame(data: bytes) -> str:
    """Store JPEG bytes and return a URL-safe nonce that serves them once."""
    nonce = secrets.token_urlsafe(32)
    async with _frame_cache_lock:
        _prune_frame_cache()
        _frame_cache[nonce] = (data, time.monotonic())
    return nonce


async def pop_frame(nonce: str) -> bytes | None:
    """Return and remove a cached frame by nonce; None if missing or expired."""
    async with _frame_cache_lock:
        _prune_frame_cache()
        entry = _frame_cache.pop(nonce, None)
    if entry is None:
        return None
    data, ts = entry
    if time.monotonic() - ts > FRAME_CACHE_TTL:
        return None
    return data


class ObicoDetectionService:
    """Singleton service that polls the ML API and acts on sustained failures."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        # printer_id -> PrintState (reset when a new print starts)
        self._states: dict[int, PrintState] = {}
        # printer_id -> task_name active when state was created (used to detect new prints)
        self._state_keys: dict[int, str] = {}
        # printer_id -> last classification ("safe"/"warning"/"failure").
        # Only written after an inference actually came back, so a missing entry
        # means "we have no verdict", which is not the same as "safe" (#2952).
        self._last_class: dict[int, str] = {}
        # printer_id -> why the most recent poll produced no verdict, or absent
        # when the last poll succeeded. Per-printer rather than global so a card
        # can say what went wrong for *that* printer.
        self._errors: dict[int, str] = {}
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
            "obico_ml_token",
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
            "ml_token": (rows.get("obico_ml_token") or "").strip(),
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

                await self._poll_once(settings)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Obico detection loop error: %s", e)
                self._last_error = str(e) or type(e).__name__
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
                self._last_class.pop(printer_id, None)
                self._errors.pop(printer_id, None)
                continue

            await self._check_printer(printer_id, status, settings)

    async def _capture_frame(self, printer_id: int) -> bytes | None:
        """Capture one JPEG frame from the printer camera. Returns None on failure."""
        # Late import to avoid cycles at module load time
        from backend.app.services.camera import capture_camera_frame_bytes
        from backend.app.services.external_camera import capture_frame as capture_external_frame

        async with async_session() as db:
            printer = await db.get(Printer, printer_id)
        if printer is None:
            self._last_error = f"Printer {printer_id} not found"
            return None

        if printer.external_camera_enabled and printer.external_camera_url:
            # Same rule as the built-in branch below, which this used to skip:
            # an external camera is single-reader too, so polling while a viewer
            # is attached just fails (#2707).
            from backend.app.api.routes.camera import live_frame_for_capture

            defer, buffered = live_frame_for_capture(printer_id)
            if defer:
                if buffered:
                    return buffered
                logger.info(
                    "Obico: viewer attached for printer %s but buffer empty; "
                    "skipping this poll to avoid competing camera handle (#2707)",
                    printer_id,
                )
                return None
            return await capture_external_frame(
                printer.external_camera_url,
                printer.external_camera_type,
                timeout=SNAPSHOT_CAPTURE_TIMEOUT,
                snapshot_url=printer.external_camera_snapshot_url,
            )

        # Reuse the fan-out broadcaster's buffered frame when a viewer is
        # already watching — avoids opening a second concurrent RTSP socket
        # on printers that allow only one camera connection (e.g. X2D
        # firmware 01.01.00.00; see #1271). Buffered frame is <1s old while
        # a viewer is connected.
        #
        # When a viewer is attached but no frame is buffered yet (startup
        # race, mid-reconnect), we DELIBERATELY skip this poll cycle instead
        # of falling through to capture_camera_frame_bytes. Opening a fresh
        # RTSP/chamber socket would compete with the live viewer and kick
        # the fan-out connection on most firmwares — exactly the freeze
        # reported in #1348. The poll loop retries in ~10s.
        from backend.app.api.routes.camera import is_stream_active, try_get_active_buffered_frame

        if is_stream_active(printer_id):
            buffered = try_get_active_buffered_frame(printer_id)
            if buffered:
                return buffered
            logger.info(
                "Obico: viewer attached for printer %s but buffer empty; skipping this poll to avoid competing camera socket (#1348)",
                printer_id,
            )
            return None

        return await capture_camera_frame_bytes(
            ip_address=printer.ip_address,
            access_code=printer.access_code,
            model=printer.model,
            timeout=SNAPSHOT_CAPTURE_TIMEOUT,
        )

    def _no_verdict(self, printer_id: int, reason: str) -> None:
        """Record that this poll produced no verdict for ``printer_id``.

        Kept separate from the classification so the status surface can say
        "not checking" instead of inheriting the previous verdict — or, worse,
        the default "safe" a printer used to get before its first inference.
        """
        self._errors[printer_id] = reason
        self._last_error = reason
        logger.warning(reason)

    async def _check_printer(self, printer_id: int, status, settings: dict):
        task_name = getattr(status, "task_name", None) or getattr(status, "subtask_name", "") or ""
        key = f"{task_name}"
        if self._state_keys.get(printer_id) != key:
            self._states[printer_id] = PrintState()
            self._state_keys[printer_id] = key
            self._action_fired[printer_id] = False

        # Capture locally first, then hand Obico a nonce URL that returns the
        # cached bytes instantly. Obico's ML API is GET-only (/p/?img=URL) with a
        # hardcoded 5s read timeout which would otherwise race our /camera/snapshot
        # keyframe wait.
        frame = await self._capture_frame(printer_id)
        if not frame:
            self._no_verdict(printer_id, f"Failed to capture snapshot for printer {printer_id}")
            return

        external_url = settings.get("external_url") or ""
        if not external_url:
            self._no_verdict(
                printer_id,
                "external_url setting is empty — Obico's ML API needs a reachable URL to fetch the snapshot from. "
                "Set Settings → General → External URL.",
            )
            return

        nonce = await stash_frame(frame)
        snapshot_url = f"{external_url}/api/v1/obico/cached-frame/{nonce}"
        ml_url = f"{settings['ml_url']}/p/"

        try:
            async with httpx.AsyncClient(timeout=DETECTION_TIMEOUT) as client:
                resp = await client.get(
                    ml_url,
                    params={"img": snapshot_url},
                    headers=auth_headers(settings.get("ml_token")),
                )
                if resp.status_code == 401:
                    # The server runs with ML_API_TOKEN set and rejected ours.
                    # Say so plainly: the health endpoint is ungated, so "Test
                    # Connection" passes against exactly this configuration and
                    # a raw 401 gives the user nothing to act on (#2733).
                    #
                    # Obico's auth decorator runs before the handler, so a call
                    # rejected here leaves no trace in the ML API's own log —
                    # which is how #2952 came to be reported as "the loop never
                    # calls the ML API" while it was calling it every 10s.
                    self._no_verdict(
                        printer_id,
                        "Obico ML API rejected the token (401). Set Settings → Failure Detection → "
                        "ML API Token to the ML_API_TOKEN the server runs with, or clear ML_API_TOKEN "
                        "on the server.",
                    )
                    return
                resp.raise_for_status()
                payload = resp.json()
        except Exception as e:
            detail = str(e) or type(e).__name__
            self._no_verdict(printer_id, f"ML API call failed for printer {printer_id}: {detail}")
            return

        detections = payload.get("detections", []) if isinstance(payload, dict) else []
        current_p = score_from_detections(detections)
        state = self._states[printer_id]
        score = state.update(current_p)
        verdict = classify(score, settings["sensitivity"])
        self._last_class[printer_id] = verdict
        # A successful capture + ML call clears any transient error from previous
        # polls (typical case: cold-start RTSP timeout on first frame after startup,
        # followed by healthy polls that otherwise leave the banner stuck in the UI).
        self._errors.pop(printer_id, None)
        self._last_error = None

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
            self._last_error = f"Action dispatch failed: {e or type(e).__name__}"
            logger.error(self._last_error)

    # ---- queries ----

    def get_per_printer(self) -> dict:
        """Live classification per actively monitored printer.

        Only printers with a running, monitored print have a state entry, so
        consumers get "show nothing" for idle printers for free.

        Four classes, and the two non-verdict ones matter as much as the rest:

        ``error``    the most recent poll produced no verdict. ``error`` carries
                     the reason — a rejected token, an unreachable ML API, a
                     camera that would not yield a frame, an unset External URL.
        ``unknown``  monitored, but no inference has come back yet. The state
                     entry is created when the print is first seen, which is
                     before the first capture, so this is the honest answer for
                     that window.
        ``safe`` / ``warning`` / ``failure``
                     an actual verdict from an actual inference.

        This used to default to ``safe`` whenever no verdict had been recorded,
        so a printer whose detection had never once succeeded rendered exactly
        like a healthy one: a green badge reading "Safe" at score 0.000. That is
        the worst possible failure mode for a safety feature — it asserts the
        print is being watched precisely when it is not (#2952).
        """
        result = {}
        for pid, state in self._states.items():
            error = self._errors.get(pid)
            if error:
                verdict = "error"
            else:
                verdict = self._last_class.get(pid) or "unknown"
            result[pid] = {
                "class": verdict,
                "frame_count": state.frame_count,
                "score": round(state.ewm_mean, 4),
                "error": error,
            }
        return result

    def get_status(self, sensitivity: str = "medium") -> dict:
        # Report the thresholds for the configured sensitivity, not a hardcoded
        # "medium" — otherwise the Status panel always shows the medium row
        # regardless of the user's selection (#1469). thresholds() falls back
        # to the medium multiplier for any unrecognized value.
        low, high = thresholds(sensitivity)
        return {
            "is_running": self._task is not None and not self._task.done(),
            "last_error": self._last_error,
            "per_printer": self.get_per_printer(),
            "thresholds": {"low": low, "high": high},
            "history": list(self._history),
        }

    async def test_connection(self, url: str, token: str = "") -> dict:
        """Ping the ML API and check the token. Returns {ok, status_code, body, error, auth_ok}.

        The stored ``obico_ml_url`` setting is validated at the schema layer,
        but this route takes its URL from the request body, so the same
        LAN-service policy has to be applied here or the guard is trivially
        sidestepped by testing a URL instead of saving it. The response body
        is returned to the caller (it is the health signal — the endpoint
        answers "ok"), which is exactly why the destination must be inside
        policy before the request is made.

        ``token`` is used verbatim — resolving "not supplied" to the saved
        setting is the route's job, so this stays a pure outbound call.

        Health alone cannot answer whether the token works, because Obico
        gates ``/p/`` but leaves ``/hc/`` open — which is how a token-protected
        server passed this test while every detection call came back 401
        (#2733). So a second, side-effect-free probe follows: ``/p/`` with no
        ``img`` parameter. The auth decorator runs before the handler, so 401
        means the token was rejected and 422 ("Invalid request params") means
        it was accepted. No inference work is done either way.
        """
        from backend.app.api.routes._url_safety import assert_safe_lan_service_url

        try:
            assert_safe_lan_service_url(url, label="Obico ML URL")
        except ValueError as exc:
            return {"ok": False, "status_code": None, "body": None, "error": str(exc), "auth_ok": None}

        headers = auth_headers(token)

        base = url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
                resp = await client.get(f"{base}/hc/", headers=headers)
                body = resp.text.strip()
                healthy = resp.status_code == 200 and body.lower() == "ok"
                if not healthy:
                    return {
                        "ok": False,
                        "status_code": resp.status_code,
                        "body": body,
                        "error": None,
                        "auth_ok": None,
                    }

                auth_ok: bool | None
                try:
                    probe = await client.get(f"{base}/p/", headers=headers)
                    auth_ok = probe.status_code != 401
                except Exception:
                    # The health check already succeeded, so don't fail the
                    # whole test on the probe — report the token as unknown.
                    auth_ok = None
        except Exception as e:
            return {
                "ok": False,
                "status_code": None,
                "body": None,
                "error": str(e) or type(e).__name__,
                "auth_ok": None,
            }

        if auth_ok is False:
            return {
                "ok": False,
                "status_code": 401,
                "body": body,
                "error": (
                    "The ML API is reachable but rejected the token. It runs with ML_API_TOKEN set — "
                    "enter that value as the ML API Token, or clear ML_API_TOKEN on the server."
                ),
                "auth_ok": False,
            }
        return {"ok": True, "status_code": resp.status_code, "body": body, "error": None, "auth_ok": auth_ok}


obico_detection_service = ObicoDetectionService()
