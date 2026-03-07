"""Unit tests for the camera service (backend/app/services/camera.py).

Tests model detection, URL building, chamber auth payload, port selection,
and auto quality resolution.
"""

import struct
from unittest.mock import AsyncMock, patch

import pytest


class TestSupportsRtsp:
    """Tests for supports_rtsp() model detection."""

    def test_x1_models(self):
        from backend.app.services.camera import supports_rtsp

        assert supports_rtsp("X1") is True
        assert supports_rtsp("X1C") is True
        assert supports_rtsp("X1E") is True
        assert supports_rtsp("x1c") is True  # case-insensitive

    def test_h2_models(self):
        from backend.app.services.camera import supports_rtsp

        assert supports_rtsp("H2D") is True
        assert supports_rtsp("H2C") is True
        assert supports_rtsp("H2S") is True
        assert supports_rtsp("H2DPRO") is True

    def test_p2_models(self):
        from backend.app.services.camera import supports_rtsp

        assert supports_rtsp("P2S") is True

    def test_internal_codes(self):
        from backend.app.services.camera import supports_rtsp

        for code in ("BL-P001", "C13", "O1D", "O1C", "O1C2", "O1S", "O1E", "O2D", "N7"):
            assert supports_rtsp(code) is True, f"Expected RTSP for internal code {code}"

    def test_chamber_image_models(self):
        from backend.app.services.camera import supports_rtsp

        assert supports_rtsp("A1") is False
        assert supports_rtsp("A1MINI") is False
        assert supports_rtsp("P1P") is False
        assert supports_rtsp("P1S") is False

    def test_none_model(self):
        from backend.app.services.camera import supports_rtsp

        assert supports_rtsp(None) is False

    def test_empty_model(self):
        from backend.app.services.camera import supports_rtsp

        assert supports_rtsp("") is False

    def test_unknown_model(self):
        from backend.app.services.camera import supports_rtsp

        assert supports_rtsp("UNKNOWN123") is False


class TestGetCameraPort:
    """Tests for get_camera_port()."""

    def test_rtsp_model_returns_322(self):
        from backend.app.services.camera import get_camera_port

        assert get_camera_port("X1C") == 322
        assert get_camera_port("H2D") == 322
        assert get_camera_port("P2S") == 322

    def test_chamber_model_returns_6000(self):
        from backend.app.services.camera import get_camera_port

        assert get_camera_port("A1") == 6000
        assert get_camera_port("P1S") == 6000
        assert get_camera_port(None) == 6000


class TestIsChamberImageModel:
    """Tests for is_chamber_image_model()."""

    def test_a1_is_chamber(self):
        from backend.app.services.camera import is_chamber_image_model

        assert is_chamber_image_model("A1") is True
        assert is_chamber_image_model("A1MINI") is True
        assert is_chamber_image_model("P1P") is True
        assert is_chamber_image_model("P1S") is True

    def test_x1_is_not_chamber(self):
        from backend.app.services.camera import is_chamber_image_model

        assert is_chamber_image_model("X1C") is False
        assert is_chamber_image_model("H2D") is False

    def test_none_is_chamber(self):
        from backend.app.services.camera import is_chamber_image_model

        assert is_chamber_image_model(None) is True


class TestBuildCameraUrl:
    """Tests for build_camera_url()."""

    def test_rtsp_model_url(self):
        from backend.app.services.camera import build_camera_url

        url = build_camera_url("192.168.1.10", "ABCD1234", "X1C")
        assert url == "rtsps://bblp:ABCD1234@192.168.1.10:322/streaming/live/1"

    def test_chamber_model_url(self):
        from backend.app.services.camera import build_camera_url

        url = build_camera_url("192.168.1.10", "ABCD1234", "A1")
        assert url == "rtsps://bblp:ABCD1234@192.168.1.10:6000/streaming/live/1"


