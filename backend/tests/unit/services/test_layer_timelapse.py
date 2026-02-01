"""
Tests for the layer timelapse service.

These tests cover session management and pure logic functions.
"""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
