"""A layer timelapse is not lost to a slow stitch (TO-C8).

Stitching was killed at a flat 300 s, and the frames were then deleted: a
1500-layer print on a Raspberry Pi 4 takes longer than that to encode, so its
timelapse was gone for good. The deadline now grows with the frame count
(half a second per frame, at least 5 minutes, at most an hour), and a stitch
that fails keeps its frames for a day so the video can still be made.
"""

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import layer_timelapse
from backend.app.services.layer_timelapse import TimelapseSession


@pytest.fixture
def base_dir(tmp_path, monkeypatch):
    fake = MagicMock(base_dir=tmp_path)
    monkeypatch.setattr(layer_timelapse, "settings", fake)
    layer_timelapse._active_sessions.clear()
    layer_timelapse._finalizing_sessions.clear()
    yield tmp_path
    layer_timelapse._active_sessions.clear()
    layer_timelapse._finalizing_sessions.clear()


def _session_with_frames(printer_id: int, frames: int) -> TimelapseSession:
    session = TimelapseSession(printer_id=printer_id, archive_id=1, camera_url="http://cam/", camera_type="mjpeg")
    for n in range(frames):
        (session.frames_dir / f"layer_{n:05d}.jpg").write_bytes(b"\xff\xd8jpg\xff\xd9")
    session.frame_count = frames
    return session


@pytest.mark.parametrize(("frames", "seconds"), [(10, 300), (600, 300), (1500, 750), (100_000, 3600)])
def test_the_stitch_deadline_grows_with_the_frames(frames, seconds):
    assert layer_timelapse._stitch_timeout(frames) == seconds


