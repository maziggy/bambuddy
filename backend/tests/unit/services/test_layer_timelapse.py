"""
Tests for the layer timelapse service.

These tests cover session management and pure logic functions.
"""

import time
from datetime import datetime
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest


class TestTimelapseSessionManagement:
    """Tests for timelapse session lifecycle."""

    def test_start_session_creates_new_session(self):
        """Verify start_session creates and registers a new session."""
        from backend.app.services.layer_timelapse import (
            _active_sessions,
            cancel_session,
            get_session,
            start_session,
        )

        # Clear any existing sessions
        _active_sessions.clear()

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = Path("/tmp/test_bambuddy")

            session = start_session(
                printer_id=1,
                archive_id=100,
                url="http://camera.local/mjpeg",
                cam_type="mjpeg",
            )

            assert session is not None
            assert session.printer_id == 1
            assert session.archive_id == 100
            assert session.camera_url == "http://camera.local/mjpeg"
            assert session.camera_type == "mjpeg"
            assert session.last_layer == -1
            assert session.frame_count == 0

            # Session should be retrievable
            retrieved = get_session(1)
            assert retrieved is session

            # Cleanup
            cancel_session(1)

    def test_start_session_cancels_existing(self):
        """Verify starting a new session cancels any existing session."""
        from backend.app.services.layer_timelapse import (
            _active_sessions,
            cancel_session,
            get_session,
            start_session,
        )

        _active_sessions.clear()

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = Path("/tmp/test_bambuddy")

            # Start first session
            session1 = start_session(1, 100, "http://cam1/", "mjpeg")

            # Mock cleanup to track if it was called
            session1.cleanup = MagicMock()

            # Start second session for same printer
            session2 = start_session(1, 101, "http://cam2/", "rtsp")

            # First session should be replaced
            current = get_session(1)
            assert current is session2
            assert current.archive_id == 101  # Verify it's the new session
            assert current.camera_url == "http://cam2/"

            # First session's cleanup should have been called
            session1.cleanup.assert_called_once()

            # Cleanup
            cancel_session(1)

    def test_get_session_returns_none_for_unknown(self):
        """Verify get_session returns None for unknown printer."""
        from backend.app.services.layer_timelapse import _active_sessions, get_session

        _active_sessions.clear()

        result = get_session(999)
        assert result is None

    def test_cancel_session_removes_and_cleans_up(self):
        """Verify cancel_session removes session and cleans up."""
        from backend.app.services.layer_timelapse import (
            _active_sessions,
            cancel_session,
            get_session,
            start_session,
        )

        _active_sessions.clear()

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = Path("/tmp/test_bambuddy")

            session = start_session(1, 100, "http://cam/", "mjpeg")

            # Mock cleanup to avoid filesystem operations
            session.cleanup = MagicMock()

            cancel_session(1)

            # Session should be removed
            assert get_session(1) is None
            # Cleanup should have been called
            session.cleanup.assert_called_once()

    def test_cancel_nonexistent_session_is_safe(self):
        """Verify canceling a non-existent session doesn't error."""
        from backend.app.services.layer_timelapse import _active_sessions, cancel_session

        _active_sessions.clear()

        # Should not raise
        cancel_session(999)


class TestTimelapseSession:
    """Tests for TimelapseSession class."""

    def test_session_id_format(self):
        """Verify session ID follows expected datetime format."""
        from backend.app.services.layer_timelapse import TimelapseSession, _active_sessions

        _active_sessions.clear()

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = Path("/tmp/test_bambuddy")

            session = TimelapseSession(
                printer_id=1,
                archive_id=100,
                camera_url="http://test/",
                camera_type="mjpeg",
            )

            # Session ID should be timestamp format YYYYMMDD_HHMMSS
            assert len(session.session_id) == 15
            assert session.session_id[8] == "_"

            # Should be parseable as datetime
            try:
                datetime.strptime(session.session_id, "%Y%m%d_%H%M%S")
            except ValueError:
                pytest.fail("Session ID is not valid datetime format")

    def test_frames_dir_path_structure(self):
        """Verify frames directory path is structured correctly."""
        from backend.app.services.layer_timelapse import TimelapseSession

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = Path("/data/bambuddy")

            with patch.object(Path, "mkdir"):  # Avoid creating real directories
                session = TimelapseSession(
                    printer_id=42,
                    archive_id=100,
                    camera_url="http://test/",
                    camera_type="mjpeg",
                )

                expected_path = Path("/data/bambuddy/timelapse_frames/42") / session.session_id
                assert session.frames_dir == expected_path


