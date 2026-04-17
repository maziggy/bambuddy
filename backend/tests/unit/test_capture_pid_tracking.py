"""Tests for capture PID tracking and cleanup exclusion (#172).

The Obico detection service spawns short-lived ffmpeg processes for snapshot
capture via capture_camera_frame_bytes(). These must be registered in
_active_capture_pids so the cleanup task in routes/camera.py does not kill
them as orphaned.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.camera import (
    _active_capture_pids,
    capture_camera_frame_bytes,
)


@pytest.fixture(autouse=True)
def _clear_capture_pids():
    """Ensure _active_capture_pids is empty before/after each test."""
    _active_capture_pids.clear()
    yield
    _active_capture_pids.clear()


class TestCapturePidRegistration:
    """Verify PIDs are added/removed from _active_capture_pids."""

    @pytest.mark.asyncio
    async def test_pid_registered_during_capture(self):
        """PID is in _active_capture_pids while ffmpeg is running."""
        observed_pids_during_run: set[int] = set()

        fake_process = MagicMock()
        fake_process.pid = 99999
        fake_process.returncode = 0

        async def fake_communicate():
            # Snapshot what's in the set while "ffmpeg is running"
            observed_pids_during_run.update(_active_capture_pids)
            return (b"\xff\xd8" + b"\x00" * 200 + b"\xff\xd9", b"")

        fake_process.communicate = fake_communicate

        fake_proxy_server = AsyncMock()
        fake_proxy_server.close = MagicMock()

        with (
            patch("backend.app.services.camera.is_chamber_image_model", return_value=False),
            patch("backend.app.services.camera.get_camera_port", return_value=322),
            patch("backend.app.services.camera.create_tls_proxy", return_value=(12345, fake_proxy_server)),
            patch("backend.app.services.camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", return_value=fake_process),
        ):
            result = await capture_camera_frame_bytes("192.168.1.1", "test", "P2S", timeout=10)

        # PID was registered during capture
        assert 99999 in observed_pids_during_run
        # PID is removed after capture completes
        assert 99999 not in _active_capture_pids
        # Capture returned data
        assert result is not None

    @pytest.mark.asyncio
    async def test_pid_removed_after_failure(self):
        """PID is cleaned up even when ffmpeg returns non-zero."""
        fake_process = MagicMock()
        fake_process.pid = 88888
        fake_process.returncode = 1

        async def fake_communicate():
            return (b"", b"some error")

        fake_process.communicate = fake_communicate

        fake_proxy_server = AsyncMock()
        fake_proxy_server.close = MagicMock()

        with (
            patch("backend.app.services.camera.is_chamber_image_model", return_value=False),
            patch("backend.app.services.camera.get_camera_port", return_value=322),
            patch("backend.app.services.camera.create_tls_proxy", return_value=(12345, fake_proxy_server)),
            patch("backend.app.services.camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", return_value=fake_process),
        ):
            result = await capture_camera_frame_bytes("192.168.1.1", "test", "P2S", timeout=10)

        assert result is None
        assert 88888 not in _active_capture_pids

    @pytest.mark.asyncio
    async def test_pid_removed_after_timeout(self):
        """PID is cleaned up when ffmpeg times out."""
        fake_process = MagicMock()
        fake_process.pid = 77777
        fake_process.returncode = None
        fake_process.kill = MagicMock()

        async def fake_communicate():
            await asyncio.sleep(60)  # Will be cancelled by wait_for
            return (b"", b"")

        fake_process.communicate = fake_communicate

        async def fake_wait():
            fake_process.returncode = -9

        fake_process.wait = fake_wait

        fake_proxy_server = AsyncMock()
        fake_proxy_server.close = MagicMock()

        with (
            patch("backend.app.services.camera.is_chamber_image_model", return_value=False),
            patch("backend.app.services.camera.get_camera_port", return_value=322),
            patch("backend.app.services.camera.create_tls_proxy", return_value=(12345, fake_proxy_server)),
            patch("backend.app.services.camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", return_value=fake_process),
        ):
            result = await capture_camera_frame_bytes("192.168.1.1", "test", "P2S", timeout=0.01)

        assert result is None
        assert 77777 not in _active_capture_pids

    @pytest.mark.asyncio
    async def test_no_pid_tracked_for_chamber_image_models(self):
        """Chamber image models (A1/P1) don't spawn ffmpeg — no PID tracking."""
        with (
            patch("backend.app.services.camera.is_chamber_image_model", return_value=True),
            patch("backend.app.services.camera.read_chamber_image_frame", return_value=b"\xff\xd8test\xff\xd9"),
        ):
            result = await capture_camera_frame_bytes("192.168.1.1", "test", "A1", timeout=10)

        assert result is not None
        assert len(_active_capture_pids) == 0

    @pytest.mark.asyncio
    async def test_no_pid_tracked_when_subprocess_fails(self):
        """If create_subprocess_exec raises, process is None — no PID to track."""
        fake_proxy_server = AsyncMock()
        fake_proxy_server.close = MagicMock()

        with (
            patch("backend.app.services.camera.is_chamber_image_model", return_value=False),
            patch("backend.app.services.camera.get_camera_port", return_value=322),
            patch("backend.app.services.camera.create_tls_proxy", return_value=(12345, fake_proxy_server)),
            patch("backend.app.services.camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("ffmpeg")),
        ):
            result = await capture_camera_frame_bytes("192.168.1.1", "test", "P2S", timeout=10)

        assert result is None
        assert len(_active_capture_pids) == 0


class TestCleanupExcludesCapturePids:
    """Verify cleanup_orphaned_streams skips PIDs in _active_capture_pids."""

    @pytest.mark.asyncio
    async def test_cleanup_skips_capture_pids(self):
        """A PID in _active_capture_pids must not be killed by cleanup."""
        from backend.app.api.routes.camera import cleanup_orphaned_streams

        _active_capture_pids.add(42000)

        with (
            patch("backend.app.api.routes.camera._scan_bambu_ffmpeg_pids", return_value=[42000]),
            patch("backend.app.api.routes.camera._active_streams", {}),
            patch("backend.app.api.routes.camera._spawned_ffmpeg_pids", {}),
            patch("os.kill") as mock_kill,
        ):
            await cleanup_orphaned_streams()

        # os.kill should NOT have been called with SIGKILL for our capture PID
        for call in mock_kill.call_args_list:
            pid, sig = call[0]
            assert pid != 42000, "cleanup killed an active capture PID"

    @pytest.mark.asyncio
    async def test_cleanup_kills_non_capture_pids(self):
        """PIDs NOT in _active_capture_pids should still be killed."""
        import signal

        from backend.app.api.routes.camera import cleanup_orphaned_streams

        # 42000 is a capture PID, 43000 is truly orphaned
        _active_capture_pids.add(42000)

        with (
            patch("backend.app.api.routes.camera._scan_bambu_ffmpeg_pids", return_value=[42000, 43000]),
            patch("backend.app.api.routes.camera._active_streams", {}),
            patch("backend.app.api.routes.camera._spawned_ffmpeg_pids", {}),
            patch("os.kill") as mock_kill,
        ):
            await cleanup_orphaned_streams()

        # 43000 should have been killed
        mock_kill.assert_any_call(43000, signal.SIGKILL)

        # 42000 should NOT have been killed with SIGKILL
        killed_pids = [call[0][0] for call in mock_kill.call_args_list if call[0][1] == signal.SIGKILL]
        assert 42000 not in killed_pids
