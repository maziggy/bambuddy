"""Background dispatch for print/reprint jobs.

This service is separate from the app's print queue feature. It exists only to
decouple "send/start print" operations (FTP upload + start command) from API
request latency so the UI can continue immediately after dispatch.
"""

from __future__ import annotations

import asyncio
import logging
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.database import async_session
from backend.app.core.websocket import ws_manager
from backend.app.models.library import LibraryFile
from backend.app.models.printer import Printer
from backend.app.services.archive import ArchiveService
from backend.app.services.bambu_ftp import (
    delete_file_async,
    get_ftp_retry_settings,
    upload_file_async,
    with_ftp_retry,
)
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)


class DispatchJobCancelled(Exception):
    """Raised when a dispatch job is cancelled by the user."""


class DispatchEnqueueRejected(Exception):
    """Raised when a dispatch job should not be accepted."""


@dataclass(slots=True)
class PrintDispatchJob:
    id: int
    kind: Literal["reprint_archive", "print_library_file"]
    source_id: int
    source_name: str
    printer_id: int
    printer_name: str
    options: dict[str, Any] = field(default_factory=dict)
    requested_by_user_id: int | None = None
    requested_by_username: str | None = None


@dataclass(slots=True)
class ActiveDispatchState:
    job: PrintDispatchJob
    message: str
    upload_bytes: int | None = None
    upload_total_bytes: int | None = None


