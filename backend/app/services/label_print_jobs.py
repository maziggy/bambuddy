"""Label print jobs (#3612): a print outlives the HTTP request that asked for it.

A print route validates the request, then ``submit``s the work here and returns
a job id at once. Each printer (host, port) has one FIFO lane, a queue with a
worker task started on demand, so jobs for one printer run strictly one at a
time and jobs for different printers run in parallel. Progress is kept in
memory (``PrintJobStore``) for the status route to read; nothing is persisted,
a restart forgets the jobs.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

JOB_TTL_S = 15 * 60  # how long a finished job stays readable
MAX_JOBS = 200  # jobs kept in all
MAX_QUEUED_PER_PRINTER = 20  # jobs waiting behind the running one, per printer
QUEUE_FULL_MESSAGE = "The label printer has too many jobs waiting; try again later"
FAILED_GENERIC = "Label printing failed"
CANCELLED = "Printing was cancelled"

JobState = Literal["queued", "printing", "done", "failed"]
# The printer a lane serves: (host, port).
LaneKey = tuple[str, int]
Work = Callable[["PrintJob"], Awaitable[None]]
Explain = Callable[[BaseException], str]


class PrintQueueFull(Exception):
    """No room for another job (too many waiting for one printer, or too many unfinished in all)."""

    def __init__(self) -> None:
        super().__init__(QUEUE_FULL_MESSAGE)


@dataclass
class PrintJob:
    total: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: JobState = "queued"
    printed: int = 0
    error: str | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.id,
            "state": self.state,
            "printed": self.printed,
            "total": self.total,
            "error": self.error,
        }


class PrintJobStore:
    """Jobs by id. A finished job is dropped ``ttl_s`` after it finished; at
    ``max_jobs`` the oldest finished job goes first. A job that is not finished
    is never dropped: when only unfinished ones fill the store, ``add`` raises
    ``PrintQueueFull``."""

    def __init__(
        self, *, ttl_s: float = JOB_TTL_S, max_jobs: int = MAX_JOBS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.ttl_s = ttl_s
        self.max_jobs = max_jobs
        self.clock = clock
        self._jobs: dict[str, PrintJob] = {}  # insertion order = age

    def __len__(self) -> int:
        self._expire()
        return len(self._jobs)

    def _expire(self) -> None:
        now = self.clock()
        for job_id in [
            j.id for j in self._jobs.values() if j.finished_at is not None and now - j.finished_at > self.ttl_s
        ]:
            del self._jobs[job_id]

    def add(self, total: int) -> PrintJob:
        self._expire()
        while len(self._jobs) >= self.max_jobs:
            victim = next((j for j in self._jobs.values() if j.finished_at is not None), None)
            if victim is None:
                raise PrintQueueFull
            del self._jobs[victim.id]
        job = PrintJob(total=total)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> PrintJob | None:
        self._expire()
        return self._jobs.get(job_id)

    def finish(self, job: PrintJob, *, error: str | None = None) -> None:
        if job.finished_at is not None:
            return
        job.finished_at = self.clock()
        if error is None:
            job.state, job.printed = "done", job.total
        else:
            job.state, job.error = "failed", error


class _Lane:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[tuple[PrintJob, Work, Explain]] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None
        self.current: PrintJob | None = None


class LabelPrintService:
    def __init__(self, store: PrintJobStore | None = None) -> None:
        self.store = store or PrintJobStore()
        self._lanes: dict[LaneKey, _Lane] = {}
        self._closing = False

    def submit(self, key: LaneKey, total: int, work: Work, explain: Explain) -> PrintJob:
        """Queue ``work`` (awaited with its job) behind the printer's earlier jobs.
        ``explain`` turns an exception it raises into the text shown to the user.
        Raises ``PrintQueueFull`` when the printer already has
        ``MAX_QUEUED_PER_PRINTER`` jobs waiting, or the store is full of unfinished
        jobs. Must be called inside the running event loop."""
        loop = asyncio.get_running_loop()
        self._closing = False
        lane = self._lanes.get(key)
        if lane is not None and lane.loop is not loop:
            # A lane of another (closed) event loop: its queue and task are unusable.
            self._abort_lane(lane)
            lane = None
        if lane is None:
            lane = self._lanes[key] = _Lane(loop)
        try:
            if lane.queue.qsize() >= MAX_QUEUED_PER_PRINTER:
                raise PrintQueueFull
            job = self.store.add(total)
        except PrintQueueFull:
            if lane.queue.empty() and lane.task is None:
                self._lanes.pop(key, None)  # a lane made just now stays out of the way
            raise
        lane.queue.put_nowait((job, work, explain))
        self._ensure_worker(key, lane)
        return job

    def _ensure_worker(self, key: LaneKey, lane: _Lane) -> None:
        if lane.task is None or lane.task.done():
            lane.task = lane.loop.create_task(self._worker(key, lane), name=f"label-print-{key[0]}:{key[1]}")
            lane.task.add_done_callback(lambda task: self._worker_ended(key, lane, task))

    async def _worker(self, key: LaneKey, lane: _Lane) -> None:
        # Ends when the queue is empty (the lane is dropped then); the next submit starts a new one.
        while not lane.queue.empty():
            job, work, explain = lane.queue.get_nowait()
            lane.current = job
            job.state = "printing"
            try:
                await work(job)
            except asyncio.CancelledError:
                self.store.finish(job, error=CANCELLED)
                raise
            except Exception as exc:
                # Whatever it was, the job fails and the worker goes on with the next one.
                try:
                    message = explain(exc)
                except Exception:
                    message = FAILED_GENERIC
                logger.warning("Label print job on %s:%s failed: %s", key[0], key[1], exc, exc_info=_unexpected(exc))
                self.store.finish(job, error=message)
            except BaseException:
                # Not for the loop to handle below Exception: the job fails, the worker dies
                # and ``_worker_ended`` restarts a worker for the rest of the queue.
                self.store.finish(job, error=FAILED_GENERIC)
                raise
            else:
                self.store.finish(job)
            finally:
                lane.current = None

    def _worker_ended(self, key: LaneKey, lane: _Lane, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # Not an Exception the loop handles (it never gets here for those): the
            # worker itself broke. Fail what it was on, and carry on with the rest.
            logger.error("Label print worker for %s:%s died", key[0], key[1], exc_info=exc)
            if lane.current is not None:
                self.store.finish(lane.current, error=FAILED_GENERIC)
                lane.current = None
        if not lane.queue.empty() and not self._closing and self._lanes.get(key) is lane:
            self._ensure_worker(key, lane)
        elif lane.queue.empty() and self._lanes.get(key) is lane:
            del self._lanes[key]

    def _abort_lane(self, lane: _Lane, error: str = CANCELLED) -> None:
        while not lane.queue.empty():
            job, _, _ = lane.queue.get_nowait()
            self.store.finish(job, error=error)
        if lane.current is not None:
            self.store.finish(lane.current, error=error)

    async def shutdown(self) -> None:
        """Cancel the workers (app shutdown): the job being printed and every queued
        job become failed."""
        self._closing = True
        lanes, self._lanes = list(self._lanes.values()), {}
        tasks = []
        for lane in lanes:
            self._abort_lane(lane)
            if lane.task is not None and not lane.task.done():
                lane.task.cancel()
                tasks.append(lane.task)
        current = asyncio.get_running_loop()
        await asyncio.gather(*(t for t in tasks if t.get_loop() is current), return_exceptions=True)


def _unexpected(exc: BaseException) -> bool:
    """Log a traceback only for errors that are not the printer's own."""
    from backend.app.services.label_printers.base import LabelPrinterError

    return not isinstance(exc, LabelPrinterError)


label_print_service = LabelPrintService()