class TestCreateChamberAuthPayload:
    """Tests for _create_chamber_auth_payload()."""

    def test_payload_length(self):
        from backend.app.services.camera import _create_chamber_auth_payload

        payload = _create_chamber_auth_payload("12345678")
        assert len(payload) == 80

    def test_magic_and_command(self):
        from backend.app.services.camera import _create_chamber_auth_payload

        payload = _create_chamber_auth_payload("12345678")
        magic, command = struct.unpack("<II", payload[:8])
        assert magic == 0x40
        assert command == 0x3000

    def test_username_field(self):
        from backend.app.services.camera import _create_chamber_auth_payload

        payload = _create_chamber_auth_payload("12345678")
        username = payload[16:48].rstrip(b"\x00")
        assert username == b"bblp"

    def test_access_code_field(self):
        from backend.app.services.camera import _create_chamber_auth_payload

        payload = _create_chamber_auth_payload("MYCODE99")
        access_code = payload[48:80].rstrip(b"\x00")
        assert access_code == b"MYCODE99"

    def test_fields_null_padded(self):
        from backend.app.services.camera import _create_chamber_auth_payload

        payload = _create_chamber_auth_payload("AB")
        # Username "bblp" = 4 chars, remaining 28 bytes should be zero
        assert payload[20:48] == b"\x00" * 28
        # Access code "AB" = 2 chars, remaining 30 bytes should be zero
        assert payload[50:80] == b"\x00" * 30


class TestResolveCameraQuality:
    """Tests for resolve_camera_quality() auto-detection logic."""

    @pytest.mark.asyncio
    async def test_non_auto_passes_through(self):
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("low", 1) == "low"
        assert await resolve_camera_quality("medium", 5) == "medium"
        assert await resolve_camera_quality("high", 10) == "high"

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=["videotoolbox"])
    @patch("os.cpu_count", return_value=8)
    async def test_8_cores_gpu_1_printer_high(self, _cpu, _gpu):
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 1) == "high"

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=["videotoolbox"])
    @patch("os.cpu_count", return_value=8)
    async def test_8_cores_gpu_4_printers_medium(self, _cpu, _gpu):
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 4) == "medium"

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=[])
    @patch("os.cpu_count", return_value=4)
    async def test_4_cores_no_gpu_1_printer_medium(self, _cpu, _gpu):
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 1) == "medium"

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=[])
    @patch("os.cpu_count", return_value=4)
    async def test_4_cores_no_gpu_2_printers_low(self, _cpu, _gpu):
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 2) == "low"

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=[])
    @patch("os.cpu_count", return_value=2)
    async def test_2_cores_no_gpu_1_printer_low(self, _cpu, _gpu):
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 1) == "low"

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=["cuda"])
    @patch("os.cpu_count", return_value=2)
    async def test_2_cores_gpu_1_printer_medium(self, _cpu, _gpu):
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 1) == "medium"

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=[])
    @patch("os.cpu_count", return_value=None)
    async def test_none_cpu_count_defaults_to_2(self, _cpu, _gpu):
        """When os.cpu_count() returns None, fallback to 2 cores."""
        from backend.app.services.camera import resolve_camera_quality

        # 2 cores, no GPU, 1 printer => effective=2 => low
        assert await resolve_camera_quality("auto", 1) == "low"

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=[])
    @patch("os.cpu_count", return_value=8)
    async def test_zero_printer_count_treated_as_one(self, _cpu, _gpu):
        """Printer count of 0 should not cause division by zero."""
        from backend.app.services.camera import resolve_camera_quality

        # 8 cores, no GPU, 0 printers => effective=8/1=8 => high
        assert await resolve_camera_quality("auto", 0) == "high"

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=["videotoolbox"])
    @patch("os.cpu_count", return_value=16)
    async def test_16_cores_gpu_many_printers_low(self, _cpu, _gpu):
        """Many printers should reduce quality even on powerful hardware."""
        from backend.app.services.camera import resolve_camera_quality

        # 16 + 4 = 20, 20/10 = 2 => low
        assert await resolve_camera_quality("auto", 10) == "low"
