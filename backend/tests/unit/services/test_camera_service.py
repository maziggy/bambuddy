"""Unit tests for the camera service (backend/app/services/camera.py).

Tests model detection, URL building, chamber auth payload, port selection,
hardware probing, and auto quality resolution.
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


# ---------------------------------------------------------------------------
# Helper to build a fake hw_info dict for mocking _get_system_hw_info
# ---------------------------------------------------------------------------
def _make_hw_info(
    cpu_count: int = 8,
    cpu_score: float = 2.83,
    ram_gb: float = 16.0,
    ram_score: float = 3.0,
    gpu_backends: list[str] | None = None,
    gpu_score: float = 0.0,
    gpu_penalty_factor: float = 1.0,
    base_score: float | None = None,
) -> dict:
    if base_score is None:
        base_score = (cpu_score + ram_score + gpu_score) * 2
    return {
        "cpu_count": cpu_count,
        "cpu_score": cpu_score,
        "ram_gb": ram_gb,
        "ram_score": ram_score,
        "gpu_backends": gpu_backends or [],
        "gpu_score": gpu_score,
        "gpu_penalty_factor": gpu_penalty_factor,
        "base_score": base_score,
    }


class TestGetSystemHwInfo:
    """Tests for _get_system_hw_info() hardware probing."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        from backend.app.services.camera import _reset_system_hw_cache

        _reset_system_hw_cache()
        yield
        _reset_system_hw_cache()

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=["videotoolbox"])
    @patch("backend.app.services.camera.psutil")
    @patch("backend.app.services.camera.platform")
    @patch("backend.app.services.camera.sys")
    @patch("os.cpu_count", return_value=12)
    async def test_apple_silicon(self, _cpu, mock_sys, mock_platform, mock_psutil, _gpu):
        from backend.app.services.camera import _get_system_hw_info

        mock_sys.platform = "darwin"
        mock_platform.machine.return_value = "arm64"
        mock_psutil.virtual_memory.return_value.total = 36 * 1024**3

        hw = await _get_system_hw_info()
        assert hw["cpu_count"] == 12
        assert hw["gpu_score"] == 4.0
        assert hw["gpu_penalty_factor"] == 0.25
        # cpu_score = sqrt(12)*1.3 ≈ 4.50, ram_score = min(log2(36), 3) = 3.0
        # base = (4.50 + 3.0 + 4.0) * 2 ≈ 23.0
        assert 22.5 < hw["base_score"] < 23.5

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=["cuda"])
    @patch("backend.app.services.camera.psutil")
    @patch("backend.app.services.camera.platform")
    @patch("backend.app.services.camera.sys")
    @patch("os.cpu_count", return_value=16)
    async def test_x86_cuda(self, _cpu, mock_sys, mock_platform, mock_psutil, _gpu):
        from backend.app.services.camera import _get_system_hw_info

        mock_sys.platform = "linux"
        mock_platform.machine.return_value = "x86_64"
        mock_psutil.virtual_memory.return_value.total = 32 * 1024**3

        hw = await _get_system_hw_info()
        assert hw["gpu_score"] == 3.0
        assert hw["gpu_penalty_factor"] == 0.35
        # cpu_score = sqrt(16)*1.0 = 4.0, ram = 3.0, gpu = 3.0
        # base = (4+3+3)*2 = 20.0
        assert hw["base_score"] == pytest.approx(20.0, abs=0.1)

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=[])
    @patch("backend.app.services.camera.psutil")
    @patch("backend.app.services.camera.platform")
    @patch("backend.app.services.camera.sys")
    @patch("os.cpu_count", return_value=4)
    async def test_raspberry_pi(self, _cpu, mock_sys, mock_platform, mock_psutil, _gpu):
        from backend.app.services.camera import _get_system_hw_info

        mock_sys.platform = "linux"
        mock_platform.machine.return_value = "aarch64"
        mock_psutil.virtual_memory.return_value.total = 4 * 1024**3

        hw = await _get_system_hw_info()
        # ARM non-Apple: core_efficiency = 0.5
        # cpu_score = sqrt(4)*0.5 = 1.0, ram_score = log2(4) = 2.0
        # base = (1.0 + 2.0 + 0.0) * 2 = 6.0
        assert hw["base_score"] == pytest.approx(6.0, abs=0.1)
        assert hw["gpu_penalty_factor"] == 1.0

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=["qsv"])
    @patch("backend.app.services.camera.psutil")
    @patch("backend.app.services.camera.platform")
    @patch("backend.app.services.camera.sys")
    @patch("os.cpu_count", return_value=8)
    async def test_intel_qsv(self, _cpu, mock_sys, mock_platform, mock_psutil, _gpu):
        from backend.app.services.camera import _get_system_hw_info

        mock_sys.platform = "linux"
        mock_platform.machine.return_value = "x86_64"
        mock_psutil.virtual_memory.return_value.total = 16 * 1024**3

        hw = await _get_system_hw_info()
        assert hw["gpu_score"] == 2.5
        assert hw["gpu_penalty_factor"] == 0.4

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=["videotoolbox"])
    @patch("backend.app.services.camera.psutil")
    @patch("backend.app.services.camera.platform")
    @patch("backend.app.services.camera.sys")
    @patch("os.cpu_count", return_value=8)
    async def test_intel_mac_videotoolbox(self, _cpu, mock_sys, mock_platform, mock_psutil, _gpu):
        from backend.app.services.camera import _get_system_hw_info

        mock_sys.platform = "darwin"
        mock_platform.machine.return_value = "x86_64"
        mock_psutil.virtual_memory.return_value.total = 16 * 1024**3

        hw = await _get_system_hw_info()
        assert hw["gpu_score"] == 3.0
        assert hw["gpu_penalty_factor"] == 0.4

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=["vaapi"])
    @patch("backend.app.services.camera.psutil")
    @patch("backend.app.services.camera.platform")
    @patch("backend.app.services.camera.sys")
    @patch("os.cpu_count", return_value=4)
    async def test_vaapi(self, _cpu, mock_sys, mock_platform, mock_psutil, _gpu):
        from backend.app.services.camera import _get_system_hw_info

        mock_sys.platform = "linux"
        mock_platform.machine.return_value = "x86_64"
        mock_psutil.virtual_memory.return_value.total = 8 * 1024**3

        hw = await _get_system_hw_info()
        assert hw["gpu_score"] == 2.0
        assert hw["gpu_penalty_factor"] == 0.5

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=[])
    @patch("backend.app.services.camera.psutil")
    @patch("backend.app.services.camera.platform")
    @patch("backend.app.services.camera.sys")
    @patch("os.cpu_count", return_value=None)
    async def test_none_cpu_count_defaults_to_2(self, _cpu, mock_sys, mock_platform, mock_psutil, _gpu):
        from backend.app.services.camera import _get_system_hw_info

        mock_sys.platform = "linux"
        mock_platform.machine.return_value = "x86_64"
        mock_psutil.virtual_memory.return_value.total = 4 * 1024**3

        hw = await _get_system_hw_info()
        assert hw["cpu_count"] == 2

    @pytest.mark.asyncio
    @patch("backend.app.services.camera.detect_gpu_hwaccels", new_callable=AsyncMock, return_value=["d3d11va"])
    @patch("backend.app.services.camera.psutil")
    @patch("backend.app.services.camera.platform")
    @patch("backend.app.services.camera.sys")
    @patch("os.cpu_count", return_value=8)
    async def test_unknown_gpu_backend(self, _cpu, mock_sys, mock_platform, mock_psutil, _gpu):
        from backend.app.services.camera import _get_system_hw_info

        mock_sys.platform = "win32"
        mock_platform.machine.return_value = "AMD64"
        mock_psutil.virtual_memory.return_value.total = 16 * 1024**3

        hw = await _get_system_hw_info()
        assert hw["gpu_score"] == 1.0
        assert hw["gpu_penalty_factor"] == 0.7


