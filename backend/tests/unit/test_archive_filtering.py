"""
Unit tests for archive filtering and timelapse snapshot-diff logic.

Tests:
1. Calibration print filtering — /usr/ prefix skips archive creation
2. Timelapse snapshot-diff — _list_timelapse_videos and _scan_for_timelapse_with_retries
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Patch paths for lazy imports inside functions
_FTP_MODULE = "backend.app.services.bambu_ftp"


class TestCalibrationPrintFiltering:
    """Test that internal printer files under /usr/ are not archived."""

    @pytest.mark.asyncio
    async def test_usr_prefix_skips_archive(self, capture_logs):
        """Calibration gcode (/usr/etc/print/auto_cali_for_user.gcode) should skip archiving."""
        with (
            patch("backend.app.main.async_session") as mock_session_maker,
            patch("backend.app.main.notification_service") as mock_notif,
            patch("backend.app.main.smart_plug_manager") as mock_plug,
            patch("backend.app.main.ws_manager") as mock_ws,
            patch("backend.app.main.printer_manager") as mock_pm,
            patch("backend.app.main.mqtt_relay") as mock_relay,
        ):
            mock_notif.on_print_start = AsyncMock()
            mock_plug.on_print_start = AsyncMock()
            mock_ws.send_print_start = AsyncMock()
            mock_relay.on_print_start = AsyncMock()
            mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))

            # Mock printer with auto_archive enabled
            mock_printer = MagicMock()
            mock_printer.auto_archive = True
            mock_printer.id = 1

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session.execute = AsyncMock(
                return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_printer))
            )
            mock_session_maker.return_value = mock_session

            # Mock _send_print_start_notification
            with patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock) as mock_notif_send:
                from backend.app.main import on_print_start

                await on_print_start(
                    1,
                    {
                        "filename": "/usr/etc/print/auto_cali_for_user.gcode",
                        "subtask_name": "auto_cali_for_user",
                    },
                )

                # Notification should still be sent
                mock_notif_send.assert_called_once()

        # Verify the skip was logged
        info_messages = [r.message for r in capture_logs.records if r.levelno >= 20]
        skip_msgs = [m for m in info_messages if "internal printer file" in str(m)]
        assert skip_msgs, "Should log that internal printer file was skipped"

    @pytest.mark.asyncio
    async def test_usr_prefix_various_paths(self, capture_logs):
        """Various /usr/ paths should all be skipped."""
        test_paths = [
            "/usr/etc/print/auto_cali_for_user.gcode",
            "/usr/etc/print/some_other_calibration.gcode",
            "/usr/bin/firmware_test.gcode",
        ]

        for path in test_paths:
            with (
                patch("backend.app.main.async_session") as mock_session_maker,
                patch("backend.app.main.notification_service") as mock_notif,
                patch("backend.app.main.smart_plug_manager") as mock_plug,
                patch("backend.app.main.ws_manager") as mock_ws,
                patch("backend.app.main.printer_manager") as mock_pm,
                patch("backend.app.main.mqtt_relay") as mock_relay,
                patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
            ):
                mock_notif.on_print_start = AsyncMock()
                mock_plug.on_print_start = AsyncMock()
                mock_ws.send_print_start = AsyncMock()
                mock_relay.on_print_start = AsyncMock()
                mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))

                mock_printer = MagicMock()
                mock_printer.auto_archive = True
                mock_printer.id = 1

                mock_session = AsyncMock()
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock()
                mock_session.execute = AsyncMock(
                    return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_printer))
                )
                mock_session_maker.return_value = mock_session

                from backend.app.main import on_print_start

                await on_print_start(1, {"filename": path, "subtask_name": "test"})

            skip_msgs = [r for r in capture_logs.records if "internal printer file" in str(r.message)]
            assert skip_msgs, f"Path {path} should be skipped"
            capture_logs.clear()

    @pytest.mark.asyncio
    async def test_normal_gcode_not_skipped(self, capture_logs):
        """User gcode files under /data/ should NOT be skipped."""
        with (
            patch("backend.app.main.async_session") as mock_session_maker,
            patch("backend.app.main.notification_service") as mock_notif,
            patch("backend.app.main.smart_plug_manager") as mock_plug,
            patch("backend.app.main.ws_manager") as mock_ws,
            patch("backend.app.main.printer_manager") as mock_pm,
            patch("backend.app.main.mqtt_relay") as mock_relay,
        ):
            mock_notif.on_print_start = AsyncMock()
            mock_plug.on_print_start = AsyncMock()
            mock_ws.send_print_start = AsyncMock()
            mock_relay.on_print_start = AsyncMock()
            mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))

            mock_printer = MagicMock()
            mock_printer.auto_archive = True
            mock_printer.id = 1

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session.execute = AsyncMock(
                return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_printer))
            )
            mock_session_maker.return_value = mock_session

            from backend.app.main import on_print_start

            await on_print_start(
                1,
                {
                    "filename": "/data/Metadata/benchy.gcode.3mf",
                    "subtask_name": "benchy",
                },
            )

        # Should NOT see "internal printer file" skip message
        skip_msgs = [r for r in capture_logs.records if "internal printer file" in str(r.message)]
        assert not skip_msgs, "User gcode should not be skipped"


class TestListTimelapseVideos:
    """Test the _list_timelapse_videos helper function."""

    @pytest.mark.asyncio
    async def test_finds_video_files_in_timelapse_dir(self):
        """Should return MP4 and AVI files found in /timelapse directory."""
        mock_printer = MagicMock()
        mock_printer.ip_address = "192.168.1.100"
        mock_printer.access_code = "12345678"
        mock_printer.model = "X1C"

        mock_files = [
            {"name": "video1.mp4", "is_directory": False, "size": 1000, "path": "/timelapse/video1.mp4"},
            {"name": "video2.mp4", "is_directory": False, "size": 2000, "path": "/timelapse/video2.mp4"},
            {"name": "thumbs", "is_directory": True, "size": 0, "path": "/timelapse/thumbs"},
            {"name": "video3.avi", "is_directory": False, "size": 500, "path": "/timelapse/video3.avi"},
        ]

        with patch(f"{_FTP_MODULE}.list_files_async", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = mock_files

            from backend.app.main import _list_timelapse_videos

            videos, path = await _list_timelapse_videos(mock_printer)

        assert len(videos) == 3
        assert path == "/timelapse"
        assert all(f["name"].endswith((".mp4", ".avi")) for f in videos)

    @pytest.mark.asyncio
    async def test_tries_multiple_directories(self):
        """Should try /timelapse, /timelapse/video, /record, /recording."""
        mock_printer = MagicMock()
        mock_printer.ip_address = "192.168.1.100"
        mock_printer.access_code = "12345678"
        mock_printer.model = "H2D"

        async def mock_list_files(ip, code, path, printer_model=None):
            if path == "/record":
                return [{"name": "clip.mp4", "is_directory": False, "size": 500, "path": "/record/clip.mp4"}]
            return []

        with patch(f"{_FTP_MODULE}.list_files_async", side_effect=mock_list_files):
            from backend.app.main import _list_timelapse_videos

            mp4s, path = await _list_timelapse_videos(mock_printer)

        assert len(mp4s) == 1
        assert path == "/record"
        assert mp4s[0]["name"] == "clip.mp4"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_files(self):
        """Should return ([], None) when no MP4 files exist."""
        mock_printer = MagicMock()
        mock_printer.ip_address = "192.168.1.100"
        mock_printer.access_code = "12345678"
        mock_printer.model = "X1C"

        with patch(f"{_FTP_MODULE}.list_files_async", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = []

            from backend.app.main import _list_timelapse_videos

            mp4s, path = await _list_timelapse_videos(mock_printer)

        assert mp4s == []
        assert path is None

    @pytest.mark.asyncio
    async def test_skips_directories(self):
        """Should filter out directory entries even if named .mp4."""
        mock_printer = MagicMock()
        mock_printer.ip_address = "192.168.1.100"
        mock_printer.access_code = "12345678"
        mock_printer.model = "X1C"

        mock_files = [
            {"name": "fake.mp4", "is_directory": True, "size": 0, "path": "/timelapse/fake.mp4"},
            {"name": "real.mp4", "is_directory": False, "size": 1000, "path": "/timelapse/real.mp4"},
        ]

        with patch(f"{_FTP_MODULE}.list_files_async", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = mock_files

            from backend.app.main import _list_timelapse_videos

            mp4s, path = await _list_timelapse_videos(mock_printer)

        assert len(mp4s) == 1
        assert mp4s[0]["name"] == "real.mp4"


class TestScanForTimelapseWithRetries:
    """Test the snapshot-diff timelapse scan logic."""

    def _make_mocks(self, archive_filename="benchy.gcode.3mf", timelapse_path=None):
        """Create standard mock archive and printer."""
        mock_archive = MagicMock()
        mock_archive.id = 1
        mock_archive.timelapse_path = timelapse_path
        mock_archive.printer_id = 1
        mock_archive.filename = archive_filename

        mock_printer = MagicMock()
        mock_printer.id = 1
        mock_printer.ip_address = "192.168.1.100"
        mock_printer.access_code = "12345678"
        mock_printer.model = "X1C"

        return mock_archive, mock_printer

    def _make_session_mock(self, mock_printer):
        """Create a mock async session that returns the given printer."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_printer))
        )
        return mock_session

    @pytest.mark.asyncio
    async def test_detects_new_file_after_baseline(self):
        """Should detect a file that wasn't in the baseline snapshot."""
        mock_archive, mock_printer = self._make_mocks()

        baseline_files = [
            {"name": "old_video.mp4", "is_directory": False, "size": 1000, "path": "/timelapse/old_video.mp4"},
        ]
        new_files = baseline_files + [
            {"name": "new_video.mp4", "is_directory": False, "size": 2000, "path": "/timelapse/new_video.mp4"},
        ]

        call_count = 0

        async def mock_list_mp4s(printer):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return baseline_files, "/timelapse"
            return new_files, "/timelapse"

        mock_service = MagicMock()
        mock_service.get_archive = AsyncMock(return_value=mock_archive)
        mock_service.attach_timelapse = AsyncMock(return_value=True)
        mock_session = self._make_session_mock(mock_printer)

        with (
            patch("backend.app.main.async_session", return_value=mock_session),
            patch("backend.app.main._list_timelapse_videos", side_effect=mock_list_mp4s),
            patch("backend.app.main.ws_manager") as mock_ws,
            patch("backend.app.main.asyncio.sleep", new_callable=AsyncMock),
            patch("backend.app.main.ArchiveService", return_value=mock_service),
            patch(f"{_FTP_MODULE}.download_file_bytes_async", new_callable=AsyncMock) as mock_download,
        ):
            mock_ws.send_archive_updated = AsyncMock()
            mock_download.return_value = b"fake video data"

            from backend.app.main import _scan_for_timelapse_with_retries

            await _scan_for_timelapse_with_retries(1)

        # Should have attached the NEW file, not the old one
        mock_service.attach_timelapse.assert_called_once()
        attached_filename = mock_service.attach_timelapse.call_args[0][2]
        assert attached_filename == "new_video.mp4", f"Expected new_video.mp4, got {attached_filename}"

    @pytest.mark.asyncio
    async def test_ignores_old_files_with_wrong_mtime(self):
        """Should not pick old files even if they'd sort first by mtime."""
        mock_archive, mock_printer = self._make_mocks()

        # Both old files exist at baseline — neither should be picked
        baseline_files = [
            {"name": "old_video1.mp4", "is_directory": False, "size": 1000, "path": "/timelapse/old_video1.mp4"},
            {"name": "old_video2.mp4", "is_directory": False, "size": 2000, "path": "/timelapse/old_video2.mp4"},
        ]

        # Always return same files — no new file ever appears
        async def mock_list_mp4s(printer):
            return baseline_files, "/timelapse"

        mock_service = MagicMock()
        mock_service.get_archive = AsyncMock(return_value=mock_archive)
        mock_service.attach_timelapse = AsyncMock(return_value=True)
        mock_session = self._make_session_mock(mock_printer)

        with (
            patch("backend.app.main.async_session", return_value=mock_session),
            patch("backend.app.main._list_timelapse_videos", side_effect=mock_list_mp4s),
            patch("backend.app.main.ws_manager") as mock_ws,
            patch("backend.app.main.asyncio.sleep", new_callable=AsyncMock),
            patch("backend.app.main.ArchiveService", return_value=mock_service),
            patch(f"{_FTP_MODULE}.download_file_bytes_async", new_callable=AsyncMock) as mock_download,
        ):
            mock_ws.send_archive_updated = AsyncMock()
            mock_download.return_value = b"fake video data"

            from backend.app.main import _scan_for_timelapse_with_retries

            await _scan_for_timelapse_with_retries(1)

        # "benchy" not in "old_video1.mp4" or "old_video2.mp4" — no match at all
        mock_service.attach_timelapse.assert_not_called()

    @pytest.mark.asyncio
    async def test_name_match_fallback(self):
        """When no new file appears, should fall back to name matching."""
        mock_archive, mock_printer = self._make_mocks()

        baseline_files = [
            {"name": "old_video.mp4", "is_directory": False, "size": 1000, "path": "/timelapse/old_video.mp4"},
            {
                "name": "benchy_20240101.mp4",
                "is_directory": False,
                "size": 2000,
                "path": "/timelapse/benchy_20240101.mp4",
            },
        ]

        async def mock_list_mp4s(printer):
            return baseline_files, "/timelapse"

        mock_service = MagicMock()
        mock_service.get_archive = AsyncMock(return_value=mock_archive)
        mock_service.attach_timelapse = AsyncMock(return_value=True)
        mock_session = self._make_session_mock(mock_printer)

        with (
            patch("backend.app.main.async_session", return_value=mock_session),
            patch("backend.app.main._list_timelapse_videos", side_effect=mock_list_mp4s),
            patch("backend.app.main.ws_manager") as mock_ws,
            patch("backend.app.main.asyncio.sleep", new_callable=AsyncMock),
            patch("backend.app.main.ArchiveService", return_value=mock_service),
            patch(f"{_FTP_MODULE}.download_file_bytes_async", new_callable=AsyncMock) as mock_download,
        ):
            mock_ws.send_archive_updated = AsyncMock()
            mock_download.return_value = b"fake video data"

            from backend.app.main import _scan_for_timelapse_with_retries

            await _scan_for_timelapse_with_retries(1)

        # Name-match fallback: "benchy" is in "benchy_20240101.mp4"
        mock_service.attach_timelapse.assert_called_once()
        attached_filename = mock_service.attach_timelapse.call_args[0][2]
        assert attached_filename == "benchy_20240101.mp4"

    @pytest.mark.asyncio
    async def test_stops_when_archive_already_has_timelapse(self):
        """Should stop immediately if archive already has a timelapse."""
        mock_archive, _ = self._make_mocks(timelapse_path="/some/existing/timelapse.mp4")

        mock_service = MagicMock()
        mock_service.get_archive = AsyncMock(return_value=mock_archive)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        with (
            patch("backend.app.main.async_session", return_value=mock_session),
            patch("backend.app.main._list_timelapse_videos", new_callable=AsyncMock) as mock_list,
            patch("backend.app.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("backend.app.main.ArchiveService", return_value=mock_service),
        ):
            from backend.app.main import _scan_for_timelapse_with_retries

            await _scan_for_timelapse_with_retries(1)

        # Should not have tried to list files or sleep
        mock_list.assert_not_called()
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_stops_when_archive_not_found(self):
        """Should stop immediately if archive doesn't exist."""
        mock_service = MagicMock()
        mock_service.get_archive = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        with (
            patch("backend.app.main.async_session", return_value=mock_session),
            patch("backend.app.main._list_timelapse_videos", new_callable=AsyncMock) as mock_list,
            patch("backend.app.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("backend.app.main.ArchiveService", return_value=mock_service),
        ):
            from backend.app.main import _scan_for_timelapse_with_retries

            await _scan_for_timelapse_with_retries(999)

        mock_list.assert_not_called()
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_retries_four_times(self):
        """Should retry with delays [5, 10, 20, 30]."""
        mock_archive, mock_printer = self._make_mocks(archive_filename="test.gcode.3mf")

        # Never find any files
        async def mock_list_mp4s(printer):
            return [], None

        mock_service = MagicMock()
        mock_service.get_archive = AsyncMock(return_value=mock_archive)
        mock_session = self._make_session_mock(mock_printer)

        with (
            patch("backend.app.main.async_session", return_value=mock_session),
            patch("backend.app.main._list_timelapse_videos", side_effect=mock_list_mp4s),
            patch("backend.app.main.ws_manager") as mock_ws,
            patch("backend.app.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("backend.app.main.ArchiveService", return_value=mock_service),
        ):
            mock_ws.send_archive_updated = AsyncMock()

            from backend.app.main import _scan_for_timelapse_with_retries

            await _scan_for_timelapse_with_retries(1)

        # Should have slept 4 times with delays [5, 10, 20, 30]
        assert mock_sleep.call_count == 4
        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_args == [5, 10, 20, 30]


class TestListTimelapseVideosAvi:
    """Test that _list_timelapse_videos finds AVI files (P1S format)."""

    @pytest.mark.asyncio
    async def test_finds_avi_files(self):
        """Should return AVI files alongside MP4 files."""
        mock_printer = MagicMock()
        mock_printer.ip_address = "192.168.1.100"
        mock_printer.access_code = "12345678"
        mock_printer.model = "P1S"

        mock_files = [
            {
                "name": "video_2026-02-17_10-00-00.avi",
                "is_directory": False,
                "size": 50000,
                "path": "/timelapse/video_2026-02-17_10-00-00.avi",
            },
        ]

        with patch(f"{_FTP_MODULE}.list_files_async", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = mock_files

            from backend.app.main import _list_timelapse_videos

            videos, path = await _list_timelapse_videos(mock_printer)

        assert len(videos) == 1
        assert videos[0]["name"].endswith(".avi")
        assert path == "/timelapse"

    @pytest.mark.asyncio
    async def test_finds_avi_case_insensitive(self):
        """Should match .AVI (uppercase) extensions."""
        mock_printer = MagicMock()
        mock_printer.ip_address = "192.168.1.100"
        mock_printer.access_code = "12345678"
        mock_printer.model = "P1S"

        mock_files = [
            {"name": "VIDEO.AVI", "is_directory": False, "size": 1000, "path": "/timelapse/VIDEO.AVI"},
        ]

        with patch(f"{_FTP_MODULE}.list_files_async", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = mock_files

            from backend.app.main import _list_timelapse_videos

            videos, path = await _list_timelapse_videos(mock_printer)

        assert len(videos) == 1

    @pytest.mark.asyncio
    async def test_scan_detects_new_avi_file(self):
        """Snapshot-diff should detect new AVI files just like MP4."""
        mock_archive = MagicMock()
        mock_archive.id = 1
        mock_archive.timelapse_path = None
        mock_archive.printer_id = 1
        mock_archive.filename = "benchy.gcode.3mf"

        mock_printer = MagicMock()
        mock_printer.id = 1
        mock_printer.ip_address = "192.168.1.100"
        mock_printer.access_code = "12345678"
        mock_printer.model = "P1S"

        baseline_files = []
        new_files = [
            {
                "name": "video_2026-02-17.avi",
                "is_directory": False,
                "size": 50000,
                "path": "/timelapse/video_2026-02-17.avi",
            },
        ]

        call_count = 0

        async def mock_list_videos(printer):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return baseline_files, "/timelapse"
            return new_files, "/timelapse"

        mock_service = MagicMock()
        mock_service.get_archive = AsyncMock(return_value=mock_archive)
        mock_service.attach_timelapse = AsyncMock(return_value=True)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_printer))
        )

        with (
            patch("backend.app.main.async_session", return_value=mock_session),
            patch("backend.app.main._list_timelapse_videos", side_effect=mock_list_videos),
            patch("backend.app.main.ws_manager") as mock_ws,
            patch("backend.app.main.asyncio.sleep", new_callable=AsyncMock),
            patch("backend.app.main.ArchiveService", return_value=mock_service),
            patch(f"{_FTP_MODULE}.download_file_bytes_async", new_callable=AsyncMock) as mock_download,
        ):
            mock_ws.send_archive_updated = AsyncMock()
            mock_download.return_value = b"fake avi data"

            from backend.app.main import _scan_for_timelapse_with_retries

            await _scan_for_timelapse_with_retries(1)

        mock_service.attach_timelapse.assert_called_once()
        attached_filename = mock_service.attach_timelapse.call_args[0][2]
        assert attached_filename == "video_2026-02-17.avi"


class TestConvertTimelapseToMp4:
    """Test the background AVI-to-MP4 conversion."""

    @pytest.mark.asyncio
    async def test_converts_avi_to_mp4(self, tmp_path):
        """Should call FFmpeg to convert and update the DB path."""
        source = tmp_path / "video.avi"
        source.write_bytes(b"fake avi")
        mp4_path = tmp_path / "video.mp4"

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        mock_process.returncode = 0

        mock_archive = MagicMock()
        mock_archive.id = 42
        mock_archive.timelapse_path = "archives/42/video.avi"

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_archive
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with (
            patch("backend.app.services.camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("backend.app.core.database.async_session", return_value=mock_session),
            patch("backend.app.services.archive.settings") as mock_settings,
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
        ):
            mock_settings.base_dir = tmp_path
            mock_exec.return_value = mock_process
            # Create the expected output file (as FFmpeg would)
            mp4_path.write_bytes(b"fake mp4 output")

            from backend.app.services.archive import _convert_timelapse_to_mp4

            await _convert_timelapse_to_mp4(42, source)

        # FFmpeg should have been called
        mock_exec.assert_called_once()
        cmd_args = mock_exec.call_args[0]
        assert "/usr/bin/ffmpeg" in cmd_args
        assert "-threads" in cmd_args
        assert "1" in cmd_args

        # DB should have been updated to .mp4 path
        mock_session.commit.assert_called_once()
        assert mock_archive.timelapse_path == "video.mp4"

    @pytest.mark.asyncio
    async def test_skips_when_no_ffmpeg(self, tmp_path):
        """Should log and return without converting when FFmpeg is unavailable."""
        source = tmp_path / "video.avi"
        source.write_bytes(b"fake avi")

        with patch("backend.app.services.camera.get_ffmpeg_path", return_value=None):
            from backend.app.services.archive import _convert_timelapse_to_mp4

            await _convert_timelapse_to_mp4(1, source)

        # Source file should still exist (not deleted)
        assert source.exists()

    @pytest.mark.asyncio
    async def test_cleans_up_on_ffmpeg_failure(self, tmp_path):
        """Should remove partial MP4 and keep source on conversion failure."""
        source = tmp_path / "video.avi"
        source.write_bytes(b"fake avi")
        mp4_path = tmp_path / "video.mp4"

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b"conversion error"))
        mock_process.returncode = 1

        with (
            patch("backend.app.services.camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
        ):
            mock_exec.return_value = mock_process
            # Simulate partial output file
            mp4_path.write_bytes(b"partial")

            from backend.app.services.archive import _convert_timelapse_to_mp4

            await _convert_timelapse_to_mp4(1, source)

        # Partial MP4 should be cleaned up
        assert not mp4_path.exists()
        # Source should still exist
        assert source.exists()


class TestAttachTimelapseBackgroundConversion:
    """Test that attach_timelapse spawns background conversion for non-MP4."""

    @pytest.mark.asyncio
    async def test_mp4_does_not_spawn_conversion(self, tmp_path):
        """MP4 files should not trigger background conversion."""
        from backend.app.services.archive import ArchiveService

        mock_archive = MagicMock()
        mock_archive.file_path = "archives/1/file.3mf"

        mock_db = AsyncMock()
        service = ArchiveService(mock_db)
        service.get_archive = AsyncMock(return_value=mock_archive)

        archive_dir = tmp_path / "archives" / "1"
        archive_dir.mkdir(parents=True)

        with (
            patch("backend.app.services.archive.settings") as mock_settings,
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_settings.base_dir = tmp_path

            result = await service.attach_timelapse(1, b"fake mp4 data", "video.mp4")

        assert result is True
        mock_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_avi_spawns_background_conversion(self, tmp_path):
        """AVI files should trigger background conversion task."""
        from backend.app.services.archive import ArchiveService

        mock_archive = MagicMock()
        mock_archive.file_path = "archives/1/file.3mf"

        mock_db = AsyncMock()
        service = ArchiveService(mock_db)
        service.get_archive = AsyncMock(return_value=mock_archive)

        archive_dir = tmp_path / "archives" / "1"
        archive_dir.mkdir(parents=True)

        with (
            patch("backend.app.services.archive.settings") as mock_settings,
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_settings.base_dir = tmp_path

            result = await service.attach_timelapse(1, b"fake avi data", "video.avi")

        assert result is True
        mock_create_task.assert_called_once()
        # Verify task name includes archive ID
        assert "timelapse-convert-1" in mock_create_task.call_args[1]["name"]
        # Close the unawaited coroutine to prevent GC warning
        mock_create_task.call_args[0][0].close()


class TestDeleteTimelapse:
    """Test DELETE /archives/{id}/timelapse endpoint."""

    @pytest.mark.asyncio
    async def test_delete_timelapse_removes_file_and_clears_db(self, tmp_path):
        """Deleting a timelapse should remove the file and clear the DB path."""
        from backend.app.api.routes.archives import delete_timelapse

        timelapse_dir = tmp_path / "archives" / "1"
        timelapse_dir.mkdir(parents=True)
        timelapse_file = timelapse_dir / "timelapse.mp4"
        timelapse_file.write_bytes(b"fake video data")

        mock_archive = MagicMock()
        mock_archive.timelapse_path = "archives/1/timelapse.mp4"

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_archive
        mock_db.execute.return_value = mock_result

        with patch("backend.app.api.routes.archives.settings") as mock_settings:
            mock_settings.base_dir = tmp_path
            result = await delete_timelapse(archive_id=1, db=mock_db)

        assert result == {"status": "deleted"}
        assert mock_archive.timelapse_path is None
        mock_db.commit.assert_awaited_once()
        assert not timelapse_file.exists()

    @pytest.mark.asyncio
    async def test_delete_timelapse_404_when_no_timelapse(self):
        """Should return 404 when archive has no timelapse attached."""
        from fastapi import HTTPException

        from backend.app.api.routes.archives import delete_timelapse

        mock_archive = MagicMock()
        mock_archive.timelapse_path = None

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_archive
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(HTTPException) as exc_info:
            await delete_timelapse(archive_id=1, db=mock_db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_timelapse_404_when_archive_not_found(self):
        """Should return 404 when archive doesn't exist."""
        from fastapi import HTTPException

        from backend.app.api.routes.archives import delete_timelapse

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(HTTPException) as exc_info:
            await delete_timelapse(archive_id=999, db=mock_db)

        assert exc_info.value.status_code == 404
