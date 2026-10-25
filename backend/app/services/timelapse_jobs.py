"""Timelapse editor re-encodes, run as background jobs.

Re-encoding a long timelapse takes minutes. Run inside the request, a browser
or proxy that gave up left ffmpeg running with nobody waiting for the result.
A job runs on its own instead: the editor polls its progress, and cancelling
it stops ffmpeg. One job per archive at a time; the last one is kept so a
reopened editor can show how it ended.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"


@dataclass
class TimelapseJob:
    archive_id: int
    status: str = RUNNING
    # 0.0 to 1.0; None until ffmpeg reports its first position.
    progress: float | None = None
    output_path: str | None = None
    message: str | None = None
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def set_progress(self, progress: float) -> None:
        self.progress = min(max(progress, 0.0), 1.0)


class JobAlreadyRunning(Exception):
    pass


# Runs the re-encode; returns (output path relative to the data dir, message).
JobRunner = Callable[[TimelapseJob], Awaitable[tuple[str, str]]]

_jobs: dict[int, TimelapseJob] = {}


def get_job(archive_id: int) -> TimelapseJob | None:
    return _jobs.get(archive_id)


def start_job(archive_id: int, run: JobRunner) -> TimelapseJob:
    current = _jobs.get(archive_id)
    if current is not None and current.status == RUNNING:
        raise JobAlreadyRunning
    job = TimelapseJob(archive_id=archive_id)
    _jobs[archive_id] = job
    job.task = asyncio.create_task(_run(job, run))
    return job


async def _run(job: TimelapseJob, run: JobRunner) -> None:
    try:
        job.output_path, job.message = await run(job)
        job.progress = 1.0
        job.status = COMPLETED
    except asyncio.CancelledError:
        job.status = CANCELLED
    except Exception as e:
        logger.error("Timelapse processing failed for archive %s: %s", job.archive_id, e)
        job.error = str(e)
        job.status = FAILED


async def cancel_job(archive_id: int) -> bool:
    """Cancel the archive's running job; False when none is running."""
    job = _jobs.get(archive_id)
    if job is None or job.status != RUNNING or job.task is None:
        return False
    job.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await job.task
    return True


async def cancel_all() -> None:
    for archive_id in list(_jobs):
        await cancel_job(archive_id)
