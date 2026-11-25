"""External/USB camera ffmpeg-leak cleanup (#2675, reporter @bitbarista).

An external USB (V4L2) camera's ffmpeg used to be reachable only from its own
stream generator's ``finally`` — which an abrupt client disconnect can skip
(same cancellation-timing class as #776). Because external streams never
registered into ``_active_streams`` / ``_disconnect_events`` / the spawned-PID
map, both ``/camera/stop`` and ``cleanup_orphaned_streams`` were structurally
blind to the leak, leaving ``/dev/videoN`` locked. The fix registers the external
ffmpeg into the same registries the RTSP path uses.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from contextlib import suppress
from unittest.mock import AsyncMock, mock_open, patch

import pytest

from backend.app.api.routes import camera
from backend.app.services import external_camera


async def _instant_sleep(*_args, **_kwargs) -> None:
    """Drop-in for asyncio.sleep that returns immediately (no self-recursion)."""
    return None


class _CleanProc:
    """ffmpeg that terminates cleanly when asked."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        # Real Process objects always expose these (None when not piped), and
        # _terminate_ffmpeg drains them so a full pipe can't wedge the exit.
        self.stdout = None
        self.stderr = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


class _ImmediateEOFReader:
    async def read(self, _size: int = -1) -> bytes:
        return b""