class BackgroundDispatchService:
    def __init__(self):
        self._queued_jobs: deque[PrintDispatchJob] = deque()
        self._dispatcher_task: asyncio.Task | None = None
        self._running_tasks: dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._job_event = asyncio.Event()
        self._next_job_id = 1
        self._active_jobs: dict[int, ActiveDispatchState] = {}
        self._cancel_requested_job_ids: set[int] = set()

        # Progress for the current "batch" (since queue became non-empty)
        self._batch_total = 0
        self._batch_completed = 0
        self._batch_failed = 0

    @staticmethod
    def _printer_is_busy_printing(printer_id: int) -> bool:
        state = printer_manager.get_status(printer_id)
        if not state:
            return False
        return state.state in ("RUNNING", "PAUSE", "PAUSED") and bool(state.gcode_file)

    async def start(self):
        async with self._lock:
            if self._dispatcher_task and not self._dispatcher_task.done():
                return
            self._dispatcher_task = asyncio.create_task(self._dispatcher_loop(), name="background-dispatch-dispatcher")
            logger.info("Background dispatch dispatcher started")

    async def stop(self):
        dispatcher: asyncio.Task | None = None
        running_tasks: list[asyncio.Task] = []
        async with self._lock:
            dispatcher = self._dispatcher_task
            self._dispatcher_task = None
            running_tasks = list(self._running_tasks.values())
            self._running_tasks.clear()
            self._active_jobs.clear()
            self._queued_jobs.clear()
            self._cancel_requested_job_ids.clear()
            self._job_event.set()

        if dispatcher:
            dispatcher.cancel()
        for task in running_tasks:
            task.cancel()

        if dispatcher:
            try:
                await dispatcher
            except asyncio.CancelledError:
                pass

        if running_tasks:
            await asyncio.gather(*running_tasks, return_exceptions=True)

        logger.info("Background dispatch dispatcher stopped")

    async def dispatch_reprint_archive(
        self,
        *,
        archive_id: int,
        archive_name: str,
        printer_id: int,
        printer_name: str,
        options: dict[str, Any],
        requested_by_user_id: int | None,
        requested_by_username: str | None,
    ) -> dict[str, Any]:
        return await self._dispatch(
            kind="reprint_archive",
            source_id=archive_id,
            source_name=archive_name,
            printer_id=printer_id,
            printer_name=printer_name,
            options=options,
            requested_by_user_id=requested_by_user_id,
            requested_by_username=requested_by_username,
        )

    async def get_state(self) -> dict[str, Any]:
        """Get current dispatch queue state snapshot for newly connected clients."""
        async with self._lock:
            return self._build_state_payload_unlocked()

    async def dispatch_print_library_file(
        self,
        *,
        file_id: int,
        filename: str,
        printer_id: int,
        printer_name: str,
        options: dict[str, Any],
        requested_by_user_id: int | None,
        requested_by_username: str | None,
    ) -> dict[str, Any]:
        return await self._dispatch(
            kind="print_library_file",
            source_id=file_id,
            source_name=filename,
            printer_id=printer_id,
            printer_name=printer_name,
            options=options,
            requested_by_user_id=requested_by_user_id,
            requested_by_username=requested_by_username,
        )

    async def cancel_job(self, job_id: int) -> dict[str, Any]:
        """Cancel a queued dispatch job.

        Queued jobs are removed immediately. Active jobs are cancelled
        cooperatively and will stop at the next cancellation checkpoint.
        """
        async with self._lock:
            # Check active jobs first
            active_state = self._active_jobs.get(job_id)
            if active_state is not None:
                logger.info("Cancel requested for active dispatch job %s", job_id)
                self._cancel_requested_job_ids.add(job_id)
                active_job = active_state.job
                payload = self._build_state_payload_unlocked(
                    recent_event={
                        "status": "cancelling",
                        "job_id": active_job.id,
                        "source_name": active_job.source_name,
                        "printer_id": active_job.printer_id,
                        "printer_name": active_job.printer_name,
                        "message": "Cancelling current dispatch...",
                    }
                )
                result = {
                    "cancelled": True,
                    "pending": True,
                    "job_id": active_job.id,
                    "source_name": active_job.source_name,
                    "printer_id": active_job.printer_id,
                    "printer_name": active_job.printer_name,
                }
                await ws_manager.broadcast({"type": "background_dispatch", "data": payload})
                return result

            # Check queued jobs
            cancelled_job: PrintDispatchJob | None = None
            for job in self._queued_jobs:
                if job.id == job_id:
                    cancelled_job = job
                    break

            if not cancelled_job:
                logger.info("Cancel requested for unknown dispatch job %s", job_id)
                return {"cancelled": False, "reason": "not_found"}

            self._queued_jobs.remove(cancelled_job)
            logger.info("Cancelled queued dispatch job %s", cancelled_job.id)
            self._batch_total = max(0, self._batch_total - 1)

            if self._batch_total == 0 and len(self._queued_jobs) == 0 and len(self._active_jobs) == 0:
                self._batch_completed = 0
                self._batch_failed = 0

            payload = self._build_state_payload_unlocked(
                recent_event={
                    "status": "cancelled",
                    "job_id": cancelled_job.id,
                    "source_name": cancelled_job.source_name,
                    "printer_id": cancelled_job.printer_id,
                    "printer_name": cancelled_job.printer_name,
                    "message": "Cancelled from queue",
                }
            )

        await ws_manager.broadcast({"type": "background_dispatch", "data": payload})
        return {
            "cancelled": True,
            "pending": False,
            "job_id": cancelled_job.id,
            "source_name": cancelled_job.source_name,
            "printer_id": cancelled_job.printer_id,
            "printer_name": cancelled_job.printer_name,
        }

    async def _dispatch(
        self,
        *,
        kind: Literal["reprint_archive", "print_library_file"],
        source_id: int,
        source_name: str,
        printer_id: int,
        printer_name: str,
        options: dict[str, Any],
        requested_by_user_id: int | None,
        requested_by_username: str | None,
    ) -> dict[str, Any]:
        async with self._lock:
            has_pending_for_printer = any(job.printer_id == printer_id for job in self._queued_jobs)
            has_active_for_printer = any(active.job.printer_id == printer_id for active in self._active_jobs.values())

            if has_pending_for_printer or has_active_for_printer:
                raise DispatchEnqueueRejected(f"Printer {printer_name} already has a background dispatch in progress")

            if self._printer_is_busy_printing(printer_id):
                raise DispatchEnqueueRejected(f"Printer {printer_name} is currently busy printing")

            dispatch_position = len(self._queued_jobs) + len(self._active_jobs) + 1
            job = PrintDispatchJob(
                id=self._next_job_id,
                kind=kind,
                source_id=source_id,
                source_name=source_name,
                printer_id=printer_id,
                printer_name=printer_name,
                options=options,
                requested_by_user_id=requested_by_user_id,
                requested_by_username=requested_by_username,
            )
            self._next_job_id += 1
            self._batch_total += 1
            self._queued_jobs.append(job)
            self._job_event.set()

            payload = self._build_state_payload_unlocked(
                recent_event={
                    "status": "dispatched",
                    "job_id": job.id,
                    "source_name": source_name,
                    "printer_id": printer_id,
                    "printer_name": printer_name,
                    "message": f"Dispatched to {printer_name}",
                }
            )

        await ws_manager.broadcast({"type": "background_dispatch", "data": payload})

        return {
            "dispatch_job_id": job.id,
            "dispatch_position": dispatch_position,
            "status": "dispatched",
            "printer_id": printer_id,
            "source_id": source_id,
            "source_name": source_name,
        }

    async def _dispatcher_loop(self):
        while True:
            await self._job_event.wait()
            self._job_event.clear()

            while True:
                payload: dict[str, Any] | None = None
                job_to_start: PrintDispatchJob | None = None
                async with self._lock:
                    busy_printer_ids = {state.job.printer_id for state in self._active_jobs.values()}
                    start_index = next(
                        (
                            idx
                            for idx, queued_job in enumerate(self._queued_jobs)
                            if queued_job.printer_id not in busy_printer_ids
                        ),
                        None,
                    )

                    if start_index is None:
                        break

                    job_to_start = self._queued_jobs[start_index]
                    del self._queued_jobs[start_index]
                    self._active_jobs[job_to_start.id] = ActiveDispatchState(
                        job=job_to_start,
                        message="Preparing background dispatch...",
                    )

                    task = asyncio.create_task(
                        self._run_active_job(job_to_start), name=f"background-dispatch-job-{job_to_start.id}"
                    )
                    self._running_tasks[job_to_start.id] = task

                    payload = self._build_state_payload_unlocked(
                        recent_event={
                            "status": "processing",
                            "job_id": job_to_start.id,
                            "source_name": job_to_start.source_name,
                            "printer_id": job_to_start.printer_id,
                            "printer_name": job_to_start.printer_name,
                            "message": "Preparing background dispatch...",
                        }
                    )

                if payload:
                    await ws_manager.broadcast({"type": "background_dispatch", "data": payload})

    async def _run_active_job(self, job: PrintDispatchJob):
        try:
            await self._process_job(job)
            await self._mark_job_finished(job, failed=False, message="Background dispatch complete")
        except DispatchJobCancelled:
            await self._mark_job_cancelled(job)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Background dispatch job %s failed: %s", job.id, e, exc_info=True)
            await self._mark_job_finished(job, failed=True, message=str(e))
        finally:
            self._job_event.set()

    async def _set_active_message(self, job: PrintDispatchJob, message: str):
        async with self._lock:
            active = self._active_jobs.get(job.id)
            if not active:
                return
            active.message = message
            payload = self._build_state_payload_unlocked(
                recent_event={
                    "status": "processing",
                    "job_id": active.job.id,
                    "source_name": active.job.source_name,
                    "printer_id": active.job.printer_id,
                    "printer_name": active.job.printer_name,
                    "message": message,
                }
            )
        await ws_manager.broadcast({"type": "background_dispatch", "data": payload})

    async def _set_active_upload_progress(self, job: PrintDispatchJob, uploaded: int, total: int):
        async with self._lock:
            active = self._active_jobs.get(job.id)
            if not active:
                return

            active.upload_bytes = max(0, int(uploaded))
            active.upload_total_bytes = max(0, int(total))
            payload = self._build_state_payload_unlocked(
                recent_event={
                    "status": "processing",
                    "job_id": active.job.id,
                    "source_name": active.job.source_name,
                    "printer_id": active.job.printer_id,
                    "printer_name": active.job.printer_name,
                    "message": active.message,
                }
            )
        await ws_manager.broadcast({"type": "background_dispatch", "data": payload})

    async def _mark_job_finished(self, job: PrintDispatchJob, *, failed: bool, message: str):
        async with self._lock:
            if failed:
                self._batch_failed += 1
            else:
                self._batch_completed += 1

            self._active_jobs.pop(job.id, None)
            self._running_tasks.pop(job.id, None)
            self._cancel_requested_job_ids.discard(job.id)

            payload = self._build_state_payload_unlocked(
                recent_event={
                    "status": "failed" if failed else "completed",
                    "job_id": job.id,
                    "source_name": job.source_name,
                    "printer_id": job.printer_id,
                    "printer_name": job.printer_name,
                    "message": message,
                }
            )
            should_reset_batch = len(self._queued_jobs) == 0 and len(self._active_jobs) == 0

        await ws_manager.broadcast({"type": "background_dispatch", "data": payload})

        if should_reset_batch:
            async with self._lock:
                if len(self._queued_jobs) == 0 and len(self._active_jobs) == 0:
                    self._batch_total = 0
                    self._batch_completed = 0
                    self._batch_failed = 0

    async def _mark_job_cancelled(self, job: PrintDispatchJob):
        async with self._lock:
            self._active_jobs.pop(job.id, None)
            self._running_tasks.pop(job.id, None)
            self._cancel_requested_job_ids.discard(job.id)
            self._batch_total = max(0, self._batch_total - 1)

            if self._batch_total == 0 and len(self._queued_jobs) == 0 and len(self._active_jobs) == 0:
                self._batch_completed = 0
                self._batch_failed = 0

            payload = self._build_state_payload_unlocked(
                recent_event={
                    "status": "cancelled",
                    "job_id": job.id,
                    "source_name": job.source_name,
                    "printer_id": job.printer_id,
                    "printer_name": job.printer_name,
                    "message": "Cancelled during dispatch",
                }
            )

        await ws_manager.broadcast({"type": "background_dispatch", "data": payload})

    def _is_cancel_requested(self, job_id: int) -> bool:
        return job_id in self._cancel_requested_job_ids

    def _raise_if_cancel_requested(self, job: PrintDispatchJob):
        if self._is_cancel_requested(job.id):
            raise DispatchJobCancelled(f"Dispatch job {job.id} cancelled")

    def _build_state_payload_unlocked(self, recent_event: dict[str, Any] | None = None) -> dict[str, Any]:
        processing = len(self._active_jobs)
        dispatched = len(self._queued_jobs)

        dispatched_jobs = [
            {
                "job_id": job.id,
                "kind": job.kind,
                "source_id": job.source_id,
                "source_name": job.source_name,
                "printer_id": job.printer_id,
                "printer_name": job.printer_name,
            }
            for job in list(self._queued_jobs)
        ]

        active_jobs: list[dict[str, Any]] = []
        for active in self._active_jobs.values():
            upload_progress_pct = None
            if active.upload_total_bytes and active.upload_total_bytes > 0 and active.upload_bytes is not None:
                upload_progress_pct = round(
                    max(0.0, min(100.0, (active.upload_bytes / active.upload_total_bytes) * 100.0)), 1
                )

            active_jobs.append(
                {
                    "job_id": active.job.id,
                    "kind": active.job.kind,
                    "source_id": active.job.source_id,
                    "source_name": active.job.source_name,
                    "printer_id": active.job.printer_id,
                    "printer_name": active.job.printer_name,
                    "message": active.message,
                    "upload_bytes": active.upload_bytes,
                    "upload_total_bytes": active.upload_total_bytes,
                    "upload_progress_pct": upload_progress_pct,
                }
            )

        active_jobs.sort(key=lambda item: int(item["job_id"]))
        active_job = active_jobs[0] if active_jobs else None

        return {
            "total": self._batch_total,
            "dispatched": dispatched,
            "processing": processing,
            "completed": self._batch_completed,
            "failed": self._batch_failed,
            "dispatched_jobs": dispatched_jobs,
            "active_jobs": active_jobs,
            "active_job": active_job,
            "recent_event": recent_event,
        }

    async def _process_job(self, job: PrintDispatchJob):
        if job.kind == "reprint_archive":
            await self._run_reprint_archive(job)
            return
        if job.kind == "print_library_file":
            await self._run_print_library_file(job)
            return
        raise RuntimeError(f"Unknown dispatch job kind: {job.kind}")

    async def _run_reprint_archive(self, job: PrintDispatchJob):
        from backend.app.main import register_expected_print

        async with async_session() as db:
            service = ArchiveService(db)
            archive = await service.get_archive(job.source_id)
            if not archive:
                raise RuntimeError("Archive not found")

            printer = await db.scalar(select(Printer).where(Printer.id == job.printer_id))
            if not printer:
                raise RuntimeError("Printer not found")

            printer_name = printer.name
            printer_ip = printer.ip_address
            printer_access_code = printer.access_code
            printer_model = printer.model
            archive_filename = archive.filename

            if not printer_manager.is_connected(job.printer_id):
                raise RuntimeError("Printer is not connected")

            file_path = settings.base_dir / archive.file_path
            if not file_path.exists():
                raise RuntimeError("Archive file not found")

            base_name = archive.filename
            if base_name.endswith(".gcode.3mf"):
                base_name = base_name[:-10]
            elif base_name.endswith(".3mf"):
                base_name = base_name[:-4]
            remote_filename = f"{base_name}.3mf"
            remote_path = f"/{remote_filename}"

            ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()
            self._raise_if_cancel_requested(job)

            await self._set_active_message(job, f"Preparing upload to {printer_name}...")
            await delete_file_async(
                printer_ip,
                printer_access_code,
                remote_path,
                socket_timeout=ftp_timeout,
                printer_model=printer_model,
            )

            self._raise_if_cancel_requested(job)

            try:
                await self._set_active_message(job, f"Uploading {archive_filename} to {printer_name}...")
                loop = asyncio.get_running_loop()
                progress_state = {"last_emit": 0.0, "last_bytes": 0}

                def upload_progress_callback(uploaded: int, total: int):
                    if self._is_cancel_requested(job.id):
                        raise DispatchJobCancelled(f"Dispatch job {job.id} cancelled during upload")

                    now = time.monotonic()
                    should_emit = (
                        uploaded >= total
                        or now - progress_state["last_emit"] >= 0.2
                        or uploaded - progress_state["last_bytes"] >= 256 * 1024
                    )

                    if should_emit:
                        progress_state["last_emit"] = now
                        progress_state["last_bytes"] = uploaded
                        loop.call_soon_threadsafe(
                            lambda u=uploaded, t=total: asyncio.create_task(self._set_active_upload_progress(job, u, t))
                        )

                if ftp_retry_enabled:
                    uploaded = await with_ftp_retry(
                        upload_file_async,
                        printer_ip,
                        printer_access_code,
                        file_path,
                        remote_path,
                        progress_callback=upload_progress_callback,
                        socket_timeout=ftp_timeout,
                        printer_model=printer_model,
                        max_retries=ftp_retry_count,
                        retry_delay=ftp_retry_delay,
                        operation_name=f"Upload for reprint to {printer_name}",
                        non_retry_exceptions=(DispatchJobCancelled,),
                    )
                else:
                    uploaded = await upload_file_async(
                        printer_ip,
                        printer_access_code,
                        file_path,
                        remote_path,
                        progress_callback=upload_progress_callback,
                        socket_timeout=ftp_timeout,
                        printer_model=printer_model,
                    )

                if uploaded:
                    await self._set_active_upload_progress(job, 1, 1)

                if not uploaded:
                    raise RuntimeError(
                        "Failed to upload file to printer. Check if SD card is inserted and properly formatted (FAT32/exFAT)."
                    )

                register_expected_print(
                    job.printer_id,
                    remote_filename,
                    job.source_id,
                    ams_mapping=job.options.get("ams_mapping"),
                )

                plate_id = self._resolve_plate_id(file_path, job.options.get("plate_id"))

                self._raise_if_cancel_requested(job)

                await self._set_active_message(job, f"Starting print on {printer_name}...")
                started = printer_manager.start_print(
                    job.printer_id,
                    remote_filename,
                    plate_id,
                    ams_mapping=job.options.get("ams_mapping"),
                    timelapse=job.options.get("timelapse", False),
                    bed_levelling=job.options.get("bed_levelling", True),
                    flow_cali=job.options.get("flow_cali", False),
                    vibration_cali=job.options.get("vibration_cali", False),
                    layer_inspect=job.options.get("layer_inspect", False),
                    use_ams=job.options.get("use_ams", True),
                )

                if not started:
                    raise RuntimeError("Failed to start print")

                if job.requested_by_user_id and job.requested_by_username:
                    printer_manager.set_current_print_user(
                        job.printer_id,
                        job.requested_by_user_id,
                        job.requested_by_username,
                    )
            except DispatchJobCancelled:
                await self._set_active_message(job, f"Cancelled upload on {printer_name}.")
                raise

    async def _run_print_library_file(self, job: PrintDispatchJob):
        from backend.app.main import register_expected_print

        async with async_session() as db:
            lib_file = await db.scalar(select(LibraryFile).where(LibraryFile.id == job.source_id))
            if not lib_file:
                raise RuntimeError("File not found")

            if not self._is_sliced_file(lib_file.filename):
                raise RuntimeError("Not a sliced file. Only .gcode or .gcode.3mf files can be printed.")

            file_path = Path(settings.base_dir) / lib_file.file_path
            if not file_path.exists():
                raise RuntimeError("File not found on disk")

            printer = await db.scalar(select(Printer).where(Printer.id == job.printer_id))
            if not printer:
                raise RuntimeError("Printer not found")

            printer_name = printer.name
            printer_ip = printer.ip_address
            printer_access_code = printer.access_code
            printer_model = printer.model
            library_filename = lib_file.filename

            if not printer_manager.is_connected(job.printer_id):
                raise RuntimeError("Printer is not connected")

            await self._set_active_message(job, f"Creating archive for {lib_file.filename}...")
            archive_service = ArchiveService(db)
            archive = await archive_service.archive_print(
                printer_id=job.printer_id,
                source_file=file_path,
            )
            if not archive:
                raise RuntimeError("Failed to create archive")

            await db.flush()

            base_name = lib_file.filename
            if base_name.endswith(".gcode.3mf"):
                base_name = base_name[:-10]
            elif base_name.endswith(".3mf"):
                base_name = base_name[:-4]
            remote_filename = f"{base_name}.3mf"
            remote_path = f"/{remote_filename}"

            ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()
            self._raise_if_cancel_requested(job)

            await self._set_active_message(job, f"Preparing upload to {printer_name}...")
            await delete_file_async(
                printer_ip,
                printer_access_code,
                remote_path,
                socket_timeout=ftp_timeout,
                printer_model=printer_model,
            )

            self._raise_if_cancel_requested(job)

            try:
                await self._set_active_message(job, f"Uploading {library_filename} to {printer_name}...")
                loop = asyncio.get_running_loop()
                progress_state = {"last_emit": 0.0, "last_bytes": 0}

                def upload_progress_callback(uploaded: int, total: int):
                    if self._is_cancel_requested(job.id):
                        raise DispatchJobCancelled(f"Dispatch job {job.id} cancelled during upload")

                    now = time.monotonic()
                    should_emit = (
                        uploaded >= total
                        or now - progress_state["last_emit"] >= 0.2
                        or uploaded - progress_state["last_bytes"] >= 256 * 1024
                    )

                    if should_emit:
                        progress_state["last_emit"] = now
                        progress_state["last_bytes"] = uploaded
                        loop.call_soon_threadsafe(
                            lambda u=uploaded, t=total: asyncio.create_task(self._set_active_upload_progress(job, u, t))
                        )

                if ftp_retry_enabled:
                    uploaded = await with_ftp_retry(
                        upload_file_async,
                        printer_ip,
                        printer_access_code,
                        file_path,
                        remote_path,
                        progress_callback=upload_progress_callback,
                        socket_timeout=ftp_timeout,
                        printer_model=printer_model,
                        max_retries=ftp_retry_count,
                        retry_delay=ftp_retry_delay,
                        operation_name=f"Upload for print to {printer_name}",
                        non_retry_exceptions=(DispatchJobCancelled,),
                    )
                else:
                    uploaded = await upload_file_async(
                        printer_ip,
                        printer_access_code,
                        file_path,
                        remote_path,
                        progress_callback=upload_progress_callback,
                        socket_timeout=ftp_timeout,
                        printer_model=printer_model,
                    )

                if uploaded:
                    await self._set_active_upload_progress(job, 1, 1)

                if not uploaded:
                    await db.rollback()
                    raise RuntimeError(
                        "Failed to upload file to printer. Check if SD card is inserted and properly formatted (FAT32/exFAT)."
                    )

                register_expected_print(
                    job.printer_id,
                    remote_filename,
                    archive.id,
                    ams_mapping=job.options.get("ams_mapping"),
                )

                plate_id = self._resolve_plate_id(file_path, job.options.get("plate_id"))

                self._raise_if_cancel_requested(job)

                await self._set_active_message(job, f"Starting print on {printer_name}...")
                started = printer_manager.start_print(
                    job.printer_id,
                    remote_filename,
                    plate_id,
                    ams_mapping=job.options.get("ams_mapping"),
                    timelapse=job.options.get("timelapse", False),
                    bed_levelling=job.options.get("bed_levelling", True),
                    flow_cali=job.options.get("flow_cali", False),
                    vibration_cali=job.options.get("vibration_cali", False),
                    layer_inspect=job.options.get("layer_inspect", False),
                    use_ams=job.options.get("use_ams", True),
                )

                if not started:
                    await db.rollback()
                    raise RuntimeError("Failed to start print")

                await db.commit()
            except DispatchJobCancelled:
                await db.rollback()
                await self._set_active_message(job, f"Cancelled upload on {printer_name}.")
                raise

    @staticmethod
    def _resolve_plate_id(file_path: Path, requested_plate_id: int | None) -> int:
        if requested_plate_id is not None:
            return requested_plate_id

        plate_id = 1
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                for name in zf.namelist():
                    if name.startswith("Metadata/plate_") and name.endswith(".gcode"):
                        plate_str = name[15:-6]
                        plate_id = int(plate_str)
                        break
        except (ValueError, zipfile.BadZipFile, OSError):
            pass
        return plate_id

    @staticmethod
    def _is_sliced_file(filename: str) -> bool:
        lower = filename.lower()
        return lower.endswith(".gcode") or lower.endswith(".gcode.3mf")


background_dispatch = BackgroundDispatchService()
