"""A one-shot ffmpeg never outlives the capture that started it (#3292).

The camera captures killed their ffmpeg on their own deadline and nowhere
else: an error while reading its output, or the capture being cancelled, left
it running. ffmpeg shares Bambuddy's memory limit in a container, so each one
left behind counts against it until the container restarts.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import camera, external_camera
from backend.app.utils.ffmpeg_output import reap_ffmpeg


def _running_process(**overrides) -> MagicMock:
    proc = MagicMock()
    proc.pid = 4242
    proc.returncode = None
    proc.wait = AsyncMock(return_value=-9)
    proc.communicate = AsyncMock(side_effect=RuntimeError("pipe closed"))
    for key, value in overrides.items():
        setattr(proc, key, value)
    return proc


class TestReapFfmpeg:
    @pytest.mark.asyncio
    async def test_a_running_process_is_killed_and_waited_for(self):
        proc = _running_process()
        await reap_ffmpeg(proc)
        proc.kill.assert_called_once()
        proc.wait.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_finished_process_is_left_alone(self):
        proc = _running_process(returncode=0)
        await reap_ffmpeg(proc)
        proc.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_nothing_to_reap(self):
        await reap_ffmpeg(None)

    @pytest.mark.asyncio
    async def test_a_process_gone_meanwhile_is_fine(self):
        proc = _running_process()
        proc.kill.side_effect = ProcessLookupError
        await reap_ffmpeg(proc)

    @pytest.mark.asyncio
    async def test_one_that_will_not_exit_does_not_hang_the_caller(self):
        async def never():
            await asyncio.Event().wait()

        proc = _running_process(wait=never)
        await asyncio.wait_for(reap_ffmpeg(proc, timeout=0.05), 2)
        proc.kill.assert_called_once()


class TestEveryCaptureReapsItsFfmpeg:
    @pytest.mark.asyncio
    async def test_external_rtsp_capture(self):
        proc = _running_process()
        with (
            patch.object(external_camera, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch.object(external_camera, "_sanitize_camera_url", side_effect=lambda url, schemes: url),
            patch.object(external_camera, "read_ffmpeg_log_until_open", AsyncMock(side_effect=RuntimeError("boom"))),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            # Handled one level up, as before; what matters is the process.
            pytest.raises(RuntimeError),
        ):
            await external_camera._capture_rtsp_frame("rtsp://camera.example/stream", 5)

        proc.kill.assert_called_once()
        proc.wait.assert_awaited()

    @pytest.mark.asyncio
    async def test_external_rtsp_capture_cancelled(self):
        async def hang(_process):
            await asyncio.Event().wait()

        proc = _running_process()
        with (
            patch.object(external_camera, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch.object(external_camera, "_sanitize_camera_url", side_effect=lambda url, schemes: url),
            patch.object(external_camera, "read_ffmpeg_log_until_open", hang),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        ):
            task = asyncio.create_task(external_camera._capture_rtsp_frame("rtsp://camera.example/stream", 5))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_usb_capture(self):
        proc = _running_process()
        with (
            patch.object(external_camera, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch.object(external_camera, "_safe_usb_device_path", side_effect=lambda device: device),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(RuntimeError),
        ):
            await external_camera._capture_usb_frame("/dev/video0", 5)

        proc.kill.assert_called_once()
        proc.wait.assert_awaited()

    @pytest.mark.asyncio
    async def test_printer_rtsp_capture(self):
        proc = _running_process()
        with (
            patch.object(camera, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch.object(camera, "create_tls_proxy", AsyncMock(return_value=(40000, None))),
            patch.object(camera, "close_tls_proxy", AsyncMock()),
            patch.object(camera, "read_ffmpeg_log_until_open", AsyncMock(side_effect=RuntimeError("boom"))),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        ):
            assert await camera._capture_camera_frame_bytes_uncoalesced("192.0.2.10", "code", "X1C", 5) is None

        proc.kill.assert_called_once()
        proc.wait.assert_awaited()

    @pytest.mark.asyncio
    async def test_last_frame_of_a_video(self, tmp_path):
        (tmp_path / "in.mp4").write_bytes(b"video")
        proc = _running_process()
        with (
            patch.object(camera, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(RuntimeError),
        ):
            await camera.extract_video_last_frame(Path(tmp_path / "in.mp4"), Path(tmp_path / "out.jpg"))

        proc.kill.assert_called_once()
        proc.wait.assert_awaited()


@pytest.mark.asyncio
async def test_an_rtsp_capture_whose_ffmpeg_cannot_start_returns_none():
    """Nothing to reap, and the finally must not trip over the missing process."""
    with (
        patch.object(external_camera, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
        patch.object(external_camera, "_sanitize_camera_url", side_effect=lambda url, schemes: url),
        patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=PermissionError("not executable"))),
    ):
        assert await external_camera._capture_rtsp_frame("rtsp://camera.example/stream", 5) is None


@pytest.mark.asyncio
async def test_a_killed_process_whose_output_lingers_is_not_called_still_running(caplog):
    """wait() also waits for the pipes; a dead child can still keep it waiting."""

    async def pipes_open():
        proc.returncode = -9
        await asyncio.Event().wait()

    proc = _running_process()
    proc.wait = pipes_open
    await reap_ffmpeg(proc, timeout=0.05)
    assert "did not exit" not in caplog.text
    assert "output did not close" in caplog.text
