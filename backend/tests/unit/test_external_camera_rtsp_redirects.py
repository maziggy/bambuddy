"""An RTSP camera's redirect is not followed into a frame.

The camera guard checks the configured RTSP URL, but ffmpeg's RTSP demuxer
follows a server's 3xx Location by itself and has no option to refuse it, so
a camera host could steer ffmpeg to an RTSP service the guard refuses (one on
the Bambuddy host itself) and its frames would reach the viewers. ffmpeg logs
the redirect before it connects there, and reports the input open before it
writes any frame: a redirect ends the fetch, and no frame is read before the
input is open.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import external_camera
from backend.tests._fixtures.external_camera import INPUT_OPEN_LOG, fake_ffmpeg, stream_of

CAMERA = "rtsp://admin:pw@192.168.1.50:554/live"
JPEG = b"\xff\xd8" + b"\x00" * 200 + b"\xff\xd9"
REDIRECT_LOG = b"[rtsp @ 0x5eb63792b240] Status 302: Redirecting to rtsp://u:pw@127.0.0.1:8554/secret\n"
BANNER = b"ffmpeg version 7.1.5 Copyright (c) 2000-2026 the FFmpeg developers\n"


def _ffmpeg(stderr: bytes, stdout: bytes = JPEG, returncode: int = 0):
    """An ffmpeg process whose pipes really stream; kill is recorded."""
    process = MagicMock()
    process.returncode = None
    process.stderr = stream_of(stderr)
    process.stdout = stream_of(stdout)

    def _finish(code: int) -> None:
        if process.returncode is None:
            process.returncode = code

    async def _communicate():
        out, err = await process.stdout.read(), await process.stderr.read()
        _finish(returncode)
        return out, err

    async def _wait():
        _finish(returncode)
        return process.returncode

    process.communicate = _communicate
    process.wait = _wait
    process.kill = MagicMock(side_effect=lambda: _finish(-9))
    process.terminate = MagicMock(side_effect=lambda: _finish(-15))
    spawn = patch.object(external_camera.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process))
    return process, spawn


@pytest.mark.asyncio
async def test_a_redirected_capture_gives_no_frame(caplog):
    process, spawn = _ffmpeg(BANNER + REDIRECT_LOG + INPUT_OPEN_LOG)

    with fake_ffmpeg(), spawn:
        frame = await external_camera._capture_rtsp_frame(CAMERA, timeout=5)

    assert frame is None
    process.kill.assert_called()
    # Reported as the redirect it was, not as ffmpeg failing.
    assert "redirects are not followed" in caplog.text
    assert "capture failed" not in caplog.text


@pytest.mark.asyncio
async def test_a_log_line_past_the_reader_limit_ends_the_capture():
    process, spawn = _ffmpeg(b"x" * 70000 + b"\n" + INPUT_OPEN_LOG)

    with fake_ffmpeg(), spawn:
        frame = await external_camera._capture_rtsp_frame(CAMERA, timeout=5)

    assert frame is None
    process.kill.assert_called()


@pytest.mark.asyncio
async def test_a_redirected_stream_gives_no_frame():
    process, spawn = _ffmpeg(BANNER + REDIRECT_LOG + INPUT_OPEN_LOG, stdout=JPEG * 3)

    with fake_ffmpeg(), spawn:
        frames = [frame async for frame in external_camera._stream_rtsp(CAMERA, fps=5)]

    assert frames == []
    process.kill.assert_called()


@pytest.mark.asyncio
async def test_the_redirect_is_logged_without_its_login(caplog):
    _process, spawn = _ffmpeg(REDIRECT_LOG + INPUT_OPEN_LOG)

    with fake_ffmpeg(), spawn:
        await external_camera._capture_rtsp_frame(CAMERA, timeout=5)

    assert "127.0.0.1:8554" in caplog.text
    assert "u:pw@" not in caplog.text


@pytest.mark.asyncio
async def test_a_stream_reads_no_frame_before_the_input_is_open():
    """ffmpeg ending without opening the input: whatever is on stdout is not a frame."""
    _process, spawn = _ffmpeg(BANNER + b"Connection refused\n", stdout=JPEG * 3, returncode=1)

    with fake_ffmpeg(), spawn:
        frames = [frame async for frame in external_camera._stream_rtsp(CAMERA, fps=5)]

    assert frames == []


@pytest.mark.asyncio
async def test_a_capture_that_never_opened_gives_no_frame(caplog):
    _process, spawn = _ffmpeg(BANNER + b"Connection refused\n", stdout=JPEG, returncode=0)

    with fake_ffmpeg(), spawn:
        frame = await external_camera._capture_rtsp_frame(CAMERA, timeout=5)

    assert frame is None
    assert "Connection refused" in caplog.text


@pytest.mark.asyncio
async def test_a_camera_without_a_redirect_still_captures_and_streams():
    _process, spawn = _ffmpeg(BANNER + INPUT_OPEN_LOG)
    with fake_ffmpeg(), spawn:
        assert await external_camera._capture_rtsp_frame(CAMERA, timeout=5) == JPEG

    process, spawn = _ffmpeg(BANNER + INPUT_OPEN_LOG, stdout=JPEG * 3)
    with fake_ffmpeg(), spawn:
        frames = [frame async for frame in external_camera._stream_rtsp(CAMERA, fps=5)]
    assert frames == [JPEG] * 3
    process.kill.assert_not_called()


@pytest.mark.asyncio
async def test_a_redirect_reported_while_streaming_stops_ffmpeg():
    """Belt and braces: ffmpeg follows no redirect once the input is open, but
    should it, the log watch stops it."""
    process = MagicMock()
    process.returncode = None
    process.stderr = asyncio.StreamReader()
    process.stderr.feed_data(INPUT_OPEN_LOG)
    process.stdout = asyncio.StreamReader()
    process.stdout.feed_data(JPEG)

    def _kill():
        process.returncode = -9
        process.stdout.feed_eof()

    process.kill = MagicMock(side_effect=_kill)
    process.wait = AsyncMock(return_value=-9)

    async def _late_redirect():
        await asyncio.sleep(0.05)
        process.stderr.feed_data(b"frame=  1 fps=0.0\r" + REDIRECT_LOG)

    with (
        fake_ffmpeg(),
        patch.object(external_camera.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)),
    ):
        feeder = asyncio.create_task(_late_redirect())
        frames = [frame async for frame in external_camera._stream_rtsp(CAMERA, fps=5)]
        await feeder

    assert frames == [JPEG]
    process.kill.assert_called()


@pytest.mark.asyncio
async def test_a_streaming_ffmpeg_log_is_drained():
    """Nothing read ffmpeg's log while streaming, so a long session could fill
    the pipe and stall ffmpeg on its next log line."""
    process = MagicMock()
    process.returncode = None
    process.stderr = asyncio.StreamReader()
    process.stderr.feed_data(INPUT_OPEN_LOG + b"frame=  1 fps=0.0\r" * 10000)
    process.stdout = stream_of(JPEG)
    process.terminate = MagicMock()
    process.wait = AsyncMock(return_value=0)

    with (
        fake_ffmpeg(),
        patch.object(external_camera.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)),
    ):
        async for _frame in external_camera._stream_rtsp(CAMERA, fps=5):
            await asyncio.sleep(0.05)

    assert len(process.stderr._buffer) == 0


@pytest.mark.asyncio
async def test_ffmpeg_logs_at_the_level_that_carries_both_lines():
    for run in ("capture", "stream"):
        _process, spawn = _ffmpeg(INPUT_OPEN_LOG)
        with fake_ffmpeg(), spawn as spawned:
            if run == "capture":
                await external_camera._capture_rtsp_frame(CAMERA, timeout=5)
            else:
                [frame async for frame in external_camera._stream_rtsp(CAMERA, fps=5)]
        argv = spawned.await_args.args
        assert argv[argv.index("-loglevel") + 1] == "info", run


@pytest.mark.asyncio
async def test_closing_the_stream_stops_the_log_watch():
    process = MagicMock()
    process.returncode = None
    process.stderr = asyncio.StreamReader()  # never ends on its own
    process.stderr.feed_data(INPUT_OPEN_LOG)
    process.stdout = asyncio.StreamReader()
    process.stdout.feed_data(JPEG * 2)
    process.terminate = MagicMock()
    process.wait = AsyncMock(return_value=0)

    def _watches():
        return [t for t in asyncio.all_tasks() if t.get_coro().__name__ == "watch_ffmpeg_log" and not t.done()]

    with (
        fake_ffmpeg(),
        patch.object(external_camera.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)),
    ):
        stream = external_camera._stream_rtsp(CAMERA, fps=5)
        assert await anext(stream) == JPEG
        assert _watches()
        await stream.aclose()
        await asyncio.sleep(0)

    assert _watches() == []