class TestLayerChangeLogic:
    """Tests for layer change capture logic."""

    @pytest.mark.asyncio
    async def test_capture_layer_only_on_increase(self):
        """Verify frames are only captured when layer increases."""
        from backend.app.services.layer_timelapse import TimelapseSession

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = Path("/tmp/test")

            with patch.object(Path, "mkdir"):
                session = TimelapseSession(1, 100, "http://test/", "mjpeg")

                # Mock capture_frame to return data
                with patch(
                    "backend.app.services.layer_timelapse.capture_frame", new_callable=AsyncMock
                ) as mock_capture:
                    mock_capture.return_value = b"\xff\xd8test\xff\xd9"

                    with patch.object(Path, "write_bytes"):
                        # First layer should capture
                        result = await session.capture_layer(1)
                        assert result is True
                        assert session.last_layer == 1
                        assert session.frame_count == 1

                        # Same layer should NOT capture
                        result = await session.capture_layer(1)
                        assert result is False
                        assert session.frame_count == 1

                        # Lower layer should NOT capture
                        result = await session.capture_layer(0)
                        assert result is False
                        assert session.frame_count == 1

                        # Higher layer should capture
                        result = await session.capture_layer(5)
                        assert result is True
                        assert session.last_layer == 5
                        assert session.frame_count == 2

    @pytest.mark.asyncio
    async def test_capture_layer_handles_failed_capture(self):
        """Verify failed capture returns False but updates layer."""
        from backend.app.services.layer_timelapse import TimelapseSession

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = Path("/tmp/test")

            with patch.object(Path, "mkdir"):
                session = TimelapseSession(1, 100, "http://test/", "mjpeg")

                # Mock capture_frame to return None (failure)
                with patch(
                    "backend.app.services.layer_timelapse.capture_frame", new_callable=AsyncMock
                ) as mock_capture:
                    mock_capture.return_value = None

                    result = await session.capture_layer(1)

                    assert result is False
                    assert session.last_layer == 1  # Layer is still updated
                    assert session.frame_count == 0  # But frame count not incremented


