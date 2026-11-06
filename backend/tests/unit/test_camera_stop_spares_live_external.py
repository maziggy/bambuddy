"""/camera/stop and the janitor on external camera streams.

External cameras used to run one stream per viewer, and the stop route told
them apart by a viewer id. They now share one upstream through the fan-out
(RC-C6, see test_camera_external_fanout.py), so the subscriber guard decides
whether a stop may end it, as for the printer's own camera. What is left to
pin here: a stop that gets past the guard ends the printer's external streams,
a deleted printer loses all of them, and the janitor still finds a leaked or
stuck one (#2675).
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from backend.app.api.routes import camera

PRINTER = 7


class _Proc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        self.stdout = None
        self.stderr = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


@pytest.fixture
def no_fanout(monkeypatch):
    monkeypatch.setattr(camera, "_FFMPEG_KILL_TIMEOUT", 0.05)
    monkeypatch.setattr(camera, "get_subscriber_count", lambda _key: 0)

    async def no_broadcaster(_key):
        return False

    monkeypatch.setattr(camera, "shutdown_broadcaster", no_broadcaster)


@pytest.fixture
def register():
    added: list[tuple[str, _Proc]] = []

    def _register(suffix: str, pid: int, last_frame_age: float | None = 0.5):
        sid = f"{PRINTER}-ext-{suffix}"
        proc = _Proc(pid)
        event = asyncio.Event()
        camera._active_streams[sid] = proc
        camera._disconnect_events[sid] = event
        camera._spawned_ffmpeg_pids[pid] = time.time()
        if last_frame_age is not None:
            camera._stream_last_frame_times[sid] = time.time() - last_frame_age
        added.append((sid, proc))
        return proc, event

    yield _register
    for sid, proc in added:
        camera._active_streams.pop(sid, None)
        camera._disconnect_events.pop(sid, None)
        camera._stream_last_frame_times.pop(sid, None)
        camera._spawned_ffmpeg_pids.pop(proc.pid, None)


@pytest.mark.asyncio
async def test_a_stop_with_nobody_watching_ends_every_external_stream(no_fanout, register):
    """Also one whose cleanup was skipped and still looks live (#2675)."""
    a, a_event = register("all00004", 54007)
    b, _ = register("all00005", 54008, last_frame_age=60.0)

    result = await camera.stop_camera_stream(PRINTER, _=None, viewer="tile-a")

    assert result["stopped"] == 2
    assert a.returncode is not None and b.returncode is not None and a_event.is_set()
    assert f"{PRINTER}-ext-all00004" not in camera._active_streams


@pytest.mark.asyncio
async def test_a_stop_while_someone_watches_ends_nothing(no_fanout, register, monkeypatch):
    live, live_event = register("live0005", 54009)
    monkeypatch.setattr(camera, "get_subscriber_count", lambda _key: 1)

    result = await camera.stop_camera_stream(PRINTER, _=None, viewer="tile-a")

    assert result.get("skipped") is True
    assert live.returncode is None and not live_event.is_set()


@pytest.mark.asyncio
async def test_a_deleted_printer_loses_every_stream(no_fanout, register):
    live, live_event = register("live0006", 54010)

    await camera.forget_printer(PRINTER)

    assert live.returncode is not None and live_event.is_set()
    assert f"{PRINTER}-ext-live0006" not in camera._active_streams


@pytest.mark.asyncio
async def test_the_janitor_kills_a_stuck_external_stream(no_fanout, register, monkeypatch):
    """Orphaned by a skipped cleanup, with nobody left to stop it (#2675)."""
    monkeypatch.setattr(camera, "_scan_bambu_ffmpeg_pids", lambda: [])
    # A real pid, so the janitor's existence check keeps it.
    stale, stale_event = register("stale007", os.getpid(), last_frame_age=60.0)
    camera._spawned_ffmpeg_pids[stale.pid] = time.time() - 120

    await camera.cleanup_orphaned_streams()

    assert stale.returncode is not None and stale_event.is_set()
    assert f"{PRINTER}-ext-stale007" not in camera._active_streams


@pytest.mark.asyncio
async def test_the_janitor_keeps_a_stream_between_reconnects(no_fanout, register, monkeypatch):
    """Its ffmpeg died and the upstream is starting the next one under the same
    id: a stop must still reach it."""
    monkeypatch.setattr(camera, "_scan_bambu_ffmpeg_pids", lambda: [])
    dead, _ = register("dead0008", 54011)
    dead.returncode = 1

    await camera.cleanup_orphaned_streams()

    assert f"{PRINTER}-ext-dead0008" in camera._disconnect_events