class TestResolveCameraQuality:
    """Tests for resolve_camera_quality() auto-detection logic."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        from backend.app.services.camera import _reset_system_hw_cache

        _reset_system_hw_cache()
        yield
        _reset_system_hw_cache()

    @pytest.mark.asyncio
    async def test_non_auto_passes_through(self):
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("low", 1) == "low"
        assert await resolve_camera_quality("medium", 5) == "medium"
        assert await resolve_camera_quality("high", 10) == "high"

    @pytest.mark.asyncio
    @patch(
        "backend.app.services.camera._get_system_hw_info",
        new_callable=AsyncMock,
        return_value=_make_hw_info(base_score=23.0, gpu_penalty_factor=0.25),
    )
    async def test_macbook_pro_m3_1_stream_high(self, _hw):
        """MacBook Pro M3 Pro (12c/36GB/vtb), 1 stream -> high"""
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 1) == "high"

    @pytest.mark.asyncio
    @patch(
        "backend.app.services.camera._get_system_hw_info",
        new_callable=AsyncMock,
        return_value=_make_hw_info(base_score=23.0, gpu_penalty_factor=0.25),
    )
    async def test_macbook_pro_m3_6_streams_high(self, _hw):
        """MacBook Pro M3 Pro, 6 streams -> still high"""
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 6) == "high"

    @pytest.mark.asyncio
    @patch(
        "backend.app.services.camera._get_system_hw_info",
        new_callable=AsyncMock,
        return_value=_make_hw_info(base_score=23.0, gpu_penalty_factor=0.25),
    )
    async def test_macbook_pro_m3_12_streams_high(self, _hw):
        """MacBook Pro M3 Pro, 12 streams -> still high"""
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 12) == "high"

    @pytest.mark.asyncio
    @patch(
        "backend.app.services.camera._get_system_hw_info",
        new_callable=AsyncMock,
        return_value=_make_hw_info(base_score=20.0, gpu_penalty_factor=0.35),
    )
    async def test_desktop_cuda_1_stream_high(self, _hw):
        """Desktop 16c/32GB/CUDA, 1 stream -> high"""
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 1) == "high"

    @pytest.mark.asyncio
    @patch(
        "backend.app.services.camera._get_system_hw_info",
        new_callable=AsyncMock,
        return_value=_make_hw_info(base_score=20.0, gpu_penalty_factor=0.35),
    )
    async def test_desktop_cuda_10_streams_medium(self, _hw):
        """Desktop 16c/32GB/CUDA, 10 streams -> medium"""
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 10) == "medium"

    @pytest.mark.asyncio
    @patch(
        "backend.app.services.camera._get_system_hw_info",
        new_callable=AsyncMock,
        return_value=_make_hw_info(base_score=10.0, gpu_penalty_factor=1.0),
    )
    async def test_mini_pc_no_gpu_12_streams_low(self, _hw):
        """Mini PC 4c/8GB/no GPU, 12 streams -> low"""
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 12) == "low"

    @pytest.mark.asyncio
    @patch(
        "backend.app.services.camera._get_system_hw_info",
        new_callable=AsyncMock,
        return_value=_make_hw_info(base_score=6.8, gpu_penalty_factor=1.0),
    )
    async def test_mini_pc_2c_no_gpu_1_stream_low(self, _hw):
        """Mini PC 2c/4GB/no GPU, 1 stream -> low"""
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 1) == "low"

    @pytest.mark.asyncio
    @patch(
        "backend.app.services.camera._get_system_hw_info",
        new_callable=AsyncMock,
        return_value=_make_hw_info(base_score=6.0, gpu_penalty_factor=1.0),
    )
    async def test_raspberry_pi_1_stream_low(self, _hw):
        """Raspberry Pi 4 (4c/4GB/none), 1 stream -> low"""
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 1) == "low"

    @pytest.mark.asyncio
    @patch(
        "backend.app.services.camera._get_system_hw_info",
        new_callable=AsyncMock,
        return_value=_make_hw_info(base_score=15.0, gpu_penalty_factor=0.4),
    )
    async def test_mini_pc_qsv_12_streams_medium(self, _hw):
        """Mini PC 4c/8GB/QSV, 12 streams -> medium"""
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 12) == "medium"

    @pytest.mark.asyncio
    @patch(
        "backend.app.services.camera._get_system_hw_info",
        new_callable=AsyncMock,
        return_value=_make_hw_info(base_score=16.7, gpu_penalty_factor=0.4),
    )
    async def test_intel_nuc_qsv_4_streams_medium(self, _hw):
        """Intel NUC 8c/16GB/QSV, 4 streams -> medium"""
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 4) == "medium"

    @pytest.mark.asyncio
    @patch(
        "backend.app.services.camera._get_system_hw_info",
        new_callable=AsyncMock,
        return_value=_make_hw_info(base_score=20.0, gpu_penalty_factor=0.35),
    )
    async def test_zero_stream_count_treated_as_one(self, _hw):
        """Stream count of 0 should not cause division by zero."""
        from backend.app.services.camera import resolve_camera_quality

        assert await resolve_camera_quality("auto", 0) == "high"
