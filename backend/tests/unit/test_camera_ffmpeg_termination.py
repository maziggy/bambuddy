"""ffmpeg teardown: draining the pipes, and the bounded waits behind it.

Originally #2580 (fix shape from PR #2581 by @ronaldheft): the cleanup paths
``await process.wait()``-ed unbounded after ``kill()``, which on a P2S RTSP read
timeout parked the fan-out stream coroutine for 12 hours, leaving every viewer
attached to a stalled broadcaster while snapshots (fresh connections) kept
working. Three places had it, all bounded now:

1. ``_terminate_ffmpeg`` — the stream generator's cleanup (the reported hang).
2. ``stop_camera`` — hung the very request a user makes to recover.
3. ``cleanup_orphaned_streams`` — hung the janitor that is the safety net.

That diagnosis — "a SIGKILLed ffmpeg stuck in uninterruptible I/O" — turned out
to be wrong, and the bound was capping a deadlock of our own making. ffmpeg was
blocked writing to a stdout pipe nobody was reading, which makes SIGTERM
unactionable, and ``wait()`` cannot observe an exit while a pipe transport is
still undrained. So the abandon path fired on every camera close, costing 4s of
the printer's single camera connection each time. The pipes are drained now; the
bounds remain as backstops, and the tests for them stay valid.

The draining tests below drive a REAL subprocess, because the failure is in
asyncio's pipe/transport bookkeeping — a fake process object cannot reproduce
it and would happily pass against the broken code.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from contextlib import suppress

import pytest

from backend.app.api.routes import camera

pytestmark = pytest.mark.asyncio

# Stands in for ffmpeg: floods stdout, and handles SIGTERM the way ffmpeg does
# — a handler that sets a flag which only the main loop checks, so a process
# blocked in write() never acts on it until something drains the pipe.
_FFMPEG_LIKE = """
import signal, sys
stop = False
def _handler(*_a):
    global stop
    stop = True
signal.signal(signal.SIGTERM, _handler)
sys.stderr.write("x" * 4096)
sys.stderr.flush()
while not stop:
    sys.stdout.buffer.write(b"x" * 65536)
    sys.stdout.buffer.flush()
"""

# Same, but SIGTERM is ignored outright — forces the SIGKILL branch.
_SIGTERM_PROOF = """
import signal, sys
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    sys.stdout.buffer.write(b"x" * 65536)
    sys.stdout.buffer.flush()
"""


async def _spawn(program: str) -> asyncio.subprocess.Process:
    """Start the stand-in and let it fill its stdout pipe, as the cancel path
    leaves a real ffmpeg."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        program,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.sleep(0.4)
    return process


class _FakeServer:
    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


class _TimeoutReader:
    """stdout that immediately reports a read timeout (stalled RTSP)."""

    async def read(self, _size: int = -1) -> bytes:
        raise TimeoutError


class _SingleFrameReader:
    """stdout that yields one complete JPEG then EOF."""

    def __init__(self) -> None:
        self._sent = False

    async def read(self, _size: int = -1) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return b"\xff\xd8fresh-frame\xff\xd9"


class _StuckPostKillProcess:
    """ffmpeg whose post-kill wait() never completes unless cancelled."""

    def __init__(self, pid: int = 41001) -> None:
        self.pid = pid
        self.returncode = None
        self.stdout = _TimeoutReader()
        self.stderr = None
        self.wait_calls = 0
        self.killed = False
        self.post_kill_wait_cancelled = asyncio.Event()
        self._release = asyncio.Event()

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1:
            # Graceful-terminate window: simulate "didn't exit in time".
            raise TimeoutError
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self.post_kill_wait_cancelled.set()
            raise
        self.returncode = -9
        return self.returncode


class _FrameProcess:
    """Healthy replacement ffmpeg delivering one frame."""

    def __init__(self, pid: int = 41002) -> None:
        self.pid = pid
        self.returncode = None
        self.stdout = _SingleFrameReader()
        self.stderr = None

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        self.returncode = 0
        return self.returncode


# ---------------------------------------------------------------------------
# 0. _terminate_ffmpeg drains the pipes — against a real subprocess
# ---------------------------------------------------------------------------


async def test_terminate_drains_stdout_so_sigterm_works(caplog):
    """A process blocked writing to a full pipe still shuts down on SIGTERM.

    Undrained, this took the full grace period plus the SIGKILL bound (4s
    measured) and ended in the abandon error. Drained, SIGTERM lands.
    """
    process = await _spawn(_FFMPEG_LIKE)
    camera._spawned_ffmpeg_pids[process.pid] = time.time()

    with caplog.at_level(logging.WARNING, logger=camera.logger.name):
        started = time.monotonic()
        await asyncio.wait_for(camera._terminate_ffmpeg(process, "test-drain"), timeout=5.0)
        elapsed = time.monotonic() - started

    assert process.returncode is not None, "wait() must observe the exit"
    # Comfortably under the 2.0s grace period: proves SIGTERM was acted on
    # rather than timing out into the kill branch.
    assert elapsed < 1.5, f"teardown took {elapsed:.2f}s — pipes likely not drained"
    assert "didn't terminate gracefully" not in caplog.text
    assert "abandoning wait" not in caplog.text
    assert process.pid not in camera._spawned_ffmpeg_pids


