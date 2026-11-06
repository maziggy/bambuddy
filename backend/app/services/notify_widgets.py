"""Persistent Notify! Lock Screen widgets, separate from Live Activities.

These widgets hold stored values: iOS chooses when to refresh them (usually about
15 minutes). Bambuddy updates changed content at most once per minute, never
promises a ticking countdown, and addresses only its own saved WG identifiers.
"""

import asyncio
import json
import logging
from datetime import timedelta

import httpx
from sqlalchemy import and_, case, delete, or_, select, update

from backend.app.core.database import async_session
from backend.app.models.notification import NotificationProvider
from backend.app.models.notification_lock_screen_widget import NotificationLockScreenWidget
from backend.app.models.printer import Printer
from backend.app.services.notify_client import NotifyClient, NotifyError
from backend.app.services.notify_live_activities import PrintSnapshot, _credential_key as _fingerprint, _now
from backend.app.services.notify_worker import NotifyWorkerLifecycle

logger = logging.getLogger(__name__)
_ERROR_PREFIX = "Notify! Lock Screen widget: "
_CAPACITY_ERROR_PREFIX = "Notify! widget capacity: "
_INTERVAL = 60


def _credential_key(config):
    return _fingerprint(
        {
            "device_id": str(config.get("device_id", "")).strip(),
            "token": str(config.get("token", "")).strip(),
        }
    )


def widget_content(printer_name: str, state) -> dict:
    """A static, compact status card. Faults replace the gauge with a diagnosis."""
    content = {
        "title": printer_name.replace("\x00", "")[:120] or "Bambuddy",
        "value": "Offline",
        "unit": None,
        "detail": "Printer is not connected",
        "symbol": "printer.fill",
        "tint": "#00AE42",
        "progress": None,
    }
    if state is not None and state.connected:
        # Shared parsing keeps HMS advisory filtering and preparation-stage
        # progress consistent with Bambuddy's Live Activity and printer card.
        snapshot = PrintSnapshot.from_state(state)
        tile = snapshot.content(printer_name)
        phase = tile["status"]
        if snapshot.fault:
            content.update(value="Error", detail=snapshot.fault[:120], tint="#E5484D")
        elif phase == "Paused":
            content.update(value="Paused", detail=f"Print paused at {snapshot.progress:.0f}%")
        elif snapshot.state in {"RUNNING", "PRINTING", "PREPARE", "SLICING"}:
            progress = tile["progress"]
            parts = [phase]
            if snapshot.layers:
                parts.append(f"Layer {snapshot.layer}/{snapshot.layers}")
            if snapshot.remaining_seconds > 0:
                minutes = snapshot.remaining_seconds // 60
                eta = f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"
                parts.append(f"About {eta} left")
            content.update(value=f"{progress:.0f}", unit="%", detail=" · ".join(parts)[:120], progress=progress)
        elif snapshot.state in {"FINISH", "COMPLETED"}:
            content.update(value="Complete", detail="Print completed", progress=100)
        elif snapshot.state == "FAILED":
            content.update(value="Failed", detail="Print failed", tint="#E5484D")
        elif snapshot.state in {"CANCELLED", "ABORTED", "STOPPED"}:
            content.update(value="Stopped", detail="Print stopped")
        elif snapshot.state == "IDLE":
            content.update(value="Idle", detail="Ready to print")
        else:
            content.update(value="Connecting", detail="Waiting for printer status")
    # Notify caps the serialized merged content at 1024 UTF-8 bytes. Reserve
    # space rather than letting emoji printer names make every update fail.
    while len(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()) > 950:
        field = "detail" if len(content["detail"]) > 20 else "title"
        content[field] = content[field][:-5]
    return content


def _status(printer_id):
    from backend.app.services.printer_manager import printer_manager

    return printer_manager.get_status(printer_id)


