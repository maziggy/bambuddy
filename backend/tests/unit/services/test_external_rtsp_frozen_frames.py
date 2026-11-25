"""A frozen external RTSP camera is reconnected (RC-C1).

ffmpeg can go on repeating its last frame after the camera has stopped sending
(`-r fps` duplicates it), so frame counting sees a healthy stream: status says
live, and timelapse, finish photos and Obico reuse a picture that stopped
hours ago. The printer's own RTSP stream restarts ffmpeg after 20 s of
byte-identical frames (#3218); the external one now does the same, with the
same still-scene rule for a camera that really shows an unchanging picture.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services import external_camera
from backend.app.services.external_camera import _FreezeWatch

A = b"\xff\xd8" + b"a" * 200 + b"\xff\xd9"
B = b"\xff\xd8" + b"b" * 200 + b"\xff\xd9"


class TestTheFreezeWatch:
    def test_a_moving_picture_never_freezes(self):
        watch = _FreezeWatch()
        watch.start_session()
        assert not any(watch.frozen(A if i % 2 else B, float(i)) for i in range(100))

    def test_the_same_frame_for_twenty_seconds_is_frozen(self):
        watch = _FreezeWatch()
        watch.start_session()
        assert not watch.frozen(A, 0.0)
        assert not watch.frozen(A, 19.0)
        assert watch.frozen(A, 21.0)

    def test_the_same_picture_after_a_restart_is_a_still_scene(self):
        """A dark, idle chamber can encode to the same frame every time: then
        the stream only re-checks every few minutes."""
        watch = _FreezeWatch()
        watch.start_session()
        watch.frozen(A, 0.0)
        assert watch.frozen(A, 21.0)

        watch.start_session()
        assert not watch.frozen(A, 22.0)
        assert not watch.frozen(A, 22.0 + 299.0)
        assert watch.frozen(A, 22.0 + 301.0)

    def test_a_changed_picture_ends_the_still_scene(self):
        watch = _FreezeWatch()
        watch.start_session()
        watch.frozen(A, 0.0)
        watch.frozen(A, 21.0)
        watch.start_session()
        watch.frozen(A, 22.0)

        watch.frozen(B, 23.0)

        assert not watch.frozen(B, 40.0)
        assert watch.frozen(B, 44.0)


class TestASlowOrQuietCamera:
    """Unlike a Bambu camera, an external one can legitimately repeat frames for
    longer than 20 s: a source slower than one picture per 20 s (ffmpeg's -r
    repeats each until the next), or a surveillance camera's smart codec on a
    static night scene. Restarting those every 20 s would break a camera that
    worked; the limit grows to three times the longest gap seen between real
    changes, up to the still-scene interval."""

    def test_a_slow_source_is_restarted_once_then_left_alone(self):
        watch = _FreezeWatch()
        watch.start_session()
        watch.frozen(A, 0.0)
        assert watch.frozen(A, 21.0)  # looked frozen: no way to know yet

        # After the restart the picture is a new one: it had not frozen, it is slow.
        watch.start_session()
        assert not watch.frozen(B, 23.0)
        assert not watch.frozen(B, 23.0 + 60.0)  # 3 x the 21 s gap it showed
        assert watch.frozen(B, 23.0 + 64.0)

    def test_a_gap_seen_within_a_session_raises_the_limit(self):
        watch = _FreezeWatch()
        watch.start_session()
        watch.frozen(A, 0.0)
        watch.frozen(B, 15.0)  # a 15 s gap between real pictures
        assert not watch.frozen(B, 15.0 + 44.0)
        assert watch.frozen(B, 15.0 + 46.0)

    def test_the_limit_never_exceeds_the_still_scene_interval(self):
        watch = _FreezeWatch()
        watch.start_session()
        watch.frozen(A, 0.0)
        watch.frozen(B, 200.0)  # 3 x 200 s would be 10 minutes
        assert watch.frozen(B, 200.0 + 301.0)


class _Ffmpeg:
    """An ffmpeg that repeats one frame forever, as after a dead upstream."""

    def __init__(self, frame: bytes) -> None:
        self.pid = 53001
        self.returncode = None
        self.stdout = MagicMock()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self._frame = frame

        async def read(_n):
            await asyncio.sleep(0.005)
            return self._frame

        self.stdout.read = read

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return 0


@pytest.fixture
def ffmpeg(monkeypatch):
    spawned: list[_Ffmpeg] = []

    async def spawn(*_args, **_kwargs):
        proc = _Ffmpeg(A)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(external_camera, "get_ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(external_camera, "read_ffmpeg_log_until_open", AsyncMock(return_value=("open", b"")))
    monkeypatch.setattr(external_camera, "watch_ffmpeg_log", AsyncMock(return_value=None))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr(external_camera, "_EXTERNAL_RTSP_FROZEN_SECONDS", 0.1)
    monkeypatch.setattr(external_camera.asyncio, "sleep", _fast_sleep(asyncio.sleep))
    return spawned


def _fast_sleep(real_sleep):
    async def sleep(delay, *args, **kwargs):
        # The reconnect pause; the frame pacing stays as it is.
        return await real_sleep(min(delay, 0.01), *args, **kwargs)

    return sleep


@pytest.mark.asyncio
async def test_a_frozen_session_ends(ffmpeg):
    session = external_camera._stream_rtsp("rtsp://192.0.2.80/live", 5, freeze=_FreezeWatch())

    frames = 0
    async with asyncio.timeout(3):
        async for _ in session:
            frames += 1

    assert frames > 1
    # It ended on the freeze, not on its way out: ffmpeg was still running.
    assert ffmpeg[0].returncode is not None


@pytest.mark.asyncio
async def test_the_stream_reconnects_a_frozen_camera(ffmpeg):
    stream = external_camera.generate_mjpeg_stream("rtsp://192.0.2.80/live", "rtsp", 5)

    async with asyncio.timeout(3):
        while len(ffmpeg) < 2:
            await anext(stream)
    await stream.aclose()

    assert len(ffmpeg) >= 2