async def test_terminate_observes_kill_of_a_sigterm_proof_process(monkeypatch, caplog):
    """Even when SIGTERM is genuinely ignored, wait() must see the SIGKILL.

    This is the case the abandon error was invented for. With the pipes drained
    the exit is observable, so it must not fire.
    """
    monkeypatch.setattr(camera, "_FFMPEG_TERM_TIMEOUT", 0.3)
    process = await _spawn(_SIGTERM_PROOF)
    camera._spawned_ffmpeg_pids[process.pid] = time.time()

    with caplog.at_level(logging.WARNING, logger=camera.logger.name):
        await asyncio.wait_for(camera._terminate_ffmpeg(process, "test-kill"), timeout=5.0)

    assert process.returncode == -9, "SIGKILLed exit must be observed, not abandoned"
    assert "didn't terminate gracefully" in caplog.text  # SIGTERM really was ignored
    assert "abandoning wait" not in caplog.text
    assert process.pid not in camera._spawned_ffmpeg_pids


async def test_terminate_is_a_noop_for_an_already_dead_process():
    """The early return must still drop the pid from the tracking dict."""
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    await process.wait()
    camera._spawned_ffmpeg_pids[process.pid] = time.time()

    await asyncio.wait_for(camera._terminate_ffmpeg(process, "test-dead"), timeout=2.0)

    assert process.pid not in camera._spawned_ffmpeg_pids


# ---------------------------------------------------------------------------
# 1. _terminate_ffmpeg — the helper itself is bounded
# ---------------------------------------------------------------------------


async def test_terminate_ffmpeg_abandons_unreaped_kill(monkeypatch):
    monkeypatch.setattr(camera, "_FFMPEG_KILL_TIMEOUT", 0.05)
    proc = _StuckPostKillProcess()

    # Must return promptly instead of hanging on the post-kill wait.
    await asyncio.wait_for(camera._terminate_ffmpeg(proc, "test"), timeout=1.0)

    assert proc.killed is True
    assert proc.post_kill_wait_cancelled.is_set()
    assert proc.pid not in camera._spawned_ffmpeg_pids


# ---------------------------------------------------------------------------
# 2. Stream generator — reconnects instead of pinning the fan-out pump
#    (regression scenario from PR #2581)
# ---------------------------------------------------------------------------


async def test_rtsp_stream_reconnects_past_unreaped_ffmpeg(monkeypatch):
    """RTSP read timeout → kill hangs → generator must still spawn a fresh
    ffmpeg and deliver a frame, not block in cleanup forever."""
    stalled = _StuckPostKillProcess()
    recovered = _FrameProcess()
    processes = iter((stalled, recovered))
    spawned: list[object] = []

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        process = next(processes)
        spawned.append(process)
        return process

    async def fake_create_tls_proxy(_ip_address: str, _port: int):
        return 48521, _FakeServer()

    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(camera, "create_tls_proxy", fake_create_tls_proxy)
    monkeypatch.setattr(camera.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(camera, "_FFMPEG_KILL_TIMEOUT", 0.01)

    stream = camera.generate_rtsp_mjpeg_stream(
        ip_address="192.0.2.17",
        access_code="test-code",
        model="P2S",
        fps=15,
        stream_id="9999-fanout",
        disconnect_event=asyncio.Event(),
        printer_id=9999,
    )

    try:
        chunk = await asyncio.wait_for(anext(stream), timeout=5.0)
        assert b"fresh-frame" in chunk
        assert stalled.killed is True
        assert stalled.post_kill_wait_cancelled.is_set()
        assert len(spawned) == 2, "expected a replacement ffmpeg to be spawned"
    finally:
        stalled._release.set()
        with suppress(Exception):
            await asyncio.wait_for(stream.aclose(), timeout=2.0)


# ---------------------------------------------------------------------------
# 3. Janitor — cleanup_orphaned_streams must not hang on an unreaped process
# ---------------------------------------------------------------------------


async def test_cleanup_orphaned_streams_bounded_on_unreaped_process(monkeypatch):
    monkeypatch.setattr(camera, "_FFMPEG_KILL_TIMEOUT", 0.05)
    monkeypatch.setattr(camera, "_scan_bambu_ffmpeg_pids", lambda: [])

    import os

    # Real pid: janitor layer 2 prunes _spawned_ffmpeg_pids entries whose pid
    # doesn't exist (os.kill(pid, 0)), which would reset the spawn age and
    # skip the stale-stream kill below.
    proc = _StuckPostKillProcess(pid=os.getpid())
    proc.wait_calls = 1  # skip the graceful-terminate branch; janitor kills directly
    sid = "9998-fanout"
    now = time.time()
    camera._active_streams[sid] = proc
    camera._spawned_ffmpeg_pids[proc.pid] = now - 120  # spawned long ago
    camera._stream_last_frame_times[sid] = now - 60  # stale: no frames >30s

    try:
        # Must complete despite proc.wait() never returning.
        await asyncio.wait_for(camera.cleanup_orphaned_streams(), timeout=2.0)

        assert proc.killed is True
        assert sid not in camera._active_streams
        assert proc.pid not in camera._spawned_ffmpeg_pids
    finally:
        proc._release.set()
        camera._active_streams.pop(sid, None)
        camera._spawned_ffmpeg_pids.pop(proc.pid, None)
        camera._stream_last_frame_times.pop(sid, None)
        camera._disconnect_events.pop(sid, None)
