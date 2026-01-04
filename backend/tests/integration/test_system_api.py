"""Integration tests for System API endpoints.

Tests the full request/response cycle for /api/v1/system/ endpoints.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


class TestSystemAPI:
    """Integration tests for /api/v1/system/ endpoints."""

    # ========================================================================
    # System Info Endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_system_info(self, async_client: AsyncClient):
        """Verify system info endpoint returns expected structure."""
        # Mock psutil to avoid system-specific values
        with patch("backend.app.api.routes.system.psutil") as mock_psutil:
            mock_psutil.disk_usage.return_value = MagicMock(
                total=500000000000, used=250000000000, free=250000000000, percent=50.0
            )
            mock_psutil.virtual_memory.return_value = MagicMock(
                total=16000000000, available=8000000000, used=8000000000, percent=50.0
            )
            mock_psutil.boot_time.return_value = 1700000000.0
            mock_psutil.cpu_count.return_value = 4
            mock_psutil.cpu_percent.return_value = 25.0

            response = await async_client.get("/api/v1/system/info")

        assert response.status_code == 200
        result = response.json()

        # Verify top-level structure
        assert "app" in result
        assert "database" in result
        assert "printers" in result
        assert "storage" in result
        assert "system" in result
        assert "memory" in result
        assert "cpu" in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_system_info_app_section(self, async_client: AsyncClient):
        """Verify app section contains version and directory info."""
        with patch("backend.app.api.routes.system.psutil") as mock_psutil:
            mock_psutil.disk_usage.return_value = MagicMock(
                total=500000000000, used=250000000000, free=250000000000, percent=50.0
            )
            mock_psutil.virtual_memory.return_value = MagicMock(
                total=16000000000, available=8000000000, used=8000000000, percent=50.0
            )
            mock_psutil.boot_time.return_value = 1700000000.0
            mock_psutil.cpu_count.return_value = 4
            mock_psutil.cpu_percent.return_value = 25.0

            response = await async_client.get("/api/v1/system/info")

        result = response.json()
        app_info = result["app"]

        assert "version" in app_info
        assert "base_dir" in app_info
        assert "archive_dir" in app_info

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_system_info_database_section(self, async_client: AsyncClient):
        """Verify database section contains counts and statistics."""
        with patch("backend.app.api.routes.system.psutil") as mock_psutil:
            mock_psutil.disk_usage.return_value = MagicMock(
                total=500000000000, used=250000000000, free=250000000000, percent=50.0
            )
            mock_psutil.virtual_memory.return_value = MagicMock(
                total=16000000000, available=8000000000, used=8000000000, percent=50.0
            )
            mock_psutil.boot_time.return_value = 1700000000.0
            mock_psutil.cpu_count.return_value = 4
            mock_psutil.cpu_percent.return_value = 25.0

            response = await async_client.get("/api/v1/system/info")

        result = response.json()
        db_info = result["database"]

        assert "archives" in db_info
        assert "archives_completed" in db_info
        assert "archives_failed" in db_info
        assert "printers" in db_info
        assert "filaments" in db_info
        assert "projects" in db_info
        assert "smart_plugs" in db_info
        assert "total_print_time_seconds" in db_info
        assert "total_print_time_formatted" in db_info
        assert "total_filament_grams" in db_info
        assert "total_filament_kg" in db_info

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_system_info_storage_section(self, async_client: AsyncClient):
        """Verify storage section contains disk usage info."""
        with patch("backend.app.api.routes.system.psutil") as mock_psutil:
            mock_psutil.disk_usage.return_value = MagicMock(
                total=500000000000, used=250000000000, free=250000000000, percent=50.0
            )
            mock_psutil.virtual_memory.return_value = MagicMock(
                total=16000000000, available=8000000000, used=8000000000, percent=50.0
            )
            mock_psutil.boot_time.return_value = 1700000000.0
            mock_psutil.cpu_count.return_value = 4
            mock_psutil.cpu_percent.return_value = 25.0

            response = await async_client.get("/api/v1/system/info")

        result = response.json()
        storage_info = result["storage"]

        assert "archive_size_bytes" in storage_info
        assert "archive_size_formatted" in storage_info
        assert "database_size_bytes" in storage_info
        assert "database_size_formatted" in storage_info
        assert "disk_total_bytes" in storage_info
        assert "disk_total_formatted" in storage_info
        assert "disk_used_bytes" in storage_info
        assert "disk_free_bytes" in storage_info
        assert "disk_percent_used" in storage_info

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_system_info_memory_section(self, async_client: AsyncClient):
        """Verify memory section contains RAM usage info."""
        with patch("backend.app.api.routes.system.psutil") as mock_psutil:
            mock_psutil.disk_usage.return_value = MagicMock(
                total=500000000000, used=250000000000, free=250000000000, percent=50.0
            )
            mock_psutil.virtual_memory.return_value = MagicMock(
                total=16000000000, available=8000000000, used=8000000000, percent=50.0
            )
            mock_psutil.boot_time.return_value = 1700000000.0
            mock_psutil.cpu_count.return_value = 4
            mock_psutil.cpu_percent.return_value = 25.0

            response = await async_client.get("/api/v1/system/info")

        result = response.json()
        memory_info = result["memory"]

        assert "total_bytes" in memory_info
        assert "total_formatted" in memory_info
        assert "available_bytes" in memory_info
        assert "used_bytes" in memory_info
        assert "percent_used" in memory_info

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_system_info_cpu_section(self, async_client: AsyncClient):
        """Verify CPU section contains processor info."""
        with patch("backend.app.api.routes.system.psutil") as mock_psutil:
            mock_psutil.disk_usage.return_value = MagicMock(
                total=500000000000, used=250000000000, free=250000000000, percent=50.0
            )
            mock_psutil.virtual_memory.return_value = MagicMock(
                total=16000000000, available=8000000000, used=8000000000, percent=50.0
            )
            mock_psutil.boot_time.return_value = 1700000000.0
            mock_psutil.cpu_count.return_value = 4
            mock_psutil.cpu_percent.return_value = 25.0

            response = await async_client.get("/api/v1/system/info")

        result = response.json()
        cpu_info = result["cpu"]

        assert "count" in cpu_info
        assert "count_logical" in cpu_info
        assert "percent" in cpu_info

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_system_info_printers_section(self, async_client: AsyncClient, printer_factory):
        """Verify printers section contains connected printer info."""
        # Create a test printer
        _printer = await printer_factory(name="Test Printer", model="X1C")

        with (
            patch("backend.app.api.routes.system.psutil") as mock_psutil,
            patch("backend.app.api.routes.system.printer_manager") as mock_pm,
        ):
            mock_psutil.disk_usage.return_value = MagicMock(
                total=500000000000, used=250000000000, free=250000000000, percent=50.0
            )
            mock_psutil.virtual_memory.return_value = MagicMock(
                total=16000000000, available=8000000000, used=8000000000, percent=50.0
            )
            mock_psutil.boot_time.return_value = 1700000000.0
            mock_psutil.cpu_count.return_value = 4
            mock_psutil.cpu_percent.return_value = 25.0

            # Mock no connected printers for simplicity
            mock_pm._clients = {}

            response = await async_client.get("/api/v1/system/info")

        result = response.json()
        printers_info = result["printers"]

        assert "total" in printers_info
        assert "connected" in printers_info
        assert "connected_list" in printers_info
        assert printers_info["total"] >= 1  # At least our test printer

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_system_info_with_archives(self, async_client: AsyncClient, printer_factory, archive_factory):
        """Verify database stats include archive counts."""
        printer = await printer_factory()
        await archive_factory(printer.id, status="completed", print_time_seconds=3600)
        await archive_factory(printer.id, status="failed", print_time_seconds=1800)

        with (
            patch("backend.app.api.routes.system.psutil") as mock_psutil,
            patch("backend.app.api.routes.system.printer_manager") as mock_pm,
        ):
            mock_psutil.disk_usage.return_value = MagicMock(
                total=500000000000, used=250000000000, free=250000000000, percent=50.0
            )
            mock_psutil.virtual_memory.return_value = MagicMock(
                total=16000000000, available=8000000000, used=8000000000, percent=50.0
            )
            mock_psutil.boot_time.return_value = 1700000000.0
            mock_psutil.cpu_count.return_value = 4
            mock_psutil.cpu_percent.return_value = 25.0
            mock_pm._clients = {}

            response = await async_client.get("/api/v1/system/info")

        result = response.json()
        db_info = result["database"]

        assert db_info["archives"] >= 2
        assert db_info["archives_completed"] >= 1
        assert db_info["archives_failed"] >= 1
        assert db_info["total_print_time_seconds"] >= 5400


class TestSystemHelperFunctions:
    """Tests for system info helper functions."""

    def test_format_bytes_bytes(self):
        """Verify format_bytes handles bytes correctly."""
        from backend.app.api.routes.system import format_bytes

        assert format_bytes(500) == "500.0 B"

    def test_format_bytes_kilobytes(self):
        """Verify format_bytes handles kilobytes correctly."""
        from backend.app.api.routes.system import format_bytes

        result = format_bytes(1536)
        assert "KB" in result

    def test_format_bytes_megabytes(self):
        """Verify format_bytes handles megabytes correctly."""
        from backend.app.api.routes.system import format_bytes

        result = format_bytes(1536 * 1024)
        assert "MB" in result

    def test_format_bytes_gigabytes(self):
        """Verify format_bytes handles gigabytes correctly."""
        from backend.app.api.routes.system import format_bytes

        result = format_bytes(1536 * 1024 * 1024)
        assert "GB" in result

    def test_format_uptime_minutes(self):
        """Verify format_uptime handles minutes correctly."""
        from backend.app.api.routes.system import format_uptime

        result = format_uptime(300)  # 5 minutes
        assert "5m" in result

    def test_format_uptime_hours(self):
        """Verify format_uptime handles hours correctly."""
        from backend.app.api.routes.system import format_uptime

        result = format_uptime(7200)  # 2 hours
        assert "2h" in result

    def test_format_uptime_days(self):
        """Verify format_uptime handles days correctly."""
        from backend.app.api.routes.system import format_uptime

        result = format_uptime(86400 * 2 + 3600 * 5)  # 2 days 5 hours
        assert "2d" in result
        assert "5h" in result

    def test_format_uptime_less_than_minute(self):
        """Verify format_uptime handles < 1 minute correctly."""
        from backend.app.api.routes.system import format_uptime

        result = format_uptime(30)  # 30 seconds
        assert result == "< 1m"
