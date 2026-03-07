"""Unit tests for _sanitize_camera_url in external_camera service.

These are security-relevant: SSRF prevention, scheme validation, URL reconstruction.
"""

import pytest

from backend.app.services.external_camera import _sanitize_camera_url


class TestSanitizeCameraUrlSchemes:
    """Tests for URL scheme validation."""

    def test_http_allowed(self):
        assert _sanitize_camera_url("http://192.168.1.50/mjpeg") is not None

    def test_https_allowed(self):
        assert _sanitize_camera_url("https://192.168.1.50/mjpeg") is not None

    def test_rtsp_allowed(self):
        assert _sanitize_camera_url("rtsp://192.168.1.50/stream", ("rtsp",)) is not None

    def test_rtsps_allowed(self):
        assert _sanitize_camera_url("rtsps://user:pass@192.168.1.50/stream", ("rtsp", "rtsps")) is not None

    def test_ftp_blocked(self):
        assert _sanitize_camera_url("ftp://192.168.1.50/file") is None

    def test_file_blocked(self):
        assert _sanitize_camera_url("file:///etc/passwd") is None

    def test_javascript_blocked(self):
        assert _sanitize_camera_url("javascript:alert(1)") is None

    def test_empty_scheme_blocked(self):
        assert _sanitize_camera_url("://192.168.1.50/stream") is None

    def test_no_netloc_blocked(self):
        assert _sanitize_camera_url("http://") is None


class TestSanitizeCameraUrlBlockedHosts:
    """Tests for SSRF prevention: blocked cloud metadata and localhost."""

    def test_aws_metadata_blocked(self):
        assert _sanitize_camera_url("http://169.254.169.254/latest/meta-data/") is None

    def test_gcp_metadata_blocked(self):
        assert _sanitize_camera_url("http://metadata.google.internal/computeMetadata/v1/") is None

    def test_localhost_blocked(self):
        assert _sanitize_camera_url("http://localhost/stream") is None

    def test_loopback_ipv4_blocked(self):
        assert _sanitize_camera_url("http://127.0.0.1/stream") is None

    def test_loopback_ipv6_blocked(self):
        assert _sanitize_camera_url("http://[::1]/stream") is None

    def test_zero_address_blocked(self):
        assert _sanitize_camera_url("http://0.0.0.0/stream") is None  # nosec B104

    def test_link_local_ipv4_blocked(self):
        assert _sanitize_camera_url("http://169.254.1.1/stream") is None

    def test_link_local_ipv6_blocked(self):
        assert _sanitize_camera_url("http://[fe80::1]/stream") is None


class TestSanitizeCameraUrlReconstruction:
    """Tests for URL reconstruction from validated components."""

    def test_preserves_path(self):
        result = _sanitize_camera_url("http://192.168.1.50/video/mjpeg")
        assert result == "http://192.168.1.50/video/mjpeg"

    def test_preserves_port(self):
        result = _sanitize_camera_url("http://192.168.1.50:8080/stream")
        assert ":8080" in result

    def test_preserves_query(self):
        result = _sanitize_camera_url("http://192.168.1.50/stream?fps=10&quality=5")
        assert "?fps=10&quality=5" in result

    def test_preserves_rtsp_credentials(self):
        result = _sanitize_camera_url("rtsp://admin:secret@192.168.1.50/stream", ("rtsp",))
        assert "admin:secret@" in result

    def test_strips_http_credentials(self):
        """HTTP URLs should NOT preserve userinfo (only RTSP/RTSPS do)."""
        result = _sanitize_camera_url("http://user:pass@192.168.1.50/stream")
        assert "user:pass@" not in result

    def test_preserves_fragment(self):
        result = _sanitize_camera_url("http://192.168.1.50/stream#section")
        assert "#section" in result


class TestSanitizeCameraUrlValid:
    """Tests for valid camera URLs on the LAN."""

    def test_private_ip_allowed(self):
        assert _sanitize_camera_url("http://10.0.0.5/stream") is not None
        assert _sanitize_camera_url("http://172.16.0.1/stream") is not None
        assert _sanitize_camera_url("http://192.168.1.100/mjpeg") is not None

    def test_hostname_allowed(self):
        assert _sanitize_camera_url("http://my-camera.local/stream") is not None

    def test_returns_none_for_garbage(self):
        assert _sanitize_camera_url("not a url at all") is None
        assert _sanitize_camera_url("") is None


class TestValidateUsbDevice:
    """Tests for _validate_usb_device path validation."""

    def test_valid_device(self):
        from unittest.mock import patch

        from backend.app.services.external_camera import _validate_usb_device

        with patch("pathlib.Path.exists", return_value=True):
            assert _validate_usb_device("/dev/video0") == "/dev/video0"
            assert _validate_usb_device("/dev/video99") == "/dev/video99"

    def test_invalid_path_format(self):
        from backend.app.services.external_camera import _validate_usb_device

        assert _validate_usb_device("/dev/sda1") is None
        assert _validate_usb_device("http://example.com") is None
        assert _validate_usb_device("/dev/video") is None
        assert _validate_usb_device("/dev/video-1") is None

    def test_device_number_out_of_range(self):
        from backend.app.services.external_camera import _validate_usb_device

        assert _validate_usb_device("/dev/video100") is None

    def test_nonexistent_device(self):
        from unittest.mock import patch

        from backend.app.services.external_camera import _validate_usb_device

        with patch("pathlib.Path.exists", return_value=False):
            assert _validate_usb_device("/dev/video0") is None
