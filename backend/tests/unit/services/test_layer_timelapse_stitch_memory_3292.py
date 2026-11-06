"""The layer-timelapse stitch has bounded memory and never outlives its caller (#3292).

The stitch ran libx264 with no thread limit. x264 sizes its threads from the
cores it can see, and in a container that is the node's, not the pod's CPU
limit: measured over 300 frames, a 1080p stitch peaked at 1.2 GiB on a 30-core
host and a 4K one at 5.3 GiB. ffmpeg runs in Bambuddy's own cgroup, so the
reporter's pod was OOM-killed the moment a long print finished, and the
timelapse was lost.

Two threads, a short lookahead and frames no larger than 1080p bring a 1080p
stitch to about 320 MiB with the same preset and quality. And an ffmpeg the
stitch started is killed and reaped however the stitch ends, not only on its
own deadline.
"""

import asyncio
import json
import shutil
import subprocess
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


def _session(frames: int = 3) -> TimelapseSession:
    session = TimelapseSession(printer_id=1, archive_id=1, camera_url="http://cam/", camera_type="mjpeg")
    for n in range(frames):
        (session.frames_dir / f"layer_{n:05d}.jpg").write_bytes(b"\xff\xd8jpg\xff\xd9")
    session.frame_count = frames
    return session


def _running_process(communicate) -> MagicMock:
    proc = MagicMock()
    proc.returncode = None
    proc.communicate = communicate
    proc.wait = AsyncMock(return_value=-9)
    return proc


