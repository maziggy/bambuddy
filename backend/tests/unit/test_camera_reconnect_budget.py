"""The camera reconnect budget counts failures in a row, not every drop.

A stock X1C ends each RTSP session after about a minute. The built-in stream
respawned ffmpeg transparently but counted every respawn against a lifetime
budget of 30, so a live view stopped for good after about half an hour. The
external-camera path allowed three reconnects for the life of the stream.
"""

import asyncio
from contextlib import suppress

import pytest

from backend.app.api.routes import camera
from backend.app.services import external_camera
from backend.app.services.camera_profiles import CameraProfile

FRAME = b"\xff\xd8frame\xff\xd9"


class _FakeServer:
    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


class _Stdout:
    def __init__(self, frames: int) -> None:
        self._frames = frames

    async def read(self, _size: int = -1) -> bytes:
        if self._frames <= 0:
            return b""
        self._frames -= 1
        return FRAME


class _Proc:
    _next_pid = 78000

    def __init__(self, frames: int) -> None:
        _Proc._next_pid += 1
        self.pid = _Proc._next_pid
        self.returncode = None
        self.stdout = _Stdout(frames)
        self.stderr = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


@pytest.fixture
def rtsp(monkeypatch):
    """Fake ffmpeg sessions: each call spawns the next entry's frame count."""
    sessions: list[int] = []
    spawned: list[int] = []
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake_exec(*_args, **_kwargs):
        frames = sessions.pop(0) if sessions else 0
        spawned.append(frames)
        return _Proc(frames)

    async def _fake_proxy(_ip: str, _port: int):
        return 48998, _FakeServer()

    async def _recording_sleep(seconds, *args, **kwargs):
        delays.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(camera, "create_tls_proxy", _fake_proxy)
    monkeypatch.setattr(camera.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(camera.asyncio, "sleep", _recording_sleep)
    return sessions, spawned, delays


def _use_profile(monkeypatch, **kwargs):
    monkeypatch.setattr(camera, "get_camera_profile", lambda _model: CameraProfile(**kwargs))


async def _drain(stream, limit: int = 200) -> int:
    frames = 0
    async for chunk in stream:
        if b"image/jpeg" in chunk:
            frames += 1
        if frames >= limit:
            break
    return frames


def _stream():
    return camera.generate_rtsp_mjpeg_stream(
        ip_address="192.0.2.40",
        access_code="test-code",
        model="X1C",
        fps=10,
        stream_id="99-fanout-budget",
        disconnect_event=asyncio.Event(),
    )


async def test_routine_drops_never_use_up_the_budget(rtsp, monkeypatch):
    """Ten sessions that each deliver video and end, with a budget of two."""
    sessions, spawned, _ = rtsp
    _use_profile(monkeypatch, rtsp_reconnect_max=2, rtsp_reconnect_delay=0.2)
    sessions.extend([3] * 10)

    stream = _stream()
    frames = await asyncio.wait_for(_drain(stream, limit=30), timeout=10)
    with suppress(Exception):
        await stream.aclose()

    assert frames == 30
    assert len(spawned) == 10


async def test_failures_in_a_row_still_give_up(rtsp, monkeypatch):
    sessions, spawned, _ = rtsp
    _use_profile(monkeypatch, rtsp_reconnect_max=3, rtsp_reconnect_delay=0.2)
    sessions.extend([2])  # then every session fails without a frame

    frames = await asyncio.wait_for(_drain(_stream()), timeout=10)

    assert frames == 2
    # The good session, then three reconnects that each fail -- the budget of
    # three consecutive reconnects -- and the stream gives up.
    assert spawned == [2, 0, 0, 0]


async def test_failures_back_off_and_a_good_session_resets_the_delay(rtsp, monkeypatch):
    sessions, _, delays = rtsp
    _use_profile(monkeypatch, rtsp_reconnect_max=6, rtsp_reconnect_delay=0.2, rtsp_reconnect_backoff_max=1.0)
    sessions.extend([1, 0, 0, 0, 1])

    await asyncio.wait_for(_drain(_stream()), timeout=10)

    # 0.1 is the post-spawn startup check, not a reconnect delay.
    reconnect_delays = [d for d in delays if d != 0.1]
    assert reconnect_delays == [0.2, 0.4, 0.8, 1.0, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0]


# ---------------------------------------------------------------------------
# External cameras
# ---------------------------------------------------------------------------


@pytest.fixture
def external(monkeypatch):
    sessions: list[int] = []
    opened: list[int] = []
    closed: list[int] = []

    def _fake_stream_rtsp(_url, _fps, on_process=None):
        frames = sessions.pop(0) if sessions else 0
        index = len(opened)
        opened.append(frames)

        async def _gen():
            try:
                for _ in range(frames):
                    yield FRAME
            finally:
                closed.append(index)

        return _gen()

    async def _no_sleep(_seconds, *args, **kwargs):
        return None

    monkeypatch.setattr(external_camera, "_stream_rtsp", _fake_stream_rtsp)
    monkeypatch.setattr(external_camera.asyncio, "sleep", _no_sleep)
    return sessions, opened, closed


async def test_external_routine_drops_keep_the_stream_going(external):
    """Used to end for good on the fourth drop."""
    sessions, opened, _ = external
    sessions.extend([2, 2, 2, 2, 2, 2])

    frames = [f async for f in external_camera.generate_mjpeg_stream("rtsp://cam.test/live", "rtsp", 10)]

    assert len(frames) == 12
    assert opened == [2, 2, 2, 2, 2, 2, 0], "a session with no frame ends the stream"


async def test_external_stops_when_asked(external):
    sessions, opened, _ = external
    sessions.extend([1] * 10)
    stop = asyncio.Event()

    frames = []
    async for frame in external_camera.generate_mjpeg_stream("rtsp://cam.test/live", "rtsp", 10, stop_event=stop):
        frames.append(frame)
        if len(frames) == 3:
            stop.set()

    assert len(frames) == 3
    assert len(opened) == 3


async def test_closing_the_external_stream_closes_the_open_session(external):
    """The session owns the ffmpeg process; it must stop when the viewer goes,
    not whenever the abandoned iterator happens to be collected."""
    sessions, _, closed = external
    sessions.extend([50])

    stream = external_camera.generate_mjpeg_stream("rtsp://cam.test/live", "rtsp", 10)
    await anext(stream)
    await stream.aclose()

    assert closed == [0]


async def test_a_cancelled_viewer_is_not_redialled(monkeypatch):
    """The real sessions swallow CancelledError and just end. Without a check,
    the now-unlimited reconnect loop would read that as a routine drop."""
    opened: list[int] = []
    reading = asyncio.Event()
    swallow = [True]

    def _fake_stream_rtsp(_url, _fps, on_process=None):
        opened.append(len(opened))

        async def _gen():
            yield FRAME
            try:
                reading.set()
                await asyncio.Event().wait()  # blocked on the camera
            except asyncio.CancelledError:
                if not swallow[0]:
                    raise
                return  # swallowed, exactly like _stream_rtsp

        return _gen()

    monkeypatch.setattr(external_camera, "_stream_rtsp", _fake_stream_rtsp)

    async def _viewer():
        async for _ in external_camera.generate_mjpeg_stream("rtsp://cam.test/live", "rtsp", 10):
            pass

    task = asyncio.create_task(_viewer())
    await asyncio.wait_for(reading.wait(), timeout=5)
    task.cancel()
    # asyncio.wait, not wait_for: on a regression the loop redials forever,
    # and wait_for would hang waiting for the cancelled task to finish.
    done, _ = await asyncio.wait({task}, timeout=5)
    if not done:
        swallow[0] = False
        task.cancel()
        await asyncio.wait({task}, timeout=5)

    assert done, "the cancelled viewer's stream kept running"
    assert opened == [0], "a cancelled viewer's stream must not open another session"
