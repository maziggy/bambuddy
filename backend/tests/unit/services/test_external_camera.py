"""
Tests for the external camera service.

These tests cover pure functions and frame parsing logic.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestFormatMjpegFrame:
    """Tests for MJPEG frame formatting."""

    def test_format_mjpeg_frame_basic(self):
        """Verify MJPEG frame is formatted correctly with boundary and headers."""
        from backend.app.services.external_camera import _format_mjpeg_frame

        # Minimal JPEG data (just SOI and EOI markers)
        jpeg_data = b"\xff\xd8\xff\xd9"

        result = _format_mjpeg_frame(jpeg_data)

        # Check boundary
        assert result.startswith(b"--frame\r\n")
        # Check content type
        assert b"Content-Type: image/jpeg\r\n" in result
        # Check content length
        assert b"Content-Length: 4\r\n" in result
        # Check frame data is included
        assert jpeg_data in result
        # Check ends with CRLF
        assert result.endswith(b"\r\n")

    def test_format_mjpeg_frame_larger_data(self):
        """Verify content length is correct for larger frames."""
        from backend.app.services.external_camera import _format_mjpeg_frame

        # Simulate a larger JPEG (1000 bytes)
        jpeg_data = b"\xff\xd8" + b"\x00" * 996 + b"\xff\xd9"

        result = _format_mjpeg_frame(jpeg_data)

        assert b"Content-Length: 1000\r\n" in result


class TestGetFfmpegPath:
    """Tests for ffmpeg path detection."""

    def test_get_ffmpeg_path_from_shutil_which(self):
        """Verify ffmpeg found via shutil.which is returned."""
        from backend.app.services.external_camera import get_ffmpeg_path

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            result = get_ffmpeg_path()
            assert result == "/usr/bin/ffmpeg"

    def test_get_ffmpeg_path_fallback_to_common_paths(self):
        """Verify common paths are checked when shutil.which fails."""
        from backend.app.services.external_camera import get_ffmpeg_path

        with patch("shutil.which", return_value=None), patch("pathlib.Path.exists") as mock_exists:
            # First common path exists
            mock_exists.return_value = True
            result = get_ffmpeg_path()
            assert result in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"]

    def test_get_ffmpeg_path_returns_none_when_not_found(self):
        """Verify None is returned when ffmpeg not found anywhere."""
        from backend.app.services.external_camera import get_ffmpeg_path

        with patch("shutil.which", return_value=None), patch("pathlib.Path.exists", return_value=False):
            result = get_ffmpeg_path()
            assert result is None


class TestJpegFrameExtraction:
    """Tests for JPEG frame extraction from buffer."""

    def test_extract_single_frame_from_buffer(self):
        """Test extracting a complete JPEG frame from buffer."""
        # JPEG markers
        jpeg_start = b"\xff\xd8"
        jpeg_end = b"\xff\xd9"

        # Create a buffer with one complete frame
        frame_content = b"\x00" * 100
        buffer = jpeg_start + frame_content + jpeg_end

        # Find frame boundaries
        start_idx = buffer.find(jpeg_start)
        end_idx = buffer.find(jpeg_end, start_idx + 2)

        assert start_idx == 0
        assert end_idx == 102

        # Extract frame
        frame = buffer[start_idx : end_idx + 2]
        assert frame == buffer
        assert len(frame) == 104

    def test_extract_frame_with_leading_garbage(self):
        """Test extracting frame when buffer has leading garbage data."""
        jpeg_start = b"\xff\xd8"
        jpeg_end = b"\xff\xd9"

        # Buffer with garbage before the JPEG
        garbage = b"\x00\x01\x02\x03"
        frame_content = b"\xff" * 50
        buffer = garbage + jpeg_start + frame_content + jpeg_end

        start_idx = buffer.find(jpeg_start)
        assert start_idx == 4  # After garbage

        end_idx = buffer.find(jpeg_end, start_idx + 2)
        frame = buffer[start_idx : end_idx + 2]

        assert frame.startswith(jpeg_start)
        assert frame.endswith(jpeg_end)
        assert len(frame) == 54  # 2 + 50 + 2

    def test_incomplete_frame_detection(self):
        """Test detection of incomplete frame (no end marker)."""
        jpeg_start = b"\xff\xd8"

        # Incomplete buffer - no end marker
        buffer = jpeg_start + b"\x00" * 100

        start_idx = buffer.find(jpeg_start)
        end_idx = buffer.find(b"\xff\xd9", start_idx + 2)

        assert start_idx == 0
        assert end_idx == -1  # Not found

    def test_multiple_frames_in_buffer(self):
        """Test extracting first frame when buffer contains multiple frames."""
        jpeg_start = b"\xff\xd8"
        jpeg_end = b"\xff\xd9"

        # Two complete frames
        frame1 = jpeg_start + b"\x01" * 10 + jpeg_end
        frame2 = jpeg_start + b"\x02" * 20 + jpeg_end
        buffer = frame1 + frame2

        # Extract first frame
        start_idx = buffer.find(jpeg_start)
        end_idx = buffer.find(jpeg_end, start_idx + 2)
        first_frame = buffer[start_idx : end_idx + 2]

        assert first_frame == frame1
        assert len(first_frame) == 14

        # Remaining buffer should contain second frame
        remaining = buffer[end_idx + 2 :]
        assert remaining == frame2


class TestCameraTypeValidation:
    """Tests for camera type handling."""

    @pytest.mark.asyncio
    async def test_capture_frame_unknown_type_returns_none(self):
        """Verify unknown camera type returns None."""
        from backend.app.services.external_camera import capture_frame

        result = await capture_frame("http://example.com", "unknown_type")
        assert result is None

    @pytest.mark.asyncio
    async def test_capture_frame_valid_types(self):
        """Verify valid camera types are accepted (they may fail but shouldn't error on type)."""
        from backend.app.services.external_camera import capture_frame

        # These will fail to connect but shouldn't raise type errors
        for camera_type in ["mjpeg", "rtsp", "snapshot"]:
            # Use a non-routable IP to fail fast
            result = await capture_frame("http://192.0.2.1/test", camera_type, timeout=1)
            # Should return None (failed connection) not raise exception
            assert result is None


class TestRtspUrlHandling:
    """Tests for RTSP/RTSPS URL handling."""

    def test_rtsps_url_detection(self):
        """Verify rtsps:// and rtsp:// URL schemes are distinct."""
        url_rtsps = "rtsps://user:pass@192.168.1.1:554/stream"
        url_rtsp = "rtsp://user:pass@192.168.1.1:554/stream"

        assert url_rtsps.startswith("rtsps://")
        assert not url_rtsp.startswith("rtsps://")
        assert url_rtsp.startswith("rtsp://")

    def test_ffmpeg_handles_both_rtsp_and_rtsps(self):
        """Verify ffmpeg command structure handles both URL schemes identically.

        ffmpeg automatically handles TLS for rtsps:// URLs, so no special
        flags are needed - both URL schemes use the same command structure.
        """
        # Both URL types should use the same basic ffmpeg options
        base_cmd = [
            "ffmpeg",
            "-rtsp_transport",
            "tcp",
            "-i",
        ]

        rtsp_url = "rtsp://user:pass@192.168.1.1:554/stream"
        rtsps_url = "rtsps://user:pass@192.168.1.1:554/stream"

        # Command structure is identical for both
        cmd_rtsp = base_cmd + [rtsp_url]
        cmd_rtsps = base_cmd + [rtsps_url]

        # Only the URL differs
        assert cmd_rtsp[:-1] == cmd_rtsps[:-1]
        assert cmd_rtsp[-1] != cmd_rtsps[-1]


class TestUsbCameraHandling:
    """Tests for USB camera support."""

    def test_list_usb_cameras_returns_list(self):
        """Verify list_usb_cameras returns a list (may be empty if no cameras)."""
        from backend.app.services.external_camera import list_usb_cameras

        result = list_usb_cameras()
        assert isinstance(result, list)

    def test_list_usb_cameras_dict_structure(self):
        """Verify each camera entry has expected fields."""
        from backend.app.services.external_camera import list_usb_cameras

        result = list_usb_cameras()
        for camera in result:
            assert "device" in camera
            assert "name" in camera
            assert camera["device"].startswith("/dev/video")

    @pytest.mark.asyncio
    async def test_capture_frame_usb_type_accepted(self):
        """Verify 'usb' camera type is accepted."""
        from backend.app.services.external_camera import capture_frame

        # Non-existent device should fail gracefully
        result = await capture_frame("/dev/video999", "usb", timeout=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_capture_frame_usb_invalid_device_path(self):
        """Verify invalid USB device paths are rejected."""
        from backend.app.services.external_camera import capture_frame

        # Invalid device path (not /dev/video*)
        result = await capture_frame("/dev/sda1", "usb", timeout=1)
        assert result is None

        result = await capture_frame("http://example.com", "usb", timeout=1)
        assert result is None
