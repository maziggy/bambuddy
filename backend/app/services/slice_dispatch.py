"""In-memory background dispatcher for slice jobs.

Mirrors the shape of `background_dispatch.py` (the print-upload dispatcher)
but tailored for slicing: jobs are independent (no printer-busy gating),
short-lived (typically 5-60s), and the result is a `LibraryFile` or
`PrintArchive` row rather than a printer-side dispatch.

The frontend kicks off a slice via `POST /library/files/{id}/slice` or
`POST /archives/{id}/slice`, gets back `{job_id, status_url}`, then polls
`GET /slice-jobs/{id}` until status is `completed` or `failed`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)


SliceJobStatus = Literal["pending", "running", "completed", "failed"]


@dataclass(slots=True)
class SliceJob:
    id: int
    kind: Literal["library_file", "archive"]
    source_id: int
    source_name: str
    status: SliceJobStatus = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # On success: the body returned to the caller — usually a SliceResponse
    # or SliceArchiveResponse dict.
    result: dict[str, Any] | None = None
    # On failure: HTTP status + error message.
    error_status: int | None = None
    error_detail: str | None = None
    # Live progress fed by the sidecar's --pipe channel while the slicer
    # is running. Populated by a polling task spawned alongside the
    # blocking POST /slice request; None when the sidecar doesn't
    # support progress (older sidecars, no request_id, etc.). Surfaced
    # in the SliceJobState response so the persistent toast can render
    # "Generating G-code (75%)" instead of just elapsed time.
    progress: dict[str, Any] | None = None


# Retention: keep finished jobs around for 30 minutes so the polling client
# always sees a terminal state on its next tick. After that, the next access
# sweep prunes them.
_RETENTION_SECONDS = 30 * 60


class SliceDispatchService:
    def __init__(self) -> None:
        self._jobs: dict[int, SliceJob] = {}
        self._next_id: int = 1
        self._lock = asyncio.Lock()
        self._tasks: dict[int, asyncio.Task] = {}

    async def enqueue(
        self,
        *,
        kind: Literal["library_file", "archive"],
        source_id: int,
        source_name: str,
        run: Callable[[int], Awaitable[dict[str, Any]]],
    ) -> SliceJob:
        """Register a new slice job and start it on the event loop.

        ``run`` is an async callable that takes the freshly-created
        ``job_id`` (so it can wire up live-progress reporting via
        :meth:`set_progress`) and returns the response body the caller
        will receive once status flips to ``completed``.
        """
        async with self._lock:
            job = SliceJob(
                id=self._next_id,
                kind=kind,
                source_id=source_id,
                source_name=source_name,
            )
            self._next_id += 1
            self._jobs[job.id] = job
            self._sweep_locked()

        task = asyncio.create_task(self._run_job(job, run), name=f"slice-job-{job.id}")
        self._tasks[job.id] = task
        return job

    async def _run_job(
        self,
        job: SliceJob,
        run: Callable[[int], Awaitable[dict[str, Any]]],
    ) -> None:
        job.started_at = datetime.now(timezone.utc)
        job.status = "running"
        try:
            result = await run(job.id)
            job.result = result
            job.status = "completed"
        except _SliceJobError as exc:
            # Caller-controlled HTTP error — propagate status + detail.
            job.status = "failed"
            job.error_status = exc.status_code
            job.error_detail = exc.detail
        except Exception as exc:
            logger.exception("Slice job %s failed unexpectedly", job.id)
            job.status = "failed"
            job.error_status = 500
            job.error_detail = f"Unexpected error: {exc}"
        finally:
            job.completed_at = datetime.now(timezone.utc)
            self._tasks.pop(job.id, None)

    def get(self, job_id: int) -> SliceJob | None:
        return self._jobs.get(job_id)

    def set_progress(self, job_id: int, progress: dict[str, Any] | None) -> None:
        """Update the live-progress snapshot for a running job.

        Called by the slice route's progress poller every ~1s while the
        sidecar slice request is in flight. Silently ignores unknown ids
        (the job may have just finished and been retention-swept) so a
        late poll doesn't crash the polling task.
        """
        job = self._jobs.get(job_id)
        if job is not None:
            job.progress = progress

    def _sweep_locked(self) -> None:
        """Drop finished jobs older than the retention window. Caller holds
        the lock."""
        now = datetime.now(timezone.utc)
        stale_ids = [
            jid
            for jid, job in self._jobs.items()
            if job.status in ("completed", "failed")
            and job.completed_at is not None
            and (now - job.completed_at).total_seconds() > _RETENTION_SECONDS
        ]
        for jid in stale_ids:
            self._jobs.pop(jid, None)


class _SliceJobError(Exception):
    """Raised inside a slice job's `run` callable to surface a specific
    HTTP status + detail. The dispatcher catches these and stores them on
    the job. Callers convert ``HTTPException`` to this on the boundary.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def http_exception_to_job_error(exc) -> _SliceJobError:
    """Convert a starlette ``HTTPException`` into the dispatcher's error
    type. Handles the common case where slice helpers raise FastAPI's
    ``HTTPException`` for validation / sidecar failures.
    """
    return _SliceJobError(exc.status_code, str(exc.detail))


# Module-level singleton, started/stopped by main.py's lifespan.
slice_dispatch = SliceDispatchService()
