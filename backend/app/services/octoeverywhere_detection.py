"""OctoEverywhere AI print-failure detection service.

Each print owns a context so OctoEverywhere can combine camera snapshots over
time. Snapshots follow the configured interval, sped up to the API's
recommended interval while it suggests faster inspection, always respecting its
minimum. The API decides when to warn or pause; raw model scores are not used
for classification or actions.
"""

import asyncio
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import partial
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.settings import Settings
from backend.app.services.octoeverywhere_images import MAX_IMAGE_BYTES, prepare_frame

logger = logging.getLogger(__name__)

CREATE_CONTEXT_URL = "https://gadget-pv1-oeapi.octoeverywhere.com/api/gadget/v1/createcontext"
DETECTION_TIMEOUT = 30.0
POLL_INTERVAL = 5
DEFAULT_POLL_INTERVAL = 20
ERROR_RETRY_INTERVAL = 60
HISTORY_MAX = 50
# Gadget's confidence levels: 1 suggests a warning or pause soonest, with more
# false positives; 5 waits until the model is most certain.
CONFIDENCE_LEVELS = {"lowest": 1, "low": 2, "medium": 3, "high": 4, "highest": 5}
IP_RESTRICTED_ERROR = "OE_API_KEY_IP_RESTRICTED"
ACCOUNT_ERROR_CODES = {
    "OE_INVALID_API_KEY",
    "OE_API_KEY_DISABLED",
    "OE_API_KEY_BLOCKED_PAYMENT_FAILED",
    IP_RESTRICTED_ERROR,
    "OE_FREE_USAGE_LIMIT_REACHED",
}

API_ERRORS = {
    "OE_BAD_ARGS": "OctoEverywhere rejected the request arguments.",
    "OE_ARGS_PARSE_FAILED": "OctoEverywhere could not parse the request.",
    "OE_CONTEXT_RATE_LIMITED": "OctoEverywhere requested a delay before the next check. Detection will retry automatically.",
    "OE_IMAGE_DECODE_FAILED": "OctoEverywhere could not decode the camera snapshot.",
    "OE_INVALID_API_KEY": "OctoEverywhere rejected the Gadget API key. Check your API key in AI Failure Detection settings.",
    "OE_API_KEY_DISABLED": "The OctoEverywhere Gadget API key is disabled. Contact OctoEverywhere support to restore access.",
    "OE_API_KEY_BLOCKED_PAYMENT_FAILED": ("The OctoEverywhere API account is unavailable. Check the account status."),
    IP_RESTRICTED_ERROR: (
        "This IP address is already in use by another OctoEverywhere Gadget API key. "
        "Use the original key, or contact OctoEverywhere support."
    ),
    "OE_FREE_USAGE_LIMIT_REACHED": "API usage limit reached. Set up billing to continue.",
    "OE_BACKEND_THROTTLED": "OctoEverywhere is temporarily busy. Detection will retry automatically.",
    "OE_INTERNAL_ERROR": "OctoEverywhere encountered a service error. Detection will retry automatically.",
}


