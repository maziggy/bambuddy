"""A printer camera's RTSP redirect is not followed into a frame either.

The built-in camera paths run ffmpeg on rtsp://bblp:<code>@127.0.0.1:<proxy>,
a local TLS proxy to the printer's address. The proxy forwards the printer's
answers unchanged, so whatever answers at that address could redirect ffmpeg
to an RTSP service on the Bambuddy host or the link-local range, which the
external-camera guard refuses for the same editors. Same rule as there: a
redirect ends the fetch, and no frame is used before the input is open.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes import camera as camera_routes
from backend.app.services import camera as camera_service
from backend.tests._fixtures.external_camera import INPUT_OPEN_LOG, stream_of

JPEG = b"\xff\xd8" + b"\x00" * 200 + b"\xff\xd9"
REDIRECT_LOG = b"[rtsp @ 0x5eb63792b240] Status 302: Redirecting to rtsp://127.0.0.1:8554/secret\n"
BANNER = b"ffmpeg version 7.1.5 Copyright (c) 2000-2026 the FFmpeg developers\n"


class _FakeServer:
    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


class _Proc:
    """An ffmpeg process whose pipes really stream."""

    _next_pid = 79000

    def __init__(self, stderr: bytes, stdout: bytes, returncode: int = 0) -> None:
        _Proc._next_pid += 1
        self.pid = _Proc._next_pid
        self.returncode = None
        self._exit = returncode
        self.stderr = stream_of(stderr)
        self.stdout = stream_of(stdout)
        self.kill = MagicMock(side_effect=lambda: self._finish(-9))
        self.terminate = MagicMock(side_effect=lambda: self._finish(-15))

    def _finish(self, code: int) -> None:
        if self.returncode is None:
            self.returncode = code

    async def communicate(self):
        out, err = await self.stdout.read(), await self.stderr.read()
        self._finish(self._exit)
        return out, err

    async def wait(self) -> int:
        self._finish(self._exit)
        return self.returncode


def _capture(process: _Proc):
    async def _proxy(_ip, _port):
        return 48998, _FakeServer()

    return (
        patch.object(camera_service, "is_chamber_image_model", return_value=False),
        patch.object(camera_service, "create_tls_proxy", new=_proxy),
        patch.object(camera_service, "close_tls_proxy", new=AsyncMock()),
        patch.object(camera_service, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch.object(camera_service.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)),
    )


async def _capture_frame(process: _Proc):
    p1, p2, p3, p4, p5 = _capture(process)
    with p1, p2, p3, p4, p5 as spawn:
        frame = await camera_service._capture_camera_frame_bytes_uncoalesced("192.168.1.20", "code", "X1C", 5)
    return frame, spawn


@pytest.mark.asyncio
async def test_a_redirected_printer_capture_gives_no_frame(caplog):
    process = _Proc(BANNER + REDIRECT_LOG + INPUT_OPEN_LOG, JPEG)

    frame, _spawn = await _capture_frame(process)

    assert frame is None
    process.kill.assert_called()
    assert "redirects are not followed" in caplog.text


@pytest.mark.asyncio
async def test_a_printer_capture_that_never_opened_gives_no_frame():
    frame, _spawn = await _capture_frame(_Proc(BANNER + b"Connection refused\n", JPEG))

    assert frame is None


@pytest.mark.asyncio
async def test_a_printer_capture_without_a_redirect_still_works():
    process = _Proc(BANNER + INPUT_OPEN_LOG, JPEG)

    frame, spawn = await _capture_frame(process)

    assert frame == JPEG
    process.kill.assert_not_called()
    argv = spawn.await_args.args
    assert argv[argv.index("-loglevel") + 1] == "info"


@pytest.fixture
def printer_stream(monkeypatch):
    """Each spawn returns the next queued _Proc."""
    queue: list[_Proc] = []
    spawned: list[tuple] = []

    async def _exec(*args, **_kwargs):
        spawned.append(args)
        return queue.pop(0) if queue else _Proc(b"", b"", returncode=1)

    async def _proxy(_ip, _port):
        return 48998, _FakeServer()

    monkeypatch.setattr(camera_routes, "get_ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(camera_routes, "create_tls_proxy", _proxy)
    monkeypatch.setattr(camera_routes, "close_tls_proxy", AsyncMock())
    monkeypatch.setattr(camera_routes.asyncio, "create_subprocess_exec", _exec)
    return queue, spawned


async def _frames(limit: int = 10) -> int:
    count = 0
    stream = camera_routes.generate_rtsp_mjpeg_stream(
        ip_address="192.168.1.20", access_code="code", model="X1C", fps=10, stream_id="7-redirect-test"
    )
    try:
        async for chunk in stream:
            if b"image/jpeg" in chunk:
                count += 1
            if count >= limit:
                break
    finally:
        await stream.aclose()
    return count


@pytest.mark.asyncio
async def test_a_redirected_printer_stream_gives_no_frame_and_does_not_redial(printer_stream):
    queue, spawned = printer_stream
    process = _Proc(BANNER + REDIRECT_LOG + INPUT_OPEN_LOG, JPEG * 3)
    queue.append(process)

    assert await _frames() == 0
    process.kill.assert_called()
    assert len(spawned) == 1


@pytest.mark.asyncio
async def test_a_printer_stream_without_a_redirect_still_streams(printer_stream):
    queue, spawned = printer_stream
    queue.append(_Proc(BANNER + INPUT_OPEN_LOG, JPEG * 3))

    assert await _frames(limit=3) == 3
    argv = spawned[0]
    assert argv[argv.index("-loglevel") + 1] == "info"


@pytest.mark.asyncio
async def test_a_redirect_reported_while_the_printer_streams_stops_ffmpeg(printer_stream):
    queue, _spawned = printer_stream
    process = _Proc(b"", b"")
    process.stderr = asyncio.StreamReader()
    process.stderr.feed_data(INPUT_OPEN_LOG)
    process.stdout = asyncio.StreamReader()
    process.stdout.feed_data(JPEG)

    def _kill():
        process._finish(-9)
        process.stdout.feed_eof()
        process.stderr.feed_eof()

    process.kill = MagicMock(side_effect=_kill)
    queue.append(process)

    async def _late_redirect():
        await asyncio.sleep(0.05)
        process.stderr.feed_data(b"frame=  1 fps=0.0\r" + REDIRECT_LOG)

    feeder = asyncio.create_task(_late_redirect())
    await _frames(limit=1)
    await asyncio.sleep(0.1)
    await feeder

    process.kill.assert_called()


@pytest.mark.asyncio
async def test_a_printer_stream_that_ended_before_opening_stops_at_once(printer_stream):
    """Its log ends without the input-open line: no wait for a line that cannot come."""
    queue, _spawned = printer_stream
    queue.append(_Proc(BANNER + b"Connection refused\n", JPEG * 3, returncode=1))

    started = asyncio.get_running_loop().time()
    assert await _frames() == 0
    assert asyncio.get_running_loop().time() - started < 5


@pytest.mark.asyncio
async def test_a_printer_stream_killed_for_a_redirect_does_not_redial(printer_stream):
    """As with a real ffmpeg: killed before its first frame, its stdout just ends.
    That is not a routine drop to reconnect after."""
    queue, spawned = printer_stream
    process = _Proc(b"", b"")
    process.stderr = stream_of(BANNER + REDIRECT_LOG)
    process.stdout = asyncio.StreamReader()

    def _kill():
        process._finish(-9)
        process.stdout.feed_eof()

    process.kill = MagicMock(side_effect=_kill)
    queue.append(process)

    assert await _frames() == 0
    process.kill.assert_called()
    assert len(spawned) == 1


@pytest.mark.asyncio
async def test_a_redirect_logged_after_stdout_ended_still_stops_the_stream(printer_stream):
    """The two pipes are read independently: stdout's end can be seen before
    the log pump reaches the redirect line."""
    queue, spawned = printer_stream
    process = _Proc(b"", b"")
    process.stderr = asyncio.StreamReader()
    queue.append(process)

    async def _late_log():
        await asyncio.sleep(0.2)
        process.stderr.feed_data(BANNER + REDIRECT_LOG)
        process.stderr.feed_eof()

    feeder = asyncio.create_task(_late_log())
    assert await _frames() == 0
    await feeder
    assert len(spawned) == 1
