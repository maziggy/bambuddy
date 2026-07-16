"""Regression tests for bounded RTSP subprocess cleanup."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from backend.app.api.routes import camera

pytestmark = pytest.mark.asyncio


class _FakeServer:
    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


class _TimeoutReader:
    async def read(self, _size: int = -1) -> bytes:
        raise TimeoutError


class _SingleFrameReader:
    def __init__(self) -> None:
        self._sent = False

    async def read(self, _size: int = -1) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return b"\xff\xd8fresh-frame\xff\xd9"


class _StuckPostKillProcess:
    """ffmpeg whose post-kill wait never completes unless cancelled."""

    def __init__(self) -> None:
        self.pid = 41001
        self.returncode = None
        self.stdout = _TimeoutReader()
        self.stderr = None
        self.wait_calls = 0
        self.killed = False
        self.post_kill_wait_started = asyncio.Event()
        self.post_kill_wait_cancelled = asyncio.Event()
        self.release_post_kill_wait = asyncio.Event()

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise TimeoutError
        self.post_kill_wait_started.set()
        try:
            await self.release_post_kill_wait.wait()
        except asyncio.CancelledError:
            self.post_kill_wait_cancelled.set()
            raise
        self.returncode = -9
        return self.returncode


class _FrameProcess:
    def __init__(self) -> None:
        self.pid = 41002
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


async def test_rtsp_stream_reconnects_without_waiting_indefinitely_for_killed_ffmpeg(monkeypatch):
    """A stuck post-kill wait must not pin the fan-out pump indefinitely."""
    stalled = _StuckPostKillProcess()
    recovered = _FrameProcess()
    processes = iter((stalled, recovered))
    spawn_count = 0
    recovered_spawned = asyncio.Event()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        nonlocal spawn_count
        spawn_count += 1
        process = next(processes)
        if process is recovered:
            recovered_spawned.set()
        return process

    async def fake_create_tls_proxy(_ip_address: str, _port: int):
        return 48521, _FakeServer()

    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(camera, "create_tls_proxy", fake_create_tls_proxy)
    monkeypatch.setattr(camera.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(camera, "_FFMPEG_KILL_TIMEOUT", 0.01)

    stream = camera.generate_rtsp_mjpeg_stream(
        ip_address="192.0.2.17",
        access_code="redacted",
        model="P2S",
        fps=15,
        stream_id="1-fanout",
        disconnect_event=asyncio.Event(),
        printer_id=1,
    )

    next_chunk = asyncio.create_task(anext(stream))
    try:
        await asyncio.wait_for(recovered_spawned.wait(), timeout=1.0)
        chunk = await asyncio.wait_for(asyncio.shield(next_chunk), timeout=1.0)

        assert b"fresh-frame" in chunk
        assert stalled.killed is True
        assert stalled.post_kill_wait_started.is_set()
        assert stalled.post_kill_wait_cancelled.is_set()
        assert spawn_count == 2
    finally:
        stalled.release_post_kill_wait.set()
        if not next_chunk.done():
            try:
                await asyncio.wait_for(asyncio.shield(next_chunk), timeout=1.0)
            except (TimeoutError, StopAsyncIteration):
                next_chunk.cancel()
                with suppress(asyncio.CancelledError):
                    await next_chunk
        await stream.aclose()
