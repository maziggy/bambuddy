"""Tests for SliceDispatchService.set_progress.

The dispatcher exposes set_progress so the slice-route's parallel poller
(spawned alongside the blocking sidecar slice request) can publish
``{stage, total_percent, plate_index, plate_count}`` snapshots that the
status-poll endpoint surfaces to the UI's persistent progress toast.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.services.slice_dispatch import SliceDispatchService


@pytest.mark.asyncio
async def test_set_progress_attaches_snapshot_to_running_job():
    dispatcher = SliceDispatchService()

    started = asyncio.Event()
    release = asyncio.Event()

    async def runner(job_id: int) -> dict:
        started.set()
        # Hold the job in the running state until the test releases it.
        await release.wait()
        return {"library_file_id": 1}

    job = await dispatcher.enqueue(
        kind="library_file",
        source_id=1,
        source_name="x.stl",
        run=runner,
    )
    await started.wait()

    # Without progress published yet, the job's progress is None.
    assert dispatcher.get(job.id) is not None
    assert dispatcher.get(job.id).progress is None

    # First snapshot lands on the job.
    dispatcher.set_progress(
        job.id,
        {"stage": "Detecting perimeters", "total_percent": 12},
    )
    snap = dispatcher.get(job.id).progress
    assert snap == {"stage": "Detecting perimeters", "total_percent": 12}

    # Second snapshot replaces, doesn't merge — the dispatcher just
    # holds the latest frame; the sidecar's pipe protocol always emits
    # the full set, so partial-frame merging would be wrong.
    dispatcher.set_progress(
        job.id,
        {"stage": "Generating G-code", "total_percent": 75, "plate_index": 1},
    )
    snap = dispatcher.get(job.id).progress
    assert snap == {
        "stage": "Generating G-code",
        "total_percent": 75,
        "plate_index": 1,
    }

    # Release the runner so the job completes and the test cleans up.
    release.set()
    # Yield to the event loop so the runner's completion settles.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_set_progress_silently_ignores_unknown_job_id():
    """A late poll after retention sweep mustn't crash the polling task."""
    dispatcher = SliceDispatchService()
    # Should be a no-op, not an exception.
    dispatcher.set_progress(99999, {"stage": "x", "total_percent": 50})


@pytest.mark.asyncio
async def test_set_progress_can_clear_to_none():
    """Allow clearing — useful when the slice transitions to a final
    state and we want the toast to revert to the elapsed-time fallback
    on subsequent polls."""
    dispatcher = SliceDispatchService()
    started = asyncio.Event()
    release = asyncio.Event()

    async def runner(job_id: int) -> dict:
        started.set()
        await release.wait()
        return {"library_file_id": 1}

    job = await dispatcher.enqueue(
        kind="library_file",
        source_id=1,
        source_name="x.stl",
        run=runner,
    )
    await started.wait()

    dispatcher.set_progress(job.id, {"stage": "x", "total_percent": 50})
    assert dispatcher.get(job.id).progress is not None
    dispatcher.set_progress(job.id, None)
    assert dispatcher.get(job.id).progress is None

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