class TestCaptureLayerAppliesRotation:
    """camera_rotation was previously only wired into the notification-
    snapshot path, so a layer-timelapse video came out upside-down whenever
    the printer had a rotation configured. capture_layer now applies it to
    every captured frame, whether fresh or reused from the live view's
    buffer, before writing to disk."""

    @pytest.mark.asyncio
    async def test_rotates_fresh_capture_when_configured(self, tmp_path):
        from backend.app.services.layer_timelapse import TimelapseSession

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path

            with patch.object(Path, "mkdir"):
                session = TimelapseSession(1, 100, "/dev/video1", "usb", rotation=180)

                with (
                    patch("backend.app.api.routes.camera.live_frame_for_capture", return_value=(False, None)),
                    patch(
                        "backend.app.services.layer_timelapse.capture_frame",
                        new_callable=AsyncMock,
                        return_value=b"\xff\xd8unrotated\xff\xd9",
                    ),
                    patch(
                        "backend.app.services.layer_timelapse.apply_camera_rotation",
                        return_value=b"\xff\xd8rotated\xff\xd9",
                    ) as mock_rotate,
                    patch.object(Path, "write_bytes") as mock_write,
                ):
                    result = await session.capture_layer(1)

        assert result is True
        mock_rotate.assert_called_once_with(b"\xff\xd8unrotated\xff\xd9", 180, ANY)
        mock_write.assert_called_once_with(b"\xff\xd8rotated\xff\xd9")

    @pytest.mark.asyncio
    async def test_rotates_buffered_frame_when_configured(self, tmp_path):
        from backend.app.services.layer_timelapse import TimelapseSession

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path

            with patch.object(Path, "mkdir"):
                session = TimelapseSession(1, 100, "/dev/video1", "usb", rotation=90)

                with (
                    patch(
                        "backend.app.api.routes.camera.live_frame_for_capture",
                        return_value=(True, b"\xff\xd8buffered\xff\xd9"),
                    ),
                    patch(
                        "backend.app.services.layer_timelapse.apply_camera_rotation",
                        return_value=b"\xff\xd8rotated\xff\xd9",
                    ) as mock_rotate,
                    patch.object(Path, "write_bytes") as mock_write,
                ):
                    result = await session.capture_layer(1)

        assert result is True
        mock_rotate.assert_called_once_with(b"\xff\xd8buffered\xff\xd9", 90, ANY)
        mock_write.assert_called_once_with(b"\xff\xd8rotated\xff\xd9")

    @pytest.mark.asyncio
    async def test_skips_rotation_when_not_configured(self, tmp_path):
        """Default rotation=0 - no-op, and must not even call apply_camera_rotation
        (avoids the PIL decode/re-encode round trip for the common case)."""
        from backend.app.services.layer_timelapse import TimelapseSession

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path

            with patch.object(Path, "mkdir"):
                session = TimelapseSession(1, 100, "/dev/video1", "usb")
                assert session.rotation == 0

                with (
                    patch("backend.app.api.routes.camera.live_frame_for_capture", return_value=(False, None)),
                    patch(
                        "backend.app.services.layer_timelapse.capture_frame",
                        new_callable=AsyncMock,
                        return_value=b"\xff\xd8unrotated\xff\xd9",
                    ),
                    patch("backend.app.services.layer_timelapse.apply_camera_rotation") as mock_rotate,
                    patch.object(Path, "write_bytes") as mock_write,
                ):
                    result = await session.capture_layer(1)

        assert result is True
        mock_rotate.assert_not_called()
        mock_write.assert_called_once_with(b"\xff\xd8unrotated\xff\xd9")


class TestOnLayerChange:
    """Tests for the on_layer_change callback."""

    @pytest.mark.asyncio
    async def test_on_layer_change_captures_when_session_exists(self):
        """Verify on_layer_change triggers capture when session exists."""
        from backend.app.services.layer_timelapse import (
            _active_sessions,
            cancel_session,
            on_layer_change,
            start_session,
        )

        _active_sessions.clear()

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = Path("/tmp/test")

            with patch.object(Path, "mkdir"):
                session = start_session(1, 100, "http://test/", "mjpeg")

                with patch.object(session, "capture_layer", new_callable=AsyncMock) as mock_capture:
                    mock_capture.return_value = True

                    await on_layer_change(1, 5)

                    mock_capture.assert_called_once_with(5)

                cancel_session(1)

    @pytest.mark.asyncio
    async def test_on_layer_change_does_nothing_without_session(self):
        """Verify on_layer_change is safe when no session exists."""
        from backend.app.services.layer_timelapse import _active_sessions, on_layer_change

        _active_sessions.clear()

        # Should not raise
        await on_layer_change(999, 10)


class TestGetActiveSessions:
    """Tests for get_active_sessions."""

    def test_get_active_sessions_returns_copy(self):
        """Verify get_active_sessions returns a copy, not the original dict."""
        from backend.app.services.layer_timelapse import (
            _active_sessions,
            cancel_session,
            get_active_sessions,
            start_session,
        )

        _active_sessions.clear()

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = Path("/tmp/test")

            with patch.object(Path, "mkdir"):
                start_session(1, 100, "http://test/", "mjpeg")

                sessions = get_active_sessions()

                # Should be a copy
                assert sessions is not _active_sessions
                assert 1 in sessions

                # Modifying copy shouldn't affect original
                sessions.clear()
                assert 1 in _active_sessions

                cancel_session(1)


