"""Persistent Notify! Live Activities, independent of push and digest selection.

Only the worker performs network I/O. MQTT callbacks copy their mutable state and
wake it; database sessions are closed before every HTTP call. A durable intent is
written *before* starting a tile: after a crash or an ambiguous network failure we
never blindly repeat a push-to-start and consume another of the five device slots.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy import delete, select, update

from backend.app.core.database import async_session
from backend.app.models.notification import NotificationProvider
from backend.app.models.notification_live_activity import NotificationLiveActivity
from backend.app.models.printer import Printer
from backend.app.services.notify_client import NotifyClient, NotifyError, _https_url

logger = logging.getLogger(__name__)
_RUNNING = {"RUNNING", "PRINTING", "PAUSE", "PREPARE", "SLICING"}
_TERMINAL = {"IDLE", "FINISH", "FAILED", "COMPLETED", "CANCELLED", "ABORTED", "STOPPED"}
_STOPPED = {"ended", "suppressed"}
_ROLLOVER = {"expired", "overdue", "abandoned"}
_INTERVAL = 60


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _date(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def _config(provider) -> dict:
    return json.loads(provider.config) if isinstance(provider.config, str) else provider.config or {}


def _credential_key(config: dict) -> str:
    return hashlib.sha256(f"{config.get('device_id', '')}:{config.get('token', '')}".encode()).hexdigest()


def _identity(state) -> str:
    job_id = str(getattr(state, "subtask_id", "") or "")
    if job_id and job_id != "0":
        return f"job:{job_id}"[:160]
    # raw_data contains MQTT deltas, so gcode_start_time may disappear on the
    # next push. The stable job ID is preferred; the filename fallback is given
    # a fresh generation by real start callbacks and adopted on restart.
    filename = getattr(state, "subtask_name", None) or getattr(state, "current_print", None) or ""
    digest = hashlib.sha256(str(filename).encode()).hexdigest()[:32]
    return f"file:{digest}"


def _printer_fault(state) -> str | None:
    """Match the printer card's actionable HMS filtering, including print_error.

    Bambu's MQTT parser folds print_error into hms_errors. Level-3 sixteen-digit
    HMS notices without actions (for example an open cover) are informational;
    eight-digit task-stopping print_error prompts at that level still count.
    """
    for error in getattr(state, "hms_errors", None) or []:
        severity = getattr(error, "severity", 0)
        description = str(getattr(error, "description", "") or "")
        actions = bool(getattr(error, "actions", None))
        notice = severity == 3 and len(str(getattr(error, "full_code", "") or "")) == 16
        if severity >= 1 and (actions or (description and not notice)):
            code = getattr(error, "full_code", "") or getattr(error, "code", "")
            return (description or f"Printer needs attention ({code})").replace("\x00", "")[:170]
    return None


@dataclass(frozen=True)
class PrintSnapshot:
    key: str
    connected: bool
    state: str
    filename: str
    progress: float
    remaining_seconds: int
    layer: int
    layers: int
    job_key: str | None = None
    fault: str | None = None
    temperatures: tuple = ()
    stage: int = -1

    @classmethod
    def from_state(cls, state):
        return cls(
            key=_identity(state),
            job_key=_identity(state) if _identity(state).startswith("job:") else None,
            connected=bool(state.connected),
            state=str(state.state or "").upper(),
            filename=str(getattr(state, "subtask_name", None) or getattr(state, "current_print", None) or "Print"),
            progress=max(0, min(100, float(state.progress or 0))),
            remaining_seconds=max(0, int(getattr(state, "remaining_time", 0) or 0) * 60),
            layer=int(state.layer_num or 0),
            layers=int(state.total_layers or 0),
            temperatures=tuple((getattr(state, "temperatures", {}) or {}).items()),
            stage=getattr(state, "stg_cur", -1),
            fault=_printer_fault(state),
        )

    def content(self, printer_name: str, config: dict | None = None) -> dict:
        config = config or {}
        phase = "Printing"
        if not self.connected:
            phase = "Printer offline"
        elif self.state == "PAUSE":
            phase = "Paused"
        elif self.state in ("PREPARE", "SLICING") or (
            self.state in {"RUNNING", "PRINTING"}
            and self.layer < 1
            and (self.layers > 0 or self.stage not in {0, -1, 255})
        ):
            phase = "Preparing"
        elif self.state in _TERMINAL:
            phase = {
                "FINISH": "Complete",
                "COMPLETED": "Complete",
                "FAILED": "Failed",
                "CANCELLED": "Stopped",
                "ABORTED": "Stopped",
                "STOPPED": "Stopped",
                "IDLE": "Finished",
            }[self.state]
        if self.connected and self.fault and self.state in _RUNNING:
            lower = self.fault.lower()
            runout = "filament" in lower and any(
                word in lower for word in ("run out", "ran out", "runout", "exhaust", "empty")
            )
            phase = "Filament runout" if runout else "Printer error"
        # Character and UTF-8 limits leave ample room for server-owned fields
        # within Notify's 2048-byte merged-content budget, including emoji names.
        filename = (
            "Print in progress"
            if config.get("live_activity_privacy") is True
            else self.filename.replace("\x00", "")[:120]
        )
        layer = f" · Layer {self.layer}/{self.layers}" if self.layers else ""
        progress = 0 if phase == "Preparing" else self.progress
        content = {
            "title": printer_name.replace("\x00", "")[:80] or "Bambuddy",
            "body": self.fault
            if self.connected and self.fault and self.state in _RUNNING
            else f"{filename}{layer}"[:170],
            "symbol": str(config.get("live_activity_symbol") or "printer.fill")[:64],
            "tint": config.get("live_activity_tint") or "#00AE42",
            "progress": 100 if phase == "Complete" else progress,
            "status": phase,
            "endsIn": self.remaining_seconds
            if self.connected
            and not self.fault
            and self.state in {"RUNNING", "PRINTING"}
            and 0 < self.remaining_seconds <= 86400
            else None,
        }
        style = config.get("live_activity_style", "bar")
        content.update(
            steps=10 if style == "segments" else None, step=int(progress // 10) if style == "segments" else None
        )
        if style == "none":
            content["progress"] = None
        if (
            config.get("live_activity_stage") is True
            and self.connected
            and not self.fault
            and self.state in {"RUNNING", "PRINTING"}
        ):
            from backend.app.services.bambu_mqtt import get_stage_name

            content["status"] = get_stage_name(self.stage)[:40] if self.stage >= 0 else phase
        eta = f"{self.remaining_seconds // 3600}h {(self.remaining_seconds // 60) % 60}m"
        content["trailing"] = eta if self.remaining_seconds > 86400 and phase == "Printing" else None
        # The default tile uses the native countdown. Metric chips are opt-in;
        # send null when disabled so merge-patch clears previously selected chips.
        metrics = []
        temperatures = dict(self.temperatures)
        for field in (config.get("live_activity_metrics") or [])[:6]:
            label, value, unit = "", None, ""
            if field == "progress":
                label, value = "Progress", f"{progress:.0f}%"
            elif field == "eta" and phase == "Printing" and self.remaining_seconds > 0:
                label, value = "Remaining", eta
            elif field == "layers" and self.layers:
                label, value = "Layer", f"{self.layer}/{self.layers}"
            elif field in {"nozzle", "bed", "chamber"} and temperatures.get(field) is not None:
                label, value, unit = field.title(), f"{temperatures[field]:.0f}", "°C"
            if value is not None:
                metrics.append({"label": label, "value": value[:16], "unit": unit})
        content["metrics"] = None if self.fault else metrics or None
        button = _https_url(config.get("live_activity_button_url"))
        content["button"] = (
            {"title": "Open Bambuddy", "url": button, "open": True} if button and len(button) <= 512 else None
        )

        # Notify adds timestamps when merging. Reserve 200 bytes for those;
        # shorten display text first, then drop optional cells/button if needed.
        def size():
            return len(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode())

        while size() > 1800 and len(content["body"]) > 20:
            content["body"] = content["body"][:-10]
        if size() > 1800:
            content["button"] = None
        while size() > 1800 and content["metrics"]:
            content["metrics"].pop()
        return content


class NotifyLiveActivityService:
    def __init__(self, session_factory=None, client=None):
        self._session = session_factory or async_session
        self._client = client
        self._http: httpx.AsyncClient | None = None
        self._snapshots: dict[int, PrintSnapshot] = {}
        self._new_fallbacks: dict[int, str] = {}
        self._started_at: dict[int, datetime] = {}
        self._progress_resets: dict[int, tuple[float, datetime]] = {}
        self._finished: list[tuple[int, str | None, str]] = []
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def observe(self, printer_id: int, state) -> None:
        snapshot = PrintSnapshot.from_state(state)
        reset = self._progress_resets.get(printer_id)
        if reset:
            initial, until = reset
            if (
                snapshot.state not in _RUNNING
                or snapshot.progress < initial
                or snapshot.progress <= 5
                or _now() >= until
            ):
                self._progress_resets.pop(printer_id, None)
            else:
                snapshot = replace(snapshot, progress=0, remaining_seconds=0)
        previous = self._snapshots.get(printer_id)
        if (
            previous
            and previous.state in _RUNNING
            and snapshot.state in _RUNNING
            and previous.filename == snapshot.filename
        ):
            # A delta may omit or belatedly supply a job ID. Retain the print's
            # established identity until an explicit start or a different ID.
            if snapshot.key.startswith("file:") or previous.key.startswith("file:"):
                snapshot = replace(snapshot, key=previous.key)
        self._snapshots[printer_id] = snapshot
        if previous is None or (previous.key, previous.state, previous.connected, previous.fault) != (
            snapshot.key,
            snapshot.state,
            snapshot.connected,
            snapshot.fault,
        ):
            self._wake.set()

    def print_started(self, printer_id: int, state, data: dict | None = None) -> None:
        if state is None:
            return
        snapshot = PrintSnapshot.from_state(state)
        job_id = (data or {}).get("subtask_id") or ((data or {}).get("raw_data") or {}).get("subtask_id")
        if job_id and str(job_id) != "0":
            snapshot = replace(snapshot, key=f"job:{job_id}", job_key=f"job:{job_id}")
        self._progress_resets[printer_id] = (snapshot.progress, _now() + timedelta(minutes=2))
        snapshot = replace(snapshot, progress=0, remaining_seconds=0)
        self._started_at[printer_id] = _now()
        self._new_fallbacks.pop(printer_id, None)
        if snapshot.key.startswith("file:"):
            self._new_fallbacks[printer_id] = f"{snapshot.key}:{uuid4().hex}"
        self._snapshots[printer_id] = snapshot
        self._wake.set()

    def print_finished(self, printer_id: int, data: dict) -> None:
        job = data.get("subtask_id") or (data.get("raw_data") or {}).get("subtask_id")
        snapshot = self._snapshots.get(printer_id)
        filename = data.get("subtask_name") or data.get("filename")
        key = f"job:{job}" if job and str(job) != "0" else None
        if key is None and snapshot and (not filename or snapshot.filename == filename):
            key = (
                self._new_fallbacks.get(printer_id, snapshot.key) if snapshot.key.startswith("file:") else snapshot.key
            )
        if key is None:
            # No unambiguous ownership: the real terminal state can reconcile
            # it, but a delayed event must never end the printer's next job.
            return
        self._finished.append((printer_id, key, str(data.get("status", "completed")).upper()))
        snapshot = self._snapshots.get(printer_id)
        if snapshot and key in (snapshot.key, snapshot.job_key):
            self._snapshots[printer_id] = replace(snapshot, state=self._finished[-1][2], remaining_seconds=0)
        self._wake.set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="notify-live-activities")

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._http:
            await self._http.aclose()
            self._http = None
            self._client = None

    async def _api(self):
        if self._client is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(30, connect=5), follow_redirects=False, headers={"User-Agent": "Bambuddy/1.0"}
            )
            self._client = NotifyClient(self._http)
        return self._client

    async def _run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Notify Live Activity reconciliation failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=30)
                # Coalesce MQTT bursts. Per-row deadlines bound actual requests.
                await asyncio.sleep(1)
            except TimeoutError:
                pass

    async def _save(self, row) -> None:
        async with self._session() as db:
            await db.merge(row)
            await db.commit()

    async def tick(self) -> None:
        async with self._lock:
            async with self._session() as db:
                providers = (
                    await db.scalars(select(NotificationProvider).where(NotificationProvider.provider_type == "notify"))
                ).all()
                rows = (await db.scalars(select(NotificationLiveActivity))).all()
                names = dict((await db.execute(select(Printer.id, Printer.name))).all())
            finished, self._finished = self._finished, []
            snapshots = self._snapshots.copy()
            for provider in providers:
                config = _config(provider)
                owned = [r for r in rows if r.provider_id == provider.id]
                enabled = bool(
                    provider.enabled
                    and config.get("live_activities") is True
                    and not str(config.get("device_id", "")).upper().startswith(("GRP", "MC", "WB"))
                )
                for row in owned:
                    snapshot = snapshots.get(row.printer_id)
                    selected = provider.printer_id is None or provider.printer_id == row.printer_id
                    if (
                        row.state == "ended"
                        and row.end_reason == "disabled"
                        and enabled
                        and selected
                        and snapshot
                        and snapshot.connected
                        and snapshot.state in _RUNNING
                        and self._matches(row, snapshot, row.printer_id)
                    ):
                        # An explicit re-enable may resume this job's tile. A
                        # user-dismissed/failed-to-appear tile remains suppressed.
                        row.state, row.activity_id, row.end_reason = "pending", None, None
                        row.failures, row.next_attempt_at, row.last_sent_at = 0, None, None
                        await self._save(row)
                    if (
                        snapshot
                        and snapshot.job_key
                        and not row.job_key
                        and self._matches(row, snapshot, row.printer_id)
                    ):
                        row.job_key = snapshot.job_key
                        await self._save(row)
                    if row.state in _STOPPED:
                        continue
                    if row.credential_key != _credential_key(config):
                        # The edit hook owns cleanup using the old credentials.
                        continue
                    if row.state == "ending":
                        await self._end(row, config, row.end_reason or "STOPPED")
                        continue
                    snapshot = snapshots.get(row.printer_id)
                    final = next(
                        (
                            status
                            for pid, key, status in finished
                            if pid == row.printer_id and key in (row.print_key, row.job_key)
                        ),
                        None,
                    )
                    selected = provider.printer_id is None or provider.printer_id == row.printer_id
                    same_print = snapshot and self._matches(row, snapshot, row.printer_id)
                    if final or not enabled or not selected or row.printer_id not in names:
                        await self._end(
                            row, config, final or ("DISABLED" if not enabled or not selected else "STOPPED")
                        )
                    elif snapshot and snapshot.connected and snapshot.state in _TERMINAL:
                        await self._end(row, config, snapshot.state)
                    elif snapshot and snapshot.connected and snapshot.state in _RUNNING and not same_print:
                        await self._end(row, config, "STOPPED")
                    else:
                        # A missing/offline printer is not evidence the print ended.
                        # Clear its countdown and keep its last known progress.
                        content = (
                            snapshot.content(names[row.printer_id], config) if snapshot else json.loads(row.content)
                        )
                        if snapshot is None or not snapshot.connected:
                            content.update(status="Printer offline", endsIn=None)
                        await self._sync(
                            row,
                            config,
                            content,
                            can_start=bool(
                                snapshot
                                and snapshot.connected
                                and snapshot.state in _RUNNING
                                and enabled
                                and not self._quiet(provider)
                            ),
                        )
                if not enabled or self._quiet(provider):
                    continue
                for printer_id, snapshot in snapshots.items():
                    if (
                        not snapshot.connected
                        or snapshot.state not in _RUNNING
                        or printer_id not in names
                        or provider.printer_id not in (None, printer_id)
                    ):
                        continue
                    # Finish the old tile before using another device slot for
                    # this printer. A failed DELETE remains durable and retries.
                    if any(r.printer_id == printer_id and r.state == "ending" and r.activity_id for r in owned):
                        continue
                    if any(self._matches(r, snapshot, printer_id) for r in owned):
                        continue
                    key = (
                        self._new_fallbacks.get(printer_id, snapshot.key)
                        if snapshot.key.startswith("file:")
                        else snapshot.key
                    )
                    row = NotificationLiveActivity(
                        provider_id=provider.id,
                        printer_id=printer_id,
                        print_key=key,
                        print_name=snapshot.filename[:255],
                        job_key=snapshot.job_key,
                        credential_key=_credential_key(config),
                        state="pending",
                        content=json.dumps(snapshot.content(names[printer_id], config)),
                        created_at=_now(),
                    )
                    async with self._session() as db:
                        db.add(row)
                        await db.commit()
                    await self._sync(row, config, json.loads(row.content), can_start=True)
            # Tombstones must outlive any plausible print, but need not grow forever.
            async with self._session() as db:
                await db.execute(
                    delete(NotificationLiveActivity).where(
                        NotificationLiveActivity.state.in_(_STOPPED),
                        NotificationLiveActivity.created_at < _now() - timedelta(days=30),
                        NotificationLiveActivity.printer_id.not_in(
                            [pid for pid, snapshot in snapshots.items() if snapshot.state in _RUNNING]
                        ),
                    )
                )
                await db.commit()

    def _matches(self, row, snapshot, printer_id):
        if row.printer_id != printer_id:
            return False
        if row.job_key and snapshot.job_key:
            return row.job_key == snapshot.job_key
        fallback = self._new_fallbacks.get(printer_id)
        if fallback and snapshot.key.startswith("file:"):
            return row.print_key == fallback
        if (
            row.print_key.startswith("file:")
            and snapshot.key.startswith("job:")
            and row.print_name == snapshot.filename[:255]
            and row.job_key is None
            and self._started_at.get(printer_id, row.created_at) <= row.created_at
        ):
            return True  # Restart after firmware finally supplied its job ID.
        return row.print_key == snapshot.key or (
            snapshot.key.startswith("file:") and row.print_key.startswith(snapshot.key + ":")
        )

    @staticmethod
    def _quiet(provider) -> bool:
        # Share Bambuddy's configured/local-time quiet-hours semantics. Digest
        # and event toggles deliberately never participate in this lifecycle.
        from backend.app.services.notification_service import notification_service

        return notification_service._is_in_quiet_hours(provider)

    async def _sync(self, row, config, content, *, can_start):
        now = _now()
        urgent = json.loads(row.content).get("status") != content.get("status")
        if row.next_attempt_at and row.next_attempt_at > now and not (urgent and row.last_sent_at and not row.failures):
            return
        # Store an absolute deadline rather than repeatedly resetting a frozen
        # MQTT minute estimate. Refresh only when that estimate or phase changes.
        seconds = content.get("endsIn")
        if seconds is None:
            row.eta_seconds, row.eta_deadline = None, None
        elif seconds != row.eta_seconds or row.eta_deadline is None:
            row.eta_seconds = seconds
            row.eta_deadline = now + timedelta(seconds=seconds)
        if row.eta_deadline:
            remaining = int((row.eta_deadline - now).total_seconds())
            content["endsIn"] = remaining if remaining > 0 else None
        api = await self._api()
        token = config.get("token", "")
        try:
            if row.activity_id:
                remote = await api.get_activity(row.activity_id, token)
                state, reason = remote.get("state"), remote.get("endReason")
                if state in {"ended", "dismissed"}:
                    if reason not in _ROLLOVER or not can_start:
                        row.state = "suppressed" if reason not in _ROLLOVER else "pending"
                        row.end_reason = reason or state
                        if row.state == "pending":
                            row.activity_id = None
                        await self._save(row)
                        return
                    row.activity_id = None
                    row.state = "pending"
                    await self._save(row)
                else:
                    row.state = state or "active"
                    row.expires_at = _date(remote.get("expiresAt")) or row.expires_at
                    if can_start and row.expires_at and row.expires_at <= now:
                        # Poll first: a dismissed tile must never be rolled over.
                        await api.end_activity(row.activity_id, token)
                        row.activity_id = None
                        row.state = "pending"
                        await self._save(row)
                    else:
                        await api.update_activity(row.activity_id, token, content)
                        row.content = json.dumps(content)
                        row.failures = 0
                        row.last_sent_at = now
                        row.next_attempt_at = now + timedelta(seconds=_INTERVAL)
                        await self._save(row)
                        return
            if row.state == "uncertain":
                # No usable ID after a timeout/crash. The API has no client
                # idempotency key; do not adopt an unrelated device tile.
                row.state = "suppressed"
                row.end_reason = "unconfirmed-start"
                await self._save(row)
                return
            if not can_start or row.state in _STOPPED:
                return
            row.state = "uncertain"
            row.content = json.dumps(content)
            await self._save(row)
            result = await api.start_activity(config.get("device_id", ""), token, content)
            row.activity_id = result["activityId"]
            row.state = "starting"
            row.failures = 0
            row.expires_at = _date(result.get("expiresAt")) or now + timedelta(hours=8)
            row.last_sent_at = now
            row.next_attempt_at = now + timedelta(seconds=_INTERVAL)
            await self._save(row)
        except NotifyError as error:
            await self._failure(row, error)

    async def _failure(self, row, error):
        ending = row.state == "ending"
        if error.activity_id:
            row.activity_id = error.activity_id
            row.state = "starting"
        elif not row.activity_id and error.delivery_state == "unknown":
            row.state = "suppressed"
            row.end_reason = "unconfirmed-start"
        elif error.status_code in (401, 403):
            row.state = "suppressed"
            row.end_reason = "credentials"
        elif error.status_code == 410 and row.activity_id:
            # GET on the next pass determines whether this was a dismissal or
            # the eight-hour ceiling; never infer rollover from HTTP 410 alone.
            pass
        elif not row.activity_id:
            if error.status_code in (409, 429, 503) or error.retry_after_seconds is not None:
                row.state = "pending"
            elif error.status_code == 400 and "5" in str(error.payload.get("message", "")):
                row.state = "pending"  # Device cap: another printer may free a slot.
            else:
                row.state = "suppressed"
                row.end_reason = "start-rejected"
        if ending:
            row.state = "ending"
        row.failures = min((row.failures or 0) + 1, 5)
        row.next_attempt_at = _now() + timedelta(
            seconds=max(60 * 2 ** (row.failures - 1), error.retry_after_seconds or 0)
        )
        # Do not log upstream bodies, URLs, or credentials.
        logger.warning(
            "Notify Live Activity request failed for provider %s (HTTP %s)", row.provider_id, error.status_code
        )
        await self._save(row)
        message = str(error)
        if error.retry_after_seconds:
            message += f" Retry after {error.retry_after_seconds} seconds."
        async with self._session() as db:
            await db.execute(
                update(NotificationProvider)
                .where(NotificationProvider.id == row.provider_id)
                .values(
                    last_error=message,
                    last_error_at=_now(),
                )
            )
            await db.commit()

    async def _end(self, row, config, status):
        content = json.loads(row.content)
        content.update(
            status={"COMPLETED": "Complete", "FINISH": "Complete", "FAILED": "Failed"}.get(status, "Stopped"),
            endsIn=None,
        )
        if status in {"COMPLETED", "FINISH"}:
            content["progress"] = 100
        # Persist the terminal decision before I/O. A failed end must still
        # finish this exact job after restart or after the next job begins.
        if row.state != "ending":
            row.state = "ending"
            row.end_reason = status
            row.content = json.dumps(content)
            await self._save(row)
        if row.failures and row.next_attempt_at and row.next_attempt_at > _now():
            return
        if row.activity_id:
            try:
                await (await self._api()).end_activity(row.activity_id, config.get("token", ""), content)
            except NotifyError as error:
                if error.status_code not in (403, 404, 410):
                    await self._failure(row, error)
                    return
        row.state = "ended"
        row.end_reason = status.lower()
        row.content = json.dumps(content)
        await self._save(row)

    async def cleanup_provider(self, provider_id: int, old_config: dict) -> None:
        """Called after saving a credential/scope change or deleting a provider.

        Cleanup is best effort (the remote eight-hour ceiling bounds an outage).
        No old credential is retained in a second database location.
        """
        async with self._lock:
            async with self._session() as db:
                rows = (
                    await db.scalars(
                        select(NotificationLiveActivity).where(
                            NotificationLiveActivity.provider_id == provider_id,
                            NotificationLiveActivity.credential_key == _credential_key(old_config),
                        )
                    )
                ).all()
            for row in rows:
                if row.activity_id and row.state not in _STOPPED:
                    try:
                        await (await self._api()).end_activity(row.activity_id, old_config.get("token", ""))
                    except NotifyError:
                        logger.warning("Notify Live Activity cleanup failed for provider %s", provider_id)
            async with self._session() as db:
                await db.execute(
                    delete(NotificationLiveActivity).where(
                        NotificationLiveActivity.provider_id == provider_id,
                        NotificationLiveActivity.credential_key == _credential_key(old_config),
                    )
                )
                await db.commit()
        self._wake.set()


notify_live_activities = NotifyLiveActivityService()