async def _stitch_command(base_dir) -> list[str]:
    session = _session()
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))
    spawn = AsyncMock(return_value=proc)
    with (
        patch.object(layer_timelapse, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.create_subprocess_exec", spawn),
    ):
        await session.stitch(base_dir / "out.mp4")
    return list(spawn.await_args.args)


class TestBoundedMemory:
    @pytest.mark.asyncio
    async def test_two_encoder_threads_and_a_short_lookahead(self, base_dir):
        cmd = await _stitch_command(base_dir)
        assert cmd[cmd.index("-threads") + 1] == "2"
        assert "rc-lookahead=10" in cmd[cmd.index("-x264-params") + 1]

    @pytest.mark.asyncio
    async def test_frames_are_scaled_down_to_1080p(self, base_dir):
        cmd = await _stitch_command(base_dir)
        assert cmd[cmd.index("-vf") + 1] == layer_timelapse.STITCH_SCALE_FILTER

    @pytest.mark.asyncio
    async def test_the_quality_settings_are_unchanged(self, base_dir):
        cmd = await _stitch_command(base_dir)
        assert cmd[cmd.index("-preset") + 1] == "medium"
        assert cmd[cmd.index("-crf") + 1] == "23"
        assert cmd[-1] == str(base_dir / "out.mp4")


class TestFfmpegNeverOutlivesTheStitch:
    @pytest.mark.asyncio
    async def test_killed_and_reaped_when_the_stitch_is_cancelled(self, base_dir):
        async def hang():
            await asyncio.Event().wait()

        proc = _running_process(hang)
        with (
            patch.object(layer_timelapse, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        ):
            task = asyncio.create_task(_session().stitch(base_dir / "out.mp4"))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        proc.kill.assert_called_once()
        proc.wait.assert_awaited()

    @pytest.mark.asyncio
    async def test_killed_and_reaped_on_any_other_error(self, base_dir):
        async def broken():
            raise RuntimeError("pipe closed")

        proc = _running_process(broken)
        with (
            patch.object(layer_timelapse, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        ):
            assert await _session().stitch(base_dir / "out.mp4") is False

        proc.kill.assert_called_once()
        proc.wait.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_finished_ffmpeg_is_left_alone(self, base_dir):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        with (
            patch.object(layer_timelapse, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        ):
            assert await _session().stitch(base_dir / "out.mp4") is True

        proc.kill.assert_not_called()


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="needs ffmpeg and ffprobe")
class TestTheScaleFilter:
    """Run the filter through a real ffmpeg: what it does to real frame sizes."""

    @staticmethod
    def _scaled_size(tmp_path, width: int, height: int) -> tuple[int, int]:
        frame = tmp_path / f"in_{width}x{height}.png"
        subprocess.run(
            [
                FFMPEG,
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=gray:s={width}x{height}",
                "-frames:v",
                "1",
                str(frame),
            ],
            check=True,
        )
        out = tmp_path / f"out_{width}x{height}.png"
        subprocess.run(
            [
                FFMPEG,
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(frame),
                "-vf",
                layer_timelapse.STITCH_SCALE_FILTER,
                str(out),
            ],
            check=True,
        )
        probe = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(out),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        stream = json.loads(probe.stdout)["streams"][0]
        return stream["width"], stream["height"]

    def test_4k_comes_out_at_1080p(self, tmp_path):
        assert self._scaled_size(tmp_path, 3840, 2160) == (1920, 1080)

    def test_a_portrait_4k_frame_keeps_its_shape(self, tmp_path):
        assert self._scaled_size(tmp_path, 2160, 3840) == (1080, 1920)

    def test_1080p_and_smaller_are_left_as_they_are(self, tmp_path):
        assert self._scaled_size(tmp_path, 1920, 1080) == (1920, 1080)
        assert self._scaled_size(tmp_path, 720, 1280) == (720, 1280)

    def test_an_odd_size_is_made_even_for_yuv420p(self, tmp_path):
        assert self._scaled_size(tmp_path, 641, 481) == (640, 480)


class TestNoPartialVideo:
    @pytest.mark.asyncio
    async def test_a_cancelled_stitch_removes_what_it_wrote(self, base_dir):
        out = base_dir / "out.mp4"

        async def hang():
            out.write_bytes(b"truncated")
            await asyncio.Event().wait()

        proc = _running_process(hang)
        with (
            patch.object(layer_timelapse, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        ):
            task = asyncio.create_task(_session().stitch(out))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert not out.exists()

    @pytest.mark.asyncio
    async def test_a_stitch_that_errors_removes_what_it_wrote(self, base_dir):
        out = base_dir / "out.mp4"

        async def broken():
            out.write_bytes(b"truncated")
            raise RuntimeError("pipe closed")

        with (
            patch.object(layer_timelapse, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_running_process(broken))),
        ):
            assert await _session().stitch(out) is False

        assert not out.exists()


def test_the_kept_frames_note_gives_the_same_bounded_command(base_dir):
    session = _session()
    assert session.mark_kept("test")
    note = (session.frames_dir / layer_timelapse.KEPT_FRAMES_MARKER).read_text()
    assert f'-vf "{layer_timelapse.STITCH_SCALE_FILTER}"' in note
    assert "-threads 2" in note


@pytest.mark.asyncio
async def test_the_timelapse_editor_encode_is_bounded_too(tmp_path):
    from backend.app.services import timelapse_processor
    from backend.app.utils.ffmpeg_output import X264_BOUNDED_ARGS

    proc = MagicMock()
    proc.returncode = 0
    proc.stderr.read = AsyncMock(return_value=b"")
    proc.stdout.readline = AsyncMock(return_value=b"")
    proc.wait = AsyncMock(return_value=0)
    spawn = AsyncMock(return_value=proc)
    with (
        patch.object(timelapse_processor, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.create_subprocess_exec", spawn),
    ):
        processor = timelapse_processor.TimelapseProcessor(tmp_path / "in.mp4")
        await processor.process(tmp_path / "out.mp4", speed=2.0)

    cmd = list(spawn.await_args.args)
    n = len(X264_BOUNDED_ARGS)
    assert any(tuple(cmd[i : i + n]) == X264_BOUNDED_ARGS for i in range(len(cmd)))