@pytest.mark.asyncio
async def test_a_stitch_past_its_deadline_is_killed_and_reaped(base_dir, monkeypatch):
    session = _session_with_frames(1, 3)
    proc = MagicMock()

    async def never():
        await asyncio.Event().wait()

    proc.communicate = never
    proc.returncode = None  # still running when the deadline passes
    proc.wait = AsyncMock(return_value=-9)
    monkeypatch.setattr(layer_timelapse, "_stitch_timeout", lambda _frames: 0.05)

    with (
        patch.object(layer_timelapse, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        assert await session.stitch(base_dir / "out.mp4") is False

    proc.kill.assert_called_once()
    proc.wait.assert_awaited()


@pytest.mark.asyncio
async def test_a_failed_stitch_keeps_its_frames(base_dir):
    session = _session_with_frames(1, 3)
    layer_timelapse._active_sessions[1] = session

    async def ffmpeg_failed(self, _output, fps=30):
        self.ffmpeg_ran = True
        return False

    with patch.object(TimelapseSession, "stitch", ffmpeg_failed):
        assert await layer_timelapse.on_print_complete(1) is None

    assert len(list(session.frames_dir.glob("layer_*.jpg"))) == 3
    marker = session.frames_dir / layer_timelapse.KEPT_FRAMES_MARKER
    assert marker.exists() and "frames.txt" in marker.read_text()


@pytest.mark.asyncio
async def test_without_ffmpeg_the_frames_are_not_kept(base_dir):
    """Nothing a later attempt could do differently: an install without ffmpeg
    would otherwise keep every print's frames."""
    session = _session_with_frames(1, 3)
    layer_timelapse._active_sessions[1] = session

    with patch.object(layer_timelapse, "get_ffmpeg_path", return_value=None):
        assert await layer_timelapse.on_print_complete(1) is None

    assert not session.frames_dir.exists()


@pytest.mark.asyncio
async def test_a_stitch_cut_short_by_a_restart_keeps_its_frames(base_dir):
    """Marked before ffmpeg starts: at startup the janitor sees a kept folder."""
    session = _session_with_frames(1, 3)
    proc = MagicMock()
    marked_while_running = []

    async def interrupted():
        marked_while_running.append((session.frames_dir / layer_timelapse.KEPT_FRAMES_MARKER).exists())
        raise RuntimeError("process ended")

    proc.communicate = interrupted
    with (
        patch.object(layer_timelapse, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        assert await session.stitch(base_dir / "out.mp4") is False

    assert marked_while_running == [True]


@pytest.mark.asyncio
async def test_a_failed_stitch_leaves_no_partial_video(base_dir, monkeypatch):
    session = _session_with_frames(1, 3)
    output = session.frames_dir.parent / "timelapse_x.mp4"
    proc = MagicMock()
    proc.returncode = 1

    async def partial():
        output.write_bytes(b"half a video")
        return b"", b"encoder error"

    proc.communicate = partial
    with (
        patch.object(layer_timelapse, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        assert await session.stitch(output) is False

    assert not output.exists()


@pytest.mark.asyncio
async def test_two_stitches_of_one_printer_are_both_protected(base_dir):
    """A short print can finish while a long one still stitches; the janitor
    now runs while Bambuddy does, so the first must stay protected."""
    long_session = _session_with_frames(1, 3)
    long_session.session_id = "20260101_000000"
    short_session = _session_with_frames(1, 3)
    short_session.session_id = "20260101_000500"
    release = asyncio.Event()

    async def slow(self, _output, fps=30):
        if self is long_session:
            await release.wait()
        return True

    with patch.object(TimelapseSession, "stitch", slow):
        layer_timelapse._active_sessions[1] = long_session
        first = asyncio.create_task(layer_timelapse.on_print_complete(1))
        await asyncio.sleep(0)
        layer_timelapse._active_sessions[1] = short_session
        await layer_timelapse.on_print_complete(1)

        assert layer_timelapse._finalizing_sessions[1] == {"20260101_000000"}
        release.set()
        await first
    assert 1 not in layer_timelapse._finalizing_sessions


@pytest.mark.asyncio
async def test_a_successful_stitch_still_cleans_up(base_dir):
    session = _session_with_frames(1, 3)
    layer_timelapse._active_sessions[1] = session

    with patch.object(TimelapseSession, "stitch", AsyncMock(return_value=True)):
        assert await layer_timelapse.on_print_complete(1) is not None

    assert not session.frames_dir.exists()


def test_the_janitor_leaves_kept_frames_for_a_day(base_dir):
    kept = base_dir / "timelapse_frames" / "1" / "20260101_000000"
    kept.mkdir(parents=True)
    (kept / "layer_00000.jpg").write_bytes(b"jpg")
    (kept / layer_timelapse.KEPT_FRAMES_MARKER).write_text("stitch failed")
    hour_ago = time.time() - 3600
    os.utime(kept, (hour_ago, hour_ago))
    os.utime(kept / layer_timelapse.KEPT_FRAMES_MARKER, (hour_ago, hour_ago))

    assert layer_timelapse.cleanup_orphaned_timelapse_sessions() == 0
    assert kept.exists()

    old = time.time() - layer_timelapse.KEEP_FAILED_FRAMES_SECONDS - 60
    os.utime(kept / layer_timelapse.KEPT_FRAMES_MARKER, (old, old))
    assert layer_timelapse.cleanup_orphaned_timelapse_sessions() == 1
    assert not kept.exists()


def test_the_janitor_still_reaps_ordinary_orphans(base_dir):
    orphan = base_dir / "timelapse_frames" / "1" / "20260101_000001"
    orphan.mkdir(parents=True)
    hour_ago = time.time() - 3600
    os.utime(orphan, (hour_ago, hour_ago))

    assert layer_timelapse.cleanup_orphaned_timelapse_sessions() == 1


@pytest.mark.asyncio
async def test_the_janitor_also_runs_while_bambuddy_does(monkeypatch):
    """Hourly, so kept frames go after a day rather than at the next restart."""
    from backend.app import main

    sweeps = []
    rounds = {"n": 0}

    async def fake_sleep(_seconds):
        rounds["n"] += 1
        if rounds["n"] >= main.TIMELAPSE_CLEANUP_EVERY * 2:
            raise asyncio.CancelledError

    monkeypatch.setattr("backend.app.api.routes.camera.cleanup_orphaned_streams", AsyncMock())
    monkeypatch.setattr(layer_timelapse, "cleanup_orphaned_timelapse_sessions", lambda: sweeps.append(1) or 0)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await main._camera_cleanup_loop()

    assert len(sweeps) == 2


@pytest.mark.asyncio
async def test_a_failed_attach_leaves_the_next_prints_session_alone():
    """The completed print's session left _active_sessions when its stitch
    began; the one there now is the next print's."""
    import inspect

    from backend.app import main

    source = inspect.getsource(main)
    i = source.index('logger.warning("[LAYER-TL] Failed: %s", e)')
    assert 'if print_status != "completed":' in source[i : i + 600]


@pytest.mark.asyncio
async def test_ffmpeg_that_cannot_be_started_does_not_keep_the_frames(base_dir):
    session = _session_with_frames(1, 3)
    layer_timelapse._active_sessions[1] = session

    with (
        patch.object(layer_timelapse, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=PermissionError("not executable"))),
    ):
        assert await layer_timelapse.on_print_complete(1) is None

    assert not session.frames_dir.exists()


@pytest.mark.asyncio
async def test_a_session_is_protected_before_it_leaves_the_active_list(base_dir, monkeypatch):
    """The janitor runs in a thread: there must be no moment in which the
    stitching session is in neither list."""
    session = _session_with_frames(1, 3)
    layer_timelapse._active_sessions[1] = session
    seen = []

    class Watched(dict):
        def pop(self, key, default=None):
            seen.append(session.session_id in layer_timelapse._finalizing_sessions.get(key, set()))
            return super().pop(key, default)

    monkeypatch.setattr(layer_timelapse, "_active_sessions", Watched(layer_timelapse._active_sessions))
    with patch.object(TimelapseSession, "stitch", AsyncMock(return_value=True)):
        await layer_timelapse.on_print_complete(1)

    assert seen == [True]
    assert 1 not in layer_timelapse._finalizing_sessions


@pytest.mark.asyncio
async def test_a_session_without_frames_is_not_left_protected(base_dir):
    session = _session_with_frames(1, 0)
    layer_timelapse._active_sessions[1] = session

    assert await layer_timelapse.on_print_complete(1) is None
    assert 1 not in layer_timelapse._finalizing_sessions
    assert 1 not in layer_timelapse._active_sessions


@pytest.mark.asyncio
async def test_the_janitor_runs_even_when_the_stream_cleanup_fails(monkeypatch):
    from backend.app import main

    sweeps = []
    rounds = {"n": 0}

    async def fake_sleep(_seconds):
        rounds["n"] += 1
        if rounds["n"] >= main.TIMELAPSE_CLEANUP_EVERY:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        "backend.app.api.routes.camera.cleanup_orphaned_streams", AsyncMock(side_effect=RuntimeError("ps failed"))
    )
    monkeypatch.setattr(layer_timelapse, "cleanup_orphaned_timelapse_sessions", lambda: sweeps.append(1) or 0)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await main._camera_cleanup_loop()

    assert sweeps == [1]


@pytest.mark.asyncio
async def test_an_ffmpeg_error_keeps_the_frames_end_to_end(base_dir):
    session = _session_with_frames(1, 3)
    layer_timelapse._active_sessions[1] = session
    proc = MagicMock()
    proc.returncode = 1
    proc.communicate = AsyncMock(return_value=(b"", b"encoder error"))

    with (
        patch.object(layer_timelapse, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        assert await layer_timelapse.on_print_complete(1) is None

    assert len(list(session.frames_dir.glob("layer_*.jpg"))) == 3