class NotifyWidgetService(NotifyWorkerLifecycle):
    ownership_model = NotificationLockScreenWidget
    feature_field = "lock_screen_widgets"
    worker_name = "notify-lock-screen-widgets"
    _cleanup_key = staticmethod(_credential_key)

    def __init__(self, session_factory=None, client=None, state_getter=None):
        self._session = session_factory or async_session
        self._client = client
        self._http: httpx.AsyncClient | None = None
        self._state = state_getter or _status
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._init_worker()
        self._capacity_devices: set[str] = set()
        self._retry_capacity = False
        self._over_capacity_providers: set[int] = set()

    def providers_changed(self):
        self._retry_capacity = True
        self._capacity_devices.clear()
        super().providers_changed()

    async def _api(self):
        if self._client is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(30, connect=5), follow_redirects=False, headers={"User-Agent": "Bambuddy/1.0"}
            )
            self._client = NotifyClient(self._http)
        return self._client

    async def _save(self, row):
        async with self._session() as db:
            values = {column.name: getattr(row, column.name) for column in row.__table__.columns if column.name != "id"}
            # A provider edit may request deletion while this worker is awaiting
            # HTTP. Preserve that intent while capturing a just-returned WG ID,
            # otherwise a rapid off/on would silently cancel the user's cleanup.
            values["state"] = case((NotificationLockScreenWidget.state == "deleting", "deleting"), else_=row.state)
            await db.execute(
                update(NotificationLockScreenWidget).where(NotificationLockScreenWidget.id == row.id).values(**values)
            )
            await db.commit()

    async def _remove(self, row):
        async with self._session() as db:
            await db.execute(delete(NotificationLockScreenWidget).where(NotificationLockScreenWidget.id == row.id))
            await db.commit()
        row.state = "deleted"

    async def tick(self):
        async with self._lock:
            async with self._session() as db:
                providers = (
                    await db.scalars(select(NotificationProvider).where(NotificationProvider.provider_type == "notify"))
                ).all()
                enabled = any(self._eligible(provider) for provider in providers)
                previously_enabled = self._provider_enabled
                self._provider_enabled = enabled
                if not enabled and not previously_enabled and not self._cleanup_once and not self._cleanup_pending:
                    return
                rows = (await db.scalars(select(NotificationLockScreenWidget))).all()
                self._working_rows = rows
                printers = (
                    dict((await db.execute(select(Printer.id, Printer.name).where(Printer.is_active.is_(True)))).all())
                    if enabled
                    else {}
                )

            retry_capacity, self._retry_capacity = self._retry_capacity, False
            if not retry_capacity:
                self._capacity_devices.update(row.credential_key for row in rows if row.state == "capacity")
            for provider in providers:
                config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config
                enabled = (
                    provider.enabled
                    and config.get("lock_screen_widgets") is True
                    and not str(config.get("device_id", "")).strip().upper().startswith(("GRP", "WB", "MC"))
                )
                credential = _credential_key(config)
                owned = [r for r in rows if r.provider_id == provider.id]
                selected = {pid: name for pid, name in printers.items() if provider.printer_id in (None, pid)}
                if len(selected) > 10:
                    # Preserve already-owned widgets first; adding an eleventh
                    # printer must not delete one merely because query order changed.
                    retained = [row.printer_id for row in owned if row.printer_id in selected]
                    chosen = list(dict.fromkeys([*retained, *sorted(selected)]))[:10]
                    selected = {pid: selected[pid] for pid in chosen}
                    if provider.id not in self._over_capacity_providers:
                        logger.warning(
                            "Notify widget provider %s exceeds ten active printers; select a printer", provider.id
                        )
                        self._over_capacity_providers.add(provider.id)
                else:
                    self._over_capacity_providers.discard(provider.id)
                for row in owned:
                    if retry_capacity and row.state == "capacity":
                        row.state, row.failures, row.next_attempt_at = "pending", 0, None
                        await self._save(row)
                    if self._retired_row(row):
                        continue
                    if row.credential_key != credential:
                        continue  # Credential-edit hook cleans up with the previous token.
                    if row.state == "deleting" or not enabled or row.printer_id not in selected:
                        await self._delete(row, config)
                    elif row.state == "uncertain" and not row.failures:
                        await self._failure(
                            row, NotifyError("Previous creation was interrupted", delivery_state="unknown")
                        )
                    elif row.state not in {"uncertain", "suppressed", "capacity"}:
                        await self._sync(
                            row, config, widget_content(selected[row.printer_id], self._state(row.printer_id))
                        )
                if not enabled:
                    continue
                for printer_id, name in selected.items():
                    if credential in self._capacity_devices:
                        break
                    if any(r.printer_id == printer_id for r in owned):
                        continue
                    row = NotificationLockScreenWidget(
                        provider_id=provider.id,
                        printer_id=printer_id,
                        credential_key=credential,
                        state="pending",
                        content="{}",
                        created_at=_now(),
                        failures=0,
                    )
                    if self._retired_row(row):
                        continue
                    self._working_rows.append(row)
                    async with self._session() as db:
                        db.add(row)
                        await db.commit()
                    await self._sync(row, config, widget_content(name, self._state(printer_id)))

            self._cleanup_pending = any(
                row.state == "deleting" and row.provider_id in {p.id for p in providers} for row in rows
            )

    async def _sync(self, row, config, content):
        if self._retired_row(row):
            return
        if row.next_attempt_at and row.next_attempt_at > _now():
            return
        encoded = json.dumps(content, ensure_ascii=False, sort_keys=True)
        if row.widget_id and row.content == encoded:
            return
        api = await self._api()
        create_attempted = False
        try:
            if row.widget_id:
                await api.update_widget(row.widget_id, config.get("token", ""), content)
            else:
                # Device and widget IDs can both be eight characters. Prove
                # the configured target is a device before POST: new=1 on a WG
                # URL still addresses that existing (possibly unrelated) widget.
                await api.list_widgets(config.get("device_id", ""), config.get("token", ""))
                # If the process dies after this commit, the create may have
                # happened. The next process must never repeat new=1 blindly.
                row.state = "uncertain"
                await self._save(row)
                if self._retired_row(row):
                    row.state = "pending"  # No create was sent; cleanup need not warn of an unknown widget.
                    return
                create_attempted = True
                result = await api.create_widget(config.get("device_id", ""), config.get("token", ""), content)
                row.widget_id = result["widgetId"]
            row.state = "active"
            row.content = encoded
            row.failures = 0
            row.last_sent_at = _now()
            row.next_attempt_at = _now() + timedelta(seconds=_INTERVAL)
            await self._save(row)
            async with self._session() as db:
                # A successful widget update must not clear a push/Live Activity error.
                await db.execute(
                    update(NotificationProvider)
                    .where(
                        NotificationProvider.id == row.provider_id,
                        or_(
                            NotificationProvider.last_error.startswith(_ERROR_PREFIX),
                            and_(
                                NotificationProvider.last_error.startswith(_CAPACITY_ERROR_PREFIX),
                                ~select(NotificationLockScreenWidget.id)
                                .where(
                                    NotificationLockScreenWidget.provider_id == row.provider_id,
                                    NotificationLockScreenWidget.state == "capacity",
                                )
                                .exists(),
                            ),
                        ),
                    )
                    .values(last_error=None, last_error_at=None)
                )
                await db.commit()
        except NotifyError as error:
            if not row.widget_id and not create_attempted:
                # A failed read cannot have created anything. Do not adopt any
                # WG that might appear in its response, and retry safely.
                error = NotifyError(
                    str(error),
                    status_code=error.status_code,
                    delivery_state="not-delivered",
                    retry_after_seconds=error.retry_after_seconds or _INTERVAL,
                )
            await self._failure(row, error)

    async def _failure(self, row, error):
        deleting = row.state == "deleting"
        widget_id = getattr(error, "widget_id", None)
        if widget_id:
            row.widget_id = widget_id
            row.state = "active"
        elif not row.widget_id and error.delivery_state == "unknown":
            row.state = "uncertain"
        elif error.status_code in (401, 403, 404):
            row.state = "suppressed"
        elif not row.widget_id:
            capacity = error.status_code == 400 and "10" in str(error.payload.get("message", ""))
            if capacity:
                row.state = "capacity"
                self._capacity_devices.add(row.credential_key)
            elif error.status_code in (429, 503) or error.retry_after_seconds is not None:
                row.state = "pending"
            else:
                row.state = "suppressed"
        if deleting:
            row.state = "deleting"
        row.failures = min((row.failures or 0) + 1, 5)
        delay = max(_INTERVAL * 2 ** (row.failures - 1), error.retry_after_seconds or 0)
        row.next_attempt_at = _now() + timedelta(seconds=delay)
        await self._save(row)
        message = (_CAPACITY_ERROR_PREFIX if row.state == "capacity" else _ERROR_PREFIX) + str(error)
        if row.state == "uncertain":
            message += (
                " Creation could not be confirmed. Check Notify! and remove any duplicate or unwanted widget, "
                "then turn Lock Screen widgets off and on to retry."
            )
        elif row.state == "capacity":
            message += " Free a widget slot in Notify! and save this provider to retry."
        elif error.retry_after_seconds:
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
        logger.warning("Notify widget request failed for provider %s (HTTP %s)", row.provider_id, error.status_code)

    async def _delete(self, row, config):
        if row.state != "deleting":
            row.state = "deleting"
            await self._save(row)
        if not row.widget_id:
            await self._remove(row)
            return
        if row.failures and row.next_attempt_at and row.next_attempt_at > _now():
            return
        if row.widget_id:
            try:
                await (await self._api()).delete_widget(row.widget_id, config.get("token", ""))
            except NotifyError as error:
                if error.status_code == 403:
                    # The API deliberately conflates a removed WG and invalid
                    # credentials. Only an authenticated device list can prove
                    # our exact widget is gone; never create/adopt from that list.
                    try:
                        existing = await (await self._api()).list_widgets(
                            config.get("device_id", ""), config.get("token", "")
                        )
                    except NotifyError as listing_error:
                        await self._failure(row, listing_error)
                        return
                    if any(widget.get("widgetId") == row.widget_id for widget in existing.get("widgets", [])):
                        await self._failure(row, error)
                        return
                elif error.status_code != 404:
                    await self._failure(row, error)
                    return
        await self._remove(row)

    async def cleanup_provider(self, provider_id: int, old_config: dict, *, captured_rows=None):
        """Transfer a token rotation, or clean up before old credentials vanish.

        Widgets have no expiry. If a different-device/delete cleanup fails,
        Notify's app must remove the saved widget manually. Never retain or log
        its credential-bearing updateUrl.
        """
        async with self._lock:
            async with self._session() as db:
                current = await db.get(NotificationProvider, provider_id)
                rows = (
                    await db.scalars(
                        select(NotificationLockScreenWidget).where(
                            NotificationLockScreenWidget.provider_id == provider_id,
                            NotificationLockScreenWidget.credential_key == _credential_key(old_config),
                        )
                    )
                ).all()
            if captured_rows is not None:
                merged = {row.id: row for row in rows}
                merged.update(
                    {row.id: row for row in captured_rows if row.credential_key == _credential_key(old_config)}
                )
                rows = list(merged.values())
            config = (
                (json.loads(current.config) if isinstance(current.config, str) else current.config) if current else {}
            )
            same_device = (
                current
                and current.provider_type == "notify"
                and str(config.get("device_id", "")).strip() == str(old_config.get("device_id", "")).strip()
                and _credential_key(config) != _credential_key(old_config)
            )
            if same_device:
                # A rotated token still addresses the same device and WG. Never
                # delete/recreate its permanent widget, or revive an uncertain
                # create whose identifier we never received.
                for row in rows:
                    row.credential_key = _credential_key(config)
                    row.failures, row.next_attempt_at = 0, None
                    if row.widget_id and row.state == "suppressed":
                        row.state = "active"
                        row.content = "{}"  # Verify the new token even if printer content is unchanged.
                    await self._save(row)
                return
            cleanup_failed = False
            for row in rows:
                if row.state == "deleted":
                    continue
                if row.widget_id:
                    try:
                        await (await self._api()).delete_widget(row.widget_id, old_config.get("token", ""))
                    except NotifyError:
                        cleanup_failed = True
                elif row.state == "uncertain":
                    cleanup_failed = True
                await self._remove(row)
            if cleanup_failed:
                logger.warning(
                    "Notify widget cleanup failed for provider %s; remove old widgets in Notify!", provider_id
                )
                # Keep this distinct from transient widget-send errors: success
                # on the new device does not prove an old permanent widget gone.
                async with self._session() as db:
                    await db.execute(
                        update(NotificationProvider)
                        .where(NotificationProvider.id == provider_id)
                        .values(
                            last_error="Notify! widget cleanup: Remove the previous device's Bambuddy widgets in Notify!; automatic cleanup could not be confirmed.",
                            last_error_at=_now(),
                        )
                    )
                    await db.commit()


notify_widgets = NotifyWidgetService()