class TestCleanupOrphanedTimelapseSessions:
    """_active_sessions is in-memory only, so a process restart mid-print
    loses track of an active session without ever cleaning up its frames
    directory (or a stitched-but-not-attached output .mp4). Confirmed live:
    38MB of exactly this leftover on Carl's OrangePi after several restarts
    during testing. cleanup_orphaned_timelapse_sessions() sweeps for it."""

    def _touch_old(self, path, age_seconds=600):
        import os

        path.touch()
        old = time.time() - age_seconds
        os.utime(path, (old, old))

    def _mkdir_old(self, path, age_seconds=600):
        import os

        path.mkdir(parents=True)
        old = time.time() - age_seconds
        os.utime(path, (old, old))

    def test_removes_orphaned_frame_dir_and_stray_output(self, tmp_path):
        from backend.app.services.layer_timelapse import (
            _active_sessions,
            cleanup_orphaned_timelapse_sessions,
        )

        _active_sessions.clear()
        printer_dir = tmp_path / "timelapse_frames" / "1"
        self._mkdir_old(printer_dir / "20260101_000000")
        self._touch_old(printer_dir / "timelapse_20260101_000000.mp4")

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path
            removed = cleanup_orphaned_timelapse_sessions(min_age_seconds=300)

        assert removed == 2
        assert not (printer_dir / "20260101_000000").exists()
        assert not (printer_dir / "timelapse_20260101_000000.mp4").exists()

    def test_spares_the_currently_active_session(self, tmp_path):
        from backend.app.services.layer_timelapse import (
            TimelapseSession,
            _active_sessions,
            cleanup_orphaned_timelapse_sessions,
        )

        _active_sessions.clear()

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path
            session = TimelapseSession(1, 100, "/dev/video1", "usb")
            _active_sessions[1] = session
            import os

            old = time.time() - 600
            os.utime(session.frames_dir, (old, old))

            removed = cleanup_orphaned_timelapse_sessions(min_age_seconds=300)

        assert removed == 0
        assert session.frames_dir.exists()
        _active_sessions.clear()

    def test_spares_recently_modified_entries(self, tmp_path):
        """Defensive margin: something modified within min_age_seconds is
        left alone even if it doesn't match an active session, in case this
        is ever invoked while a session is mid-creation."""
        from backend.app.services.layer_timelapse import (
            _active_sessions,
            cleanup_orphaned_timelapse_sessions,
        )

        _active_sessions.clear()
        printer_dir = tmp_path / "timelapse_frames" / "1"
        printer_dir.mkdir(parents=True)
        (printer_dir / "20260101_000000").mkdir()

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path
            removed = cleanup_orphaned_timelapse_sessions(min_age_seconds=300)

        assert removed == 0
        assert (printer_dir / "20260101_000000").exists()

    def test_no_base_dir_is_a_no_op(self, tmp_path):
        from backend.app.services.layer_timelapse import cleanup_orphaned_timelapse_sessions

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path / "does-not-exist"
            removed = cleanup_orphaned_timelapse_sessions()

        assert removed == 0

    def test_ignores_non_numeric_printer_dirs(self, tmp_path):
        """Defensive: unrelated directories under timelapse_frames/ (there
        shouldn't be any, but printer_id is parsed from the dir name) must
        not raise."""
        from backend.app.services.layer_timelapse import (
            _active_sessions,
            cleanup_orphaned_timelapse_sessions,
        )

        _active_sessions.clear()
        (tmp_path / "timelapse_frames" / "not-a-printer-id").mkdir(parents=True)

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path
            removed = cleanup_orphaned_timelapse_sessions()

        assert removed == 0

    def test_spares_a_session_that_is_mid_stitch(self, tmp_path):
        """on_print_complete drops the session from _active_sessions before it
        hands frames_dir to ffmpeg, so for the length of a stitch (up to 300s)
        the directory matches no active session. Its mtime is the last layer's
        frame write, which on a tall print's final layer is easily older than
        the age margin — and the margin's default IS the stitch timeout, so it
        offers no headroom here. _finalizing_sessions covers that window."""
        import os

        from backend.app.services.layer_timelapse import (
            TimelapseSession,
            _active_sessions,
            _finalizing_sessions,
            cleanup_orphaned_timelapse_sessions,
        )

        _active_sessions.clear()
        _finalizing_sessions.clear()

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path
            session = TimelapseSession(1, 100, "/dev/video1", "usb")
            (session.frames_dir / "layer_00001.jpg").write_bytes(b"x")
            old = time.time() - 600
            os.utime(session.frames_dir, (old, old))

            # Exactly the state on_print_complete is in while ffmpeg runs.
            _active_sessions.pop(1, None)
            _finalizing_sessions[1] = session.session_id

            removed = cleanup_orphaned_timelapse_sessions(min_age_seconds=300)

        assert removed == 0
        assert session.frames_dir.exists(), "ffmpeg's input was deleted mid-stitch"
        _finalizing_sessions.clear()

    @pytest.mark.asyncio
    async def test_on_print_complete_clears_the_finalizing_marker(self, tmp_path):
        """Including when the stitch fails — a leaked marker would make the
        sweep skip that printer's leftovers forever."""
        from backend.app.services.layer_timelapse import (
            TimelapseSession,
            _active_sessions,
            _finalizing_sessions,
            on_print_complete,
        )

        _active_sessions.clear()
        _finalizing_sessions.clear()

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path
            session = TimelapseSession(1, 100, "/dev/video1", "usb")
            session.frame_count = 3
            _active_sessions[1] = session

            with patch.object(TimelapseSession, "stitch", AsyncMock(side_effect=RuntimeError("ffmpeg died"))):
                result = await on_print_complete(1)

        assert result is None
        assert 1 not in _finalizing_sessions

    def test_leaves_unrelated_files_alone(self, tmp_path):
        """Only this module's own artifacts are swept. A file that is neither a
        session directory nor timelapse_<id>.mp4 was put there by something
        else, and age is not a reason to delete it."""
        from backend.app.services.layer_timelapse import (
            _active_sessions,
            cleanup_orphaned_timelapse_sessions,
        )

        _active_sessions.clear()
        printer_dir = tmp_path / "timelapse_frames" / "1"
        printer_dir.mkdir(parents=True)
        stranger = printer_dir / "notes.txt"
        self._touch_old(stranger)
        self._touch_old(printer_dir / "timelapse_20260101_000000.mp4")

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path
            removed = cleanup_orphaned_timelapse_sessions(min_age_seconds=300)

        assert removed == 1
        assert stranger.exists()
        assert not (printer_dir / "timelapse_20260101_000000.mp4").exists()

    def test_a_removal_that_fails_is_not_counted_as_removed(self, tmp_path):
        """The count and the log line are the only evidence an operator has of
        what was deleted, so a failed rmtree must not be reported as a success.

        The stub honours rmtree's real contract — ignore_errors=True swallows
        the failure and returns normally — because that is the whole point: a
        caller passing it gets a silent no-op that the surrounding
        ``except OSError`` can never see, and would still count and log the
        directory as removed. A stub that raised unconditionally would pass
        either way and prove nothing.
        """
        from backend.app.services.layer_timelapse import (
            _active_sessions,
            cleanup_orphaned_timelapse_sessions,
        )

        _active_sessions.clear()
        printer_dir = tmp_path / "timelapse_frames" / "1"
        self._mkdir_old(printer_dir / "20260101_000000")

        def rmtree_on_read_only_fs(path, ignore_errors=False, **kwargs):
            if ignore_errors:
                return  # silently does nothing, exactly like the real thing
            raise OSError("read-only fs")

        with patch("backend.app.services.layer_timelapse.settings") as mock_settings:
            mock_settings.base_dir = tmp_path
            with patch("backend.app.services.layer_timelapse.shutil.rmtree", rmtree_on_read_only_fs):
                removed = cleanup_orphaned_timelapse_sessions(min_age_seconds=300)

        assert removed == 0, "a directory that is still on disk was reported as removed"
        assert (printer_dir / "20260101_000000").exists()