class _ApiError(Exception):
    """An actionable error with no remote response bodies or request secrets."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: float = 0,
        *,
        context_missing: bool = False,
        error_code: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.context_missing = context_missing
        self.error_code = _known_error_code(error_code)


@dataclass
class _Context:
    context_id: str
    process_url: str
    fallback_url: str


@dataclass
class _PrintState:
    print_id: str | None = None
    context: _Context | None = None
    next_check_at: float = 0
    last_processed_at: float | None = None
    interval: int | None = None
    recommended_interval: int | None = None  # Active only while faster inspections are suggested.
    frame_count: int = 0
    print_quality: int | None = None
    verdict: str = "unknown"
    error: str | None = None
    error_code: str | None = None
    failures: int = 0
    warning_fired: bool = False
    action_fired: bool = False


def _process_interval(server_interval: int | None, poll_interval: int, recommended_interval: int | None = None) -> int:
    """Use the configured cadence, sped up by an active timing hint, respecting the minimum."""
    # A faster-inspection hint may only shorten the interval the user chose.
    interval = poll_interval if recommended_interval is None else min(recommended_interval, poll_interval)
    return max(server_interval or interval, interval)


def _known_error_code(value) -> str | None:
    """Only expose documented, recognized codes, never arbitrary remote text."""
    return value if isinstance(value, str) and value in API_ERRORS else None


def _process_url(value) -> str:
    """Only send the API key to HTTPS endpoints owned by OctoEverywhere."""
    if not isinstance(value, str):
        raise _ApiError("OctoEverywhere returned an invalid processing URL.")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.hostname.endswith(".octoeverywhere.com")
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and not any(character.isspace() for character in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise _ApiError("OctoEverywhere returned an invalid processing URL.")
    return value


def _retry_after(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After", "")
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


def _response_payload(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not 200 <= response.status_code < 300:
        error_type = payload.get("ErrorType") if isinstance(payload, dict) else None
        # The v1 server uses this specific 500 response for a missing context,
        # including one removed by its 14-day TTL. Other internal errors must
        # retain their context so a transient outage does not reset the model.
        context_missing = (
            response.status_code == 500
            and error_type == "OE_INTERNAL_ERROR"
            and payload.get("ErrorDetails") == "Failed to get context object."
        )
        message = API_ERRORS.get(error_type) if isinstance(error_type, str) else None
        if context_missing:
            message = "OctoEverywhere print context is no longer available. Detection will reconnect automatically."
        if not message:
            if response.status_code in (401, 403):
                message = API_ERRORS["OE_INVALID_API_KEY"]
                error_type = "OE_INVALID_API_KEY"
            elif response.status_code == 429:
                message = API_ERRORS["OE_CONTEXT_RATE_LIMITED"]
            else:
                message = f"OctoEverywhere API request failed (HTTP {response.status_code})."
        raise _ApiError(
            message,
            response.status_code,
            _retry_after(response),
            context_missing=context_missing,
            error_code=error_type,
        )
    if not isinstance(payload, dict):
        raise _ApiError("OctoEverywhere returned an invalid response.", response.status_code)
    return payload


class OctoEverywhereDetectionService:
    """Singleton service that uploads snapshots from monitored, running prints."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._checks: dict[int, asyncio.Task] = {}
        self._states: dict[int, _PrintState] = {}
        self._configuration: tuple[str, str] | None = None
        self._generation = 0
        self._blocked_api_key: str | None = None
        self._blocked_error_code: str | None = None
        self._history: deque = deque(maxlen=HISTORY_MAX)
        self._last_error: str | None = None
        self._last_error_code: str | None = None

    def _set_last_error(self, reason: str | None, error_code: str | None = None):
        if self._blocked_error_code:
            reason = API_ERRORS[self._blocked_error_code]
            error_code = self._blocked_error_code
        self._last_error = reason
        self._last_error_code = _known_error_code(error_code) if reason else None

    def _refresh_last_error(self):
        state = next((state for state in self._states.values() if state.error), None)
        self._set_last_error(state.error if state else None, state.error_code if state else None)

    def _block_account(self, api_key: str, error_code: str):
        """Account failures stop every printer until the user explicitly retries."""
        self._blocked_api_key = api_key
        self._blocked_error_code = error_code
        self._generation += 1
        for task in self._checks.values():
            if task is not asyncio.current_task():
                task.cancel()
        self._refresh_last_error()
        logger.warning("OctoEverywhere inspections stopped: %s", API_ERRORS[error_code])

    def _clear_account_error(self):
        # Keep contexts, action tracking, and reserved server deadlines intact.
        self._blocked_api_key = None
        self._blocked_error_code = None
        self._refresh_last_error()

    async def start(self):
        if self._task is not None and not self._task.done():
            return
        logger.info("Starting OctoEverywhere detection service")
        self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("Stopped OctoEverywhere detection service")
        for task in self._checks.values():
            task.cancel()
        self._checks.clear()
        self._states.clear()
        self._generation += 1
        self._configuration = None
        self._clear_account_error()
        self._set_last_error(None)

    def reset_printer(self, printer_id: int):
        """Discard a completed print, including any inference still in flight."""
        task = self._checks.pop(printer_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._states.pop(printer_id, None)
        self._refresh_last_error()

    # ---- settings ----

    async def refresh_settings(self):
        """Invalidate in-flight decisions immediately after settings are saved."""
        self._generation += 1
        generation = self._generation
        task = self._task
        restart = task is not None and not task.done()
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # Printer checks run independently of the loop, so cancel and drain
        # them before applying settings or restarting the scheduler.
        checks = [task for task in self._checks.values() if task is not asyncio.current_task()]
        for task in checks:
            task.cancel()
        if checks:
            await asyncio.gather(*checks, return_exceptions=True)
        settings = await self._load_settings()
        if self._generation == generation:
            self._apply_settings(settings)
        if restart and self._task is None:
            await self.start()

    def _apply_settings(self, settings: dict):
        configuration = (settings["api_key"], settings["confidence"])
        if self._configuration != configuration:
            self._generation += 1
            for task in self._checks.values():
                if task is not asyncio.current_task():
                    task.cancel()
            if settings["api_key"] != self._blocked_api_key:
                self._clear_account_error()
            # A settings edit is still the same print. Replace its remote
            # context, but keep server pacing and actions already performed.
            for state in self._states.values():
                state.context = None
                state.recommended_interval = None
                state.frame_count = 0
                state.print_quality = None
                state.verdict = "unknown"
                state.error = None
                state.error_code = None
                state.failures = 0
            self._configuration = configuration
            self._set_last_error(None)
        if not settings["enabled"] or not settings["api_key"]:
            for printer_id in self._states.keys() | self._checks.keys():
                self.reset_printer(printer_id)
            self._set_last_error(None)
        if settings["enabled"] and not settings["api_key"]:
            self._set_last_error(
                "An OctoEverywhere Gadget API key is required. Add a key in Failure Detection settings."
            )
        if settings["enabled_printers"] is not None:
            for printer_id in self._states.keys() | self._checks.keys():
                if printer_id not in settings["enabled_printers"]:
                    self.reset_printer(printer_id)

    async def _load_settings(self) -> dict:
        keys = [
            "octoeverywhere_enabled",
            "octoeverywhere_api_key",
            "octoeverywhere_confidence",
            "octoeverywhere_action",
            "octoeverywhere_enabled_printers",
            "octoeverywhere_poll_interval",
        ]
        async with async_session() as db:
            result = await db.execute(select(Settings).where(Settings.key.in_(keys)))
            rows = {r.key: r.value for r in result.scalars().all()}

        enabled_printers_raw = rows.get("octoeverywhere_enabled_printers", "")
        enabled_printers = None
        if enabled_printers_raw:
            try:
                printer_ids = json.loads(enabled_printers_raw)
                enabled_printers = (
                    set(printer_ids)
                    if isinstance(printer_ids, list) and all(type(pid) is int for pid in printer_ids)
                    else set()
                )
            except (TypeError, ValueError):
                enabled_printers = set()

        try:
            poll_interval = int(rows.get("octoeverywhere_poll_interval", DEFAULT_POLL_INTERVAL))
            if not 5 <= poll_interval <= 30:
                poll_interval = DEFAULT_POLL_INTERVAL
        except (TypeError, ValueError):
            poll_interval = DEFAULT_POLL_INTERVAL

        return {
            "enabled": rows.get("octoeverywhere_enabled", "false").lower() == "true",
            "api_key": (rows.get("octoeverywhere_api_key") or "").strip(),
            "confidence": rows.get("octoeverywhere_confidence", "medium"),
            "action": rows.get("octoeverywhere_action", "notify"),
            "enabled_printers": enabled_printers,
            "poll_interval": poll_interval,
        }

    # ---- main loop ----

    async def _loop(self):
        while True:
            try:
                generation = self._generation
                settings = await self._load_settings()
                if generation != self._generation:
                    continue
                await self._poll_once(settings)
                # Enabled printers need whole-second scheduling for values such
                # as 21 seconds; disabled detection can check settings less often.
                await asyncio.sleep(1 if settings["enabled"] else POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # Never include exception text: transport errors can contain
                # request URLs and user-supplied credentials.
                self._set_last_error("OctoEverywhere detection is temporarily unavailable. Retrying automatically.")
                logger.error("OctoEverywhere detection loop error: %s", type(exc).__name__)
                await asyncio.sleep(ERROR_RETRY_INTERVAL)

    async def _poll_once(self, settings: dict):
        from backend.app.services.printer_manager import printer_manager

        self._apply_settings(settings)
        if not settings["enabled"] or not settings["api_key"]:
            return

        statuses = printer_manager.get_all_statuses()
        for printer_id in self._states.keys() | self._checks.keys():
            if printer_id not in statuses:
                self.reset_printer(printer_id)

        for printer_id, status in list(statuses.items()):
            if settings["enabled_printers"] is not None and printer_id not in settings["enabled_printers"]:
                self.reset_printer(printer_id)
                continue
            if not printer_manager.is_connected(printer_id):
                task = self._checks.get(printer_id)
                if task:
                    task.cancel()
                state = self._states.get(printer_id)
                if state:
                    self._no_verdict(printer_id, state, "Printer is disconnected. Detection will resume on reconnect.")
                continue
            print_state = getattr(status, "state", None)
            if print_state in ("FINISH", "FAILED", "IDLE"):
                self.reset_printer(printer_id)
                continue
            # Unknown or transitional status is not evidence that a print ended.
            # Keep its context, but only upload while the printer is running.
            if print_state != "RUNNING":
                task = self._checks.get(printer_id)
                if task:
                    task.cancel()
                continue
            state = self._states.get(printer_id)
            if state and not self._same_print(state, status):
                self.reset_printer(printer_id)
            task = self._checks.get(printer_id)
            if task is not None and not task.done():
                continue
            # One check per printer; a slow camera or API request must not
            # delay another printer's next inspection or lifecycle updates.
            task = asyncio.create_task(self._check_printer(printer_id, status, settings))
            self._checks[printer_id] = task
            task.add_done_callback(partial(self._check_done, printer_id))

    def _check_done(self, printer_id: int, task: asyncio.Task):
        # A canceled check may finish after its printer has started a new one.
        if self._checks.get(printer_id) is task:
            self._checks.pop(printer_id, None)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error("OctoEverywhere printer check failed: %s", type(error).__name__)

    @staticmethod
    def _print_id(status) -> str | None:
        subtask_id = getattr(status, "subtask_id", None)
        if subtask_id and str(subtask_id) != "0":
            return str(subtask_id)
        return None

    def _same_print(self, state: _PrintState, status) -> bool:
        """Learn late job IDs without discarding the context or forgetting a known ID."""
        print_id = self._print_id(status)
        if state.print_id and print_id and state.print_id != print_id:
            return False
        # Names and filenames may arrive late or change independently. Without
        # two distinct job IDs, rely on the print start/end callbacks instead.
        state.print_id = print_id or state.print_id
        return True

    def _is_current(self, printer_id: int, state: _PrintState, status, generation: int) -> bool:
        return (
            self._states.get(printer_id) is state
            and self._generation == generation
            and getattr(status, "state", None) == "RUNNING"
            and self._same_print(state, status)
        )

    async def _capture_frame(self, printer_id: int) -> bytes | None:
        from backend.app.services.failure_detection_camera import capture_detection_frame

        return await capture_detection_frame(printer_id)

    async def _create_context(self, client: httpx.AsyncClient, api_key: str, confidence: str) -> tuple[_Context, int]:
        level = CONFIDENCE_LEVELS.get(confidence, 3)
        response = await client.post(
            CREATE_CONTEXT_URL,
            headers={"X-API-Key": api_key},
            json={"WarningConfidenceLevel": level, "PauseConfidenceLevel": level},
        )
        payload = _response_payload(response)
        context_id = payload.get("ContextId")
        if not isinstance(context_id, str) or not context_id.strip():
            raise _ApiError("OctoEverywhere returned an invalid context.", response.status_code)
        return (
            _Context(
                context_id=context_id,
                process_url=_process_url(payload.get("ProcessRequestUrl")),
                fallback_url=_process_url(payload.get("FallbackProcessRequestUrl")),
            ),
            response.status_code,
        )

    def _no_verdict(self, printer_id: int, state: _PrintState, reason: str, error_code: str | None = None):
        if self._states.get(printer_id) is not state:
            return
        if state.error != reason:
            logger.warning("OctoEverywhere printer %s: %s", printer_id, reason)
        state.error = reason
        state.error_code = _known_error_code(error_code)
        self._set_last_error(reason, state.error_code)

    async def _check_printer(self, printer_id: int, status, settings: dict):
        generation = self._generation
        state = self._states.get(printer_id)
        if state is None or not self._same_print(state, status):
            state = _PrintState(print_id=self._print_id(status))
            self._states[printer_id] = state
            self._refresh_last_error()
        if self._blocked_error_code:
            return
        if state.last_processed_at is not None and not state.error:
            # Apply interval edits to an existing context on the next poll.
            # Failed/canceled requests clear last_processed_at, preserving their
            # reserved retry deadline regardless of the configured interval.
            state.next_check_at = state.last_processed_at + _process_interval(
                state.interval, settings["poll_interval"], state.recommended_interval
            )
        if time.monotonic() < state.next_check_at:
            return

        # Reserve this poll before awaiting camera/network I/O. Even a failed
        # capture must not create a hot loop or compete with a live camera.
        state.next_check_at = time.monotonic() + _process_interval(
            state.interval, settings["poll_interval"], state.recommended_interval
        )
        logger.debug(
            "OctoEverywhere inspection starting for printer %s (frame=%s, context_reused=%s)",
            printer_id,
            state.frame_count + 1,
            state.context is not None,
        )
        processing = False
        try:
            frame = await self._capture_frame(printer_id)
            if not frame:
                raise _ApiError("Could not capture a camera snapshot. Detection will retry automatically.")
            if len(frame) > MAX_IMAGE_BYTES:
                try:
                    frame = await asyncio.to_thread(prepare_frame, frame)
                except ValueError:
                    raise _ApiError("Could not reduce the camera snapshot to OctoEverywhere's 6 MiB image limit.")
            if not self._is_current(printer_id, state, status, generation):
                logger.debug(
                    "OctoEverywhere inspection discarded for printer %s: print status or settings changed", printer_id
                )
                return
            async with httpx.AsyncClient(timeout=DETECTION_TIMEOUT, follow_redirects=False) as client:
                if state.context is None:
                    context, _ = await self._create_context(client, settings["api_key"], settings["confidence"])
                    if not self._is_current(printer_id, state, status, generation):
                        logger.debug(
                            "OctoEverywhere inspection discarded for printer %s: print status or settings changed",
                            printer_id,
                        )
                        return
                    state.context = context
                    logger.debug("OctoEverywhere context created for printer %s", printer_id)
                if not self._is_current(printer_id, state, status, generation):
                    logger.debug(
                        "OctoEverywhere inspection discarded for printer %s: print status or settings changed",
                        printer_id,
                    )
                    return
                processing = True
                response = await client.post(
                    state.context.process_url,
                    headers={"X-API-Key": settings["api_key"]},
                    files={"image": ("snapshot.jpg", frame, "image/jpeg")},
                )
                payload = _response_payload(response)
                interval = payload.get("NextProcessIntervalSec")
                minimum_interval = interval.get("Minimum") if isinstance(interval, dict) else None
                if type(minimum_interval) is int and minimum_interval > 0:
                    # Preserve the latest minimum even if the assessment is
                    # invalid or canceled before its timing hint is accepted.
                    state.interval = minimum_interval
                    state.next_check_at = time.monotonic() + _process_interval(
                        minimum_interval, settings["poll_interval"], state.recommended_interval
                    )
                else:
                    raise _ApiError("OctoEverywhere returned an invalid processing interval.")
                faster_inspection_suggested = payload.get("FasterInspectionSuggested") is True
                recommended_interval = interval.get("Recommended") if faster_inspection_suggested else None
                # This hint is optional. Older responses and malformed hints
                # keep fixed timing without discarding a valid assessment.
                if type(recommended_interval) is not int or recommended_interval <= 0:
                    recommended_interval = None
                print_quality = payload.get("PrintQuality")
                warning = payload.get("WarningSuggested")
                pause = payload.get("PauseSuggested")
                if (
                    type(print_quality) is not int
                    or not 1 <= print_quality <= 10
                    or type(warning) is not bool
                    or type(pause) is not bool
                ):
                    raise _ApiError("OctoEverywhere returned an invalid print assessment.")
        except asyncio.CancelledError:
            # A request canceled during a settings edit may already have been
            # processed remotely. Preserve the previous minimum before retrying.
            state.next_check_at = max(
                state.next_check_at,
                time.monotonic()
                + _process_interval(state.interval, settings["poll_interval"], state.recommended_interval),
            )
            state.last_processed_at = None
            logger.debug("OctoEverywhere inspection canceled for printer %s", printer_id)
            raise
        except Exception as exc:
            state.last_processed_at = None
            # Account failures still apply if this print ended during the
            # request, but an old key or generation must not block new work.
            if (
                isinstance(exc, _ApiError)
                and exc.error_code in ACCOUNT_ERROR_CODES
                and generation == self._generation
                and (self._configuration is None or settings["api_key"] == self._configuration[0])
            ):
                self._block_account(settings["api_key"], exc.error_code)
                logger.debug(
                    "OctoEverywhere inspection failed for printer %s (error_code=%s); inspections stopped",
                    printer_id,
                    exc.error_code,
                )
                return
            if not self._is_current(printer_id, state, status, generation):
                state.next_check_at = max(
                    state.next_check_at,
                    time.monotonic()
                    + _process_interval(state.interval, settings["poll_interval"], state.recommended_interval),
                )
                logger.debug(
                    "OctoEverywhere inspection discarded for printer %s: print status or settings changed", printer_id
                )
                return
            if processing and state.context:
                # Keep the fallback for the rest of this context. Retrying on
                # the next scheduled poll also respects the API's rate limit.
                if isinstance(exc, _ApiError) and exc.context_missing:
                    state.context = None  # Recreate after backoff; preserve actions already performed.
                    state.recommended_interval = None
                elif isinstance(exc, httpx.RequestError) or (
                    isinstance(exc, _ApiError)
                    and exc.status_code is not None
                    and exc.status_code >= 500
                    and exc.error_code in (None, "OE_INTERNAL_ERROR", "OE_BACKEND_THROTTLED")
                ):
                    state.context.process_url = state.context.fallback_url
            state.failures += 1
            retry_after = exc.retry_after if isinstance(exc, _ApiError) else 0
            delay = max(
                _process_interval(state.interval, settings["poll_interval"], state.recommended_interval),
                ERROR_RETRY_INTERVAL * 2 ** min(state.failures - 1, 4),
                retry_after,
            )
            state.next_check_at = time.monotonic() + delay
            if isinstance(exc, _ApiError):
                reason = str(exc)
            elif isinstance(exc, httpx.TimeoutException):
                reason = "OctoEverywhere request timed out. Detection will retry automatically."
            elif isinstance(exc, httpx.RequestError):
                reason = "Could not reach OctoEverywhere. Check the internet connection."
            else:
                reason = "OctoEverywhere detection failed. Detection will retry automatically."
            self._no_verdict(printer_id, state, reason, exc.error_code if isinstance(exc, _ApiError) else None)
            logger.debug(
                "OctoEverywhere inspection failed for printer %s (error_code=%s, retry_in=%.1fs): %s",
                printer_id,
                state.error_code,
                delay,
                reason,
            )
            return

        if not self._is_current(printer_id, state, status, generation):
            logger.debug(
                "OctoEverywhere inspection discarded for printer %s: print status or settings changed", printer_id
            )
            return
        state.recommended_interval = recommended_interval
        state.last_processed_at = time.monotonic()
        state.next_check_at = state.last_processed_at + _process_interval(
            state.interval, settings["poll_interval"], state.recommended_interval
        )
        state.frame_count += 1
        state.print_quality = print_quality
        state.verdict = "failure" if pause else "warning" if warning else "safe"
        state.error = None
        state.error_code = None
        state.failures = 0
        self._refresh_last_error()
        logger.debug(
            "OctoEverywhere inspection result for printer %s: frame=%s, verdict=%s, print_quality=%s, "
            "warning_suggested=%s, pause_suggested=%s, faster_inspection_suggested=%s, "
            "minimum_interval=%ss, next_check_in=%ss",
            printer_id,
            state.frame_count,
            state.verdict,
            print_quality,
            warning,
            pause,
            faster_inspection_suggested,
            state.interval,
            _process_interval(state.interval, settings["poll_interval"], state.recommended_interval),
        )
        task_name = getattr(status, "task_name", None) or getattr(status, "subtask_name", "") or ""
        if state.verdict != "safe":
            self._history.appendleft(
                {
                    "printer_id": printer_id,
                    "task_name": task_name,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "class": state.verdict,
                    "print_quality": print_quality,
                }
            )

        if pause and not state.action_fired:
            action = settings["action"]
            # A pause or power cut always notifies, even after a warning: the
            # user needs to know the printer stopped. Notify-only would just
            # repeat the warning, so it stays silent.
            if action != "notify" or not state.warning_fired:
                await self._dispatch_action(printer_id, action, task_name, print_quality)
            state.action_fired = True
            state.warning_fired = True
        elif warning and not state.warning_fired:
            await self._dispatch_action(printer_id, "notify", task_name, print_quality)
            state.warning_fired = True

    async def _dispatch_action(self, printer_id: int, action: str, task_name: str, print_quality: int):
        from backend.app.services.octoeverywhere_actions import execute_action

        logger.warning(
            "OctoEverywhere: print issue on printer %s (quality=%s) — action=%s", printer_id, print_quality, action
        )
        try:
            await execute_action(printer_id, action, task_name, print_quality)
        except Exception as exc:
            self._set_last_error("OctoEverywhere could not execute the configured failure action.")
            logger.error("OctoEverywhere action dispatch failed: %s", type(exc).__name__)

    # ---- queries ----

    def get_per_printer(self) -> dict:
        return {
            printer_id: {
                "class": "error" if self._blocked_error_code or state.error else state.verdict,
                "print_quality": state.print_quality,
                "frame_count": state.frame_count,
                "error": API_ERRORS[self._blocked_error_code] if self._blocked_error_code else state.error,
                "error_code": self._blocked_error_code or state.error_code,
            }
            for printer_id, state in self._states.items()
        }

    def get_status(self) -> dict:
        return {
            "is_running": self._task is not None and not self._task.done(),
            "last_error": self._last_error,
            "last_error_code": self._last_error_code,
            "per_printer": self.get_per_printer(),
            "history": list(self._history),
        }

    async def test_connection(self, api_key: str, confidence: str = "medium") -> dict:
        """Validate a key with a context request; never upload a camera frame."""
        generation = self._generation
        api_key = api_key.strip()
        if not api_key:
            return {
                "ok": False,
                "status_code": None,
                "error": "An OctoEverywhere Gadget API key is required.",
                "error_code": None,
            }
        try:
            async with httpx.AsyncClient(timeout=DETECTION_TIMEOUT, follow_redirects=False) as client:
                _, status_code = await self._create_context(client, api_key, confidence)
        except _ApiError as exc:
            if (
                generation == self._generation
                and self._configuration
                and api_key == self._configuration[0]
                and exc.error_code in ACCOUNT_ERROR_CODES
            ):
                self._block_account(api_key, exc.error_code)
            return {"ok": False, "status_code": exc.status_code, "error": str(exc), "error_code": exc.error_code}
        except httpx.TimeoutException:
            return {"ok": False, "status_code": None, "error": "OctoEverywhere request timed out.", "error_code": None}
        except Exception:
            return {
                "ok": False,
                "status_code": None,
                "error": "Could not connect to OctoEverywhere.",
                "error_code": None,
            }
        if generation == self._generation and self._configuration and api_key == self._configuration[0]:
            self._clear_account_error()
        return {"ok": True, "status_code": status_code, "error": None, "error_code": None}


octoeverywhere_detection_service = OctoEverywhereDetectionService()