class _UsbProc:
    """ffmpeg for a USB stream: yields no frames, exits at first read."""

    def __init__(self, pid: int = 52001) -> None:
        self.pid = pid
        self.returncode = None
        self.stdout = _ImmediateEOFReader()
        self.stderr = _ImmediateEOFReader()

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# 1. The stream generator hands its ffmpeg process to the on_process callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_usb_registers_process_via_on_process(monkeypatch):
    """``_stream_usb`` must call ``on_process`` with the spawned ffmpeg so the
    route can register it — this is the linchpin of the whole fix."""

    class _FakePath:
        def __init__(self, _p: str) -> None:
            pass

        def exists(self) -> bool:
            return True

    proc = _UsbProc()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(external_camera, "get_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(external_camera, "Path", _FakePath)
    monkeypatch.setattr(external_camera.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(external_camera.asyncio, "sleep", _instant_sleep)

    captured: list[object] = []
    stream = external_camera._stream_usb("/dev/video0", 10, on_process=captured.append)
    try:
        async for _frame in stream:
            pass
    finally:
        with suppress(Exception):
            await stream.aclose()

    assert captured == [proc], "the spawned ffmpeg process must be handed to on_process"


# ---------------------------------------------------------------------------
# 2. /camera/stop now finds and kills a registered external USB process
#    (the reported {"stopped": 0} → {"stopped": 1})
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_endpoint_terminates_registered_external_process(monkeypatch):
    monkeypatch.setattr(camera, "_FFMPEG_KILL_TIMEOUT", 0.05)
    monkeypatch.setattr(camera, "get_subscriber_count", lambda _key: 0)

    async def fake_shutdown(_key):
        return False

    monkeypatch.setattr(camera, "shutdown_broadcaster", fake_shutdown)

    printer_id = 7
    sid = f"{printer_id}-ext-abc12345"
    proc = _CleanProc(pid=52010)
    event = asyncio.Event()
    camera._active_streams[sid] = proc
    camera._disconnect_events[sid] = event
    camera._spawned_ffmpeg_pids[proc.pid] = time.time()
    camera._stream_last_frame_times[sid] = time.time()

    try:
        result = await camera.stop_camera_stream(printer_id, _=None)
        assert result["stopped"] == 1
        assert proc.returncode is not None, "the external ffmpeg must be terminated"
        assert event.is_set(), "the stream's stop event must be signalled"
        # Registry fully cleaned so it can't be double-reaped.
        assert sid not in camera._active_streams
        assert sid not in camera._disconnect_events
        assert proc.pid not in camera._spawned_ffmpeg_pids
    finally:
        camera._active_streams.pop(sid, None)
        camera._disconnect_events.pop(sid, None)
        camera._spawned_ffmpeg_pids.pop(proc.pid, None)
        camera._stream_last_frame_times.pop(sid, None)


# ---------------------------------------------------------------------------
# 3. The orphan janitor reaps a stale registered external USB stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_janitor_reaps_stale_external_usb_stream(monkeypatch):
    monkeypatch.setattr(camera, "_FFMPEG_KILL_TIMEOUT", 0.05)
    monkeypatch.setattr(camera, "_scan_bambu_ffmpeg_pids", lambda: [])

    import os

    proc = _CleanProc(pid=os.getpid())  # real pid so layer-2 existence check keeps it
    sid = "7-ext-deadbeef"
    now = time.time()
    camera._active_streams[sid] = proc
    camera._spawned_ffmpeg_pids[proc.pid] = now - 120  # spawned long ago
    camera._stream_last_frame_times[sid] = now - 60  # stale: no frames >30s
    camera._disconnect_events[sid] = asyncio.Event()

    try:
        await asyncio.wait_for(camera.cleanup_orphaned_streams(), timeout=2.0)
        assert proc.returncode is not None, "stale external ffmpeg must be killed"
        assert sid not in camera._active_streams
    finally:
        camera._active_streams.pop(sid, None)
        camera._spawned_ffmpeg_pids.pop(proc.pid, None)
        camera._stream_last_frame_times.pop(sid, None)
        camera._disconnect_events.pop(sid, None)


# ---------------------------------------------------------------------------
# 4. The /proc "nuclear net" now matches USB (v4l2) ffmpeg from prior sessions
# ---------------------------------------------------------------------------


def test_scan_matches_v4l2_ffmpeg(monkeypatch):
    cmdline = b"ffmpeg\x00-f\x00v4l2\x00-i\x00/dev/video0\x00-f\x00mjpeg\x00-\x00"
    monkeypatch.setattr("os.listdir", lambda _p: ["52020"])
    monkeypatch.setattr(camera, "_read_ppid", lambda _pid: 1)
    with patch("builtins.open", mock_open(read_data=cmdline)):
        assert 52020 in camera._scan_bambu_ffmpeg_pids()


def test_scan_matches_an_orphaned_rtsp_ffmpeg(monkeypatch):
    cmdline = b"ffmpeg\x00-i\x00rtsp://bblp:code@127.0.0.1:4000/streaming/live/1\x00-\x00"
    monkeypatch.setattr("os.listdir", lambda _p: ["52022"])
    monkeypatch.setattr(camera, "_read_ppid", lambda _pid: 1)
    with patch("builtins.open", mock_open(read_data=cmdline)):
        assert camera._scan_bambu_ffmpeg_pids() == [52022]


@pytest.mark.skipif(sys.platform != "linux", reason="/proc")
def test_scan_leaves_another_processes_camera_ffmpeg_alone(tmp_path):
    """For real, through /proc: the same command run by another program (here
    a shell standing in for another Bambuddy) is not found; ours is."""
    import subprocess

    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    url = "rtsp://bblp:code@127.0.0.1:4000/streaming/live/1"
    theirs = subprocess.Popen(["sh", "-c", f"'{fake}' -i '{url}' -; true"])
    ours = subprocess.Popen([str(fake), "-i", url, "-"])
    try:
        deadline = time.monotonic() + 5
        other = None
        while other is None and time.monotonic() < deadline:
            for pid in os.listdir("/proc"):
                if pid.isdigit() and camera._read_ppid(int(pid)) == theirs.pid:
                    other = int(pid)
            time.sleep(0.05)
        assert other is not None, "the other program's ffmpeg did not start"

        found = camera._scan_bambu_ffmpeg_pids()

        assert ours.pid in found
        assert other not in found
    finally:
        for proc in (theirs, ours):
            proc.kill()
            proc.wait()
        subprocess.run(["pkill", "-f", str(fake)], check=False)


def test_scan_ignores_unrelated_ffmpeg(monkeypatch):
    # A transcode of a local file is not ours — must not be reaped.
    cmdline = b"ffmpeg\x00-i\x00/home/user/movie.mp4\x00out.mkv\x00"
    monkeypatch.setattr("os.listdir", lambda _p: ["52021"])
    with patch("builtins.open", mock_open(read_data=cmdline)):
        assert camera._scan_bambu_ffmpeg_pids() == []


# ---------------------------------------------------------------------------
# 5. Only our own USB ffmpeg: another program's camera process is left alone
# ---------------------------------------------------------------------------

OWN_PID = 4242
ONE_SHOT = [b"/usr/bin/ffmpeg", b"-f", b"v4l2", b"-i", b"/dev/video0", b"-frames:v", b"1", b"-f", b"image2pipe", b"-"]
STREAM = [b"ffmpeg", b"-f", b"v4l2", b"-framerate", b"10", b"-i", b"/dev/video2", b"-f", b"mjpeg", b"-"]


def test_our_one_shot_and_stream_commands_are_ours():
    assert camera._is_bambuddy_ffmpeg(ONE_SHOT, OWN_PID, OWN_PID)
    assert camera._is_bambuddy_ffmpeg(STREAM, OWN_PID, OWN_PID)


def test_an_orphan_from_an_earlier_run_is_ours():
    """Reparented to init once the Bambuddy that spawned it died."""
    assert camera._is_bambuddy_ffmpeg(STREAM, 1, OWN_PID)


def test_another_programs_usb_ffmpeg_is_not_ours():
    """motion, an OctoPrint webcam or a recording on a bare-metal install."""
    assert not camera._is_bambuddy_ffmpeg(STREAM, 777, OWN_PID)


def test_a_different_v4l2_command_shape_is_not_ours():
    recording = [b"ffmpeg", b"-f", b"v4l2", b"-input_format", b"h264", b"-i", b"/dev/video0", b"/srv/cam.mkv"]
    assert not camera._is_bambuddy_ffmpeg(recording, OWN_PID, OWN_PID)


RTSP = [b"ffmpeg", b"-rtsp_transport", b"tcp", b"-i", b"rtsps://bblp:code@10.0.0.5:322/streaming/live/1", b"-"]


def test_our_bambu_rtsp_and_its_orphans_are_ours():
    assert camera._is_bambuddy_ffmpeg(RTSP, OWN_PID, OWN_PID)
    assert camera._is_bambuddy_ffmpeg(RTSP, 1, OWN_PID)


def test_another_bambuddys_rtsp_is_not_ours():
    """A second install or a test server on the same host runs the same
    command: its cleanup killed this one's live camera every minute."""
    assert not camera._is_bambuddy_ffmpeg(RTSP, 777, OWN_PID)
    assert not camera._is_bambuddy_ffmpeg(RTSP, None, OWN_PID)


def test_unknown_parent_is_not_ours_for_usb():
    assert not camera._is_bambuddy_ffmpeg(STREAM, None, OWN_PID)


# ---------------------------------------------------------------------------
# 6. A stuck ffmpeg can never hang the stream's own cleanup (#2580)
# ---------------------------------------------------------------------------


class _WedgedProc:
    """ffmpeg blocked in write() on a full pipe: SIGTERM is ignored, and after
    SIGKILL wait() never returns while its pipes are unread."""

    def __init__(self) -> None:
        self.pid = 52999
        self.returncode = None
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(b"\xff\xd8" + b"x" * 200 + b"\xff\xd9")
        self.stderr = asyncio.StreamReader()
        self.killed = False

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        await asyncio.Event().wait()
        return -9  # pragma: no cover


@pytest.mark.asyncio
async def test_a_wedged_usb_ffmpeg_does_not_hang_the_stream_cleanup(monkeypatch):
    """The shared upstream's cleanup runs in the fan-out broadcaster, which stop,
    a camera change, printer delete and shutdown all wait on."""
    from backend.app.services import external_camera

    monkeypatch.setattr(camera, "_FFMPEG_TERM_TIMEOUT", 0.05)
    monkeypatch.setattr(camera, "_FFMPEG_KILL_TIMEOUT", 0.05)
    proc = _WedgedProc()
    monkeypatch.setattr(external_camera, "get_ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(external_camera, "_safe_usb_device_path", lambda _d: "/dev/video0")
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=proc))

    stream = external_camera._stream_usb("/dev/video0", 5)
    assert await asyncio.wait_for(anext(stream), 2)

    await asyncio.wait_for(stream.aclose(), 2)

    assert proc.killed
