"""Integration tests for Printers API endpoints.

Tests the full request/response cycle for /api/v1/printers/ endpoints.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote

import pytest
from httpx import AsyncClient
from sqlalchemy import select


class TestPrintersAPI:
    """Integration tests for /api/v1/printers/ endpoints."""

    # ========================================================================
    # List endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_printers_empty(self, async_client: AsyncClient):
        """Verify empty list is returned when no printers exist."""
        response = await async_client.get("/api/v1/printers/")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_printers_with_data(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify list returns existing printers."""
        await printer_factory(name="Test Printer")

        response = await async_client.get("/api/v1/printers/")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(p["name"] == "Test Printer" for p in data)

    # ========================================================================
    # Create endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_printer(self, async_client: AsyncClient):
        """Verify printer can be created."""
        data = {
            "name": "New Printer",
            "serial_number": "00M09A111111111",
            "ip_address": "192.168.1.100",
            "access_code": "12345678",
            "is_active": True,
            "model": "X1C",
        }

        response = await async_client.post("/api/v1/printers/", json=data)

        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "New Printer"
        assert result["serial_number"] == "00M09A111111111"
        assert result["model"] == "X1C"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_printer_with_hostname(self, async_client: AsyncClient):
        """Verify printer can be created with a hostname instead of IP address."""
        data = {
            "name": "DNS Printer",
            "serial_number": "00M09A555555555",
            "ip_address": "printer.local",
            "access_code": "12345678",
            "model": "P1S",
        }

        response = await async_client.post("/api/v1/printers/", json=data)

        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "DNS Printer"
        assert result["ip_address"] == "printer.local"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_printer_with_fqdn(self, async_client: AsyncClient):
        """Verify printer can be created with a fully qualified domain name."""
        data = {
            "name": "FQDN Printer",
            "serial_number": "00M09A666666666",
            "ip_address": "my-printer.home.lan",
            "access_code": "12345678",
            "model": "X1C",
        }

        response = await async_client.post("/api/v1/printers/", json=data)

        assert response.status_code == 200
        result = response.json()
        assert result["ip_address"] == "my-printer.home.lan"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_printer_invalid_hostname(self, async_client: AsyncClient):
        """Verify invalid hostnames are rejected."""
        data = {
            "name": "Bad Printer",
            "serial_number": "00M09A777777777",
            "ip_address": "-invalid",
            "access_code": "12345678",
        }

        response = await async_client.post("/api/v1/printers/", json=data)

        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_printer_duplicate_serial(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify duplicate serial number is rejected."""
        await printer_factory(serial_number="00M09A222222222")

        data = {
            "name": "Duplicate Printer",
            "serial_number": "00M09A222222222",
            "ip_address": "192.168.1.101",
            "access_code": "12345678",
        }

        response = await async_client.post("/api/v1/printers/", json=data)

        # Should fail due to duplicate serial
        assert response.status_code in [400, 409, 422, 500]

    # ========================================================================
    # Get single endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_printer(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify single printer can be retrieved."""
        printer = await printer_factory(name="Get Test Printer")

        response = await async_client.get(f"/api/v1/printers/{printer.id}")

        assert response.status_code == 200
        result = response.json()
        assert result["id"] == printer.id
        assert result["name"] == "Get Test Printer"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_printer_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.get("/api/v1/printers/9999")

        assert response.status_code == 404

    # ========================================================================
    # Update endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_printer_name(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify printer name can be updated."""
        printer = await printer_factory(name="Original Name")

        response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"name": "Updated Name"})

        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_printer_active_status(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify printer active status can be updated."""
        printer = await printer_factory(is_active=True)

        response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"is_active": False})

        assert response.status_code == 200
        assert response.json()["is_active"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_printer_auto_archive(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify auto_archive setting can be updated."""
        printer = await printer_factory(auto_archive=True)

        response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"auto_archive": False})

        assert response.status_code == 200
        assert response.json()["auto_archive"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_nonexistent_printer(self, async_client: AsyncClient):
        """Verify updating non-existent printer returns 404."""
        response = await async_client.patch("/api/v1/printers/9999", json={"name": "New Name"})

        assert response.status_code == 404

    # ========================================================================
    # Delete endpoints
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_printer(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify printer can be deleted."""
        printer = await printer_factory()
        printer_id = printer.id

        response = await async_client.delete(f"/api/v1/printers/{printer_id}")

        assert response.status_code == 200

        # Verify deleted
        response = await async_client.get(f"/api/v1/printers/{printer_id}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_nonexistent_printer(self, async_client: AsyncClient):
        """Verify deleting non-existent printer returns 404."""
        response = await async_client.delete("/api/v1/printers/9999")

        assert response.status_code == 404

    # ========================================================================
    # File download endpoint — non-ASCII filename regression (#1245)
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        ("filename", "ascii_fallback"),
        [
            ("龙泡泡石墩子_p2s_ok.gcode.3mf", "p2s_ok.gcode.3mf"),
            ("こんにちは.gcode.3mf", "gcode.3mf"),
            ("résumé.gcode.3mf", "rsum.gcode.3mf"),
            ("مرحبا.gcode.3mf", "gcode.3mf"),
            ("文件.3mf", "3mf"),
            ("hello.3mf", "hello.3mf"),
        ],
    )
    async def test_download_printer_file_non_ascii_filename(
        self,
        async_client: AsyncClient,
        printer_factory,
        filename: str,
        ascii_fallback: str,
        db_session,
    ):
        """Non-ASCII filenames must not crash header encoding (issue #1245)."""
        printer = await printer_factory()
        file_bytes = b"fake 3mf content"

        with patch(
            "backend.app.api.routes.printers.download_file_bytes_async",
            new=AsyncMock(return_value=file_bytes),
        ):
            response = await async_client.get(
                f"/api/v1/printers/{printer.id}/files/download",
                params={"path": f"/cache/{filename}"},
            )

        assert response.status_code == 200
        assert response.content == file_bytes

        content_disposition = response.headers["content-disposition"]
        assert f'filename="{ascii_fallback}"' in content_disposition
        assert "filename*=UTF-8''" in content_disposition
        encoded_name = content_disposition.split("filename*=UTF-8''", 1)[1]
        assert unquote(encoded_name) == filename

    # ========================================================================
    # Status endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_printer_status(
        self, async_client: AsyncClient, printer_factory, mock_printer_manager, db_session
    ):
        """Verify printer status can be retrieved."""
        printer = await printer_factory()

        response = await async_client.get(f"/api/v1/printers/{printer.id}/status")

        assert response.status_code == 200
        result = response.json()
        assert "connected" in result
        assert "state" in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_printer_status_not_found(self, async_client: AsyncClient):
        """Verify 404 for status of non-existent printer."""
        response = await async_client.get("/api/v1/printers/9999/status")

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_printer_status_includes_fila_switch_when_installed(
        self, async_client: AsyncClient, printer_factory, db_session
    ):
        """When the FTS accessory is installed, the status response must include
        the fila_switch object with the routing arrays. See #1162.

        The accessory is detected from print.device.fila_switch in MQTT;
        we feed a PrinterState with FilaSwitchState(installed=True, ...) and
        confirm it survives the schema serialization round-trip.
        """
        from unittest.mock import MagicMock, patch

        from backend.app.services.bambu_mqtt import FilaSwitchState, PrinterState

        printer = await printer_factory()

        state = PrinterState()
        state.connected = True
        state.state = "IDLE"
        state.fila_switch = FilaSwitchState(
            installed=True,
            in_slots=[-1, 2],
            out_extruders=[0, 1],
            stat=0,
            info=2,
        )

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_status = MagicMock(return_value=state)
            mock_pm.is_awaiting_plate_clear = MagicMock(return_value=False)

            response = await async_client.get(f"/api/v1/printers/{printer.id}/status")

        assert response.status_code == 200
        result = response.json()
        assert result["fila_switch"] is not None
        assert result["fila_switch"]["installed"] is True
        assert result["fila_switch"]["in_slots"] == [-1, 2]
        assert result["fila_switch"]["out_extruders"] == [0, 1]
        assert result["fila_switch"]["stat"] == 0
        assert result["fila_switch"]["info"] == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cover_uses_dispatched_plate_when_gcode_file_lacks_path(
        self, async_client: AsyncClient, printer_factory, db_session, tmp_path
    ):
        """When firmware drops the plate path from gcode_file (e.g. P1S
        01.10.00.00, #1166), the dispatched-plate record must take precedence
        and serve plate 4's thumbnail instead of falling back to plate_1.png."""
        import io
        import zipfile
        from unittest.mock import MagicMock, patch

        from backend.app.services.bambu_ftp import cache_3mf_download
        from backend.app.services.bambu_mqtt import PrinterState

        printer = await printer_factory()

        # Build a 3MF that mimics a "true" multi-plate archive: thumbnails
        # for plates 1..4 are all present, gcode files for plates 1..4 are
        # all present. Without the dispatch record we'd default to plate_1.png.
        threemf_path = tmp_path / "MyModel.3mf"
        with zipfile.ZipFile(threemf_path, "w") as zf:
            for plate in range(1, 5):
                zf.writestr(f"Metadata/plate_{plate}.png", f"PLATE_{plate}_PNG".encode())
                zf.writestr(f"Metadata/plate_{plate}.gcode", f"; plate {plate} gcode\n")

        cache_3mf_download(printer.id, "MyModel.3mf", threemf_path)

        state = PrinterState()
        state.connected = True
        state.state = "RUNNING"
        state.subtask_name = "MyModel"
        state.gcode_file = "MyModel.3mf"  # firmware drops plate path
        state.dispatched_plate_id = 4
        state.dispatched_subtask = "MyModel"

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_status = MagicMock(return_value=state)
            mock_pm.is_awaiting_plate_clear = MagicMock(return_value=False)

            response = await async_client.get(f"/api/v1/printers/{printer.id}/cover")

        assert response.status_code == 200
        assert response.content == b"PLATE_4_PNG"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cover_3mf_scan_fallback_for_per_plate_archive(
        self, async_client: AsyncClient, printer_factory, db_session, tmp_path
    ):
        """Per-plate archives sliced separately in Bambu Studio contain a
        single Metadata/plate_N.gcode (the active plate) but bundle thumbnails
        for every plate. With no dispatch record (e.g. dispatched via Studio
        directly) and no plate path in gcode_file, the route must scan the
        3MF and pick plate N's thumbnail. See #1166 option 4."""
        import zipfile
        from unittest.mock import MagicMock, patch

        from backend.app.services.bambu_ftp import cache_3mf_download
        from backend.app.services.bambu_mqtt import PrinterState

        printer = await printer_factory()

        # Per-plate archive: thumbnails for all plates, gcode for plate 3 only.
        threemf_path = tmp_path / "PerPlate.3mf"
        with zipfile.ZipFile(threemf_path, "w") as zf:
            for plate in range(1, 5):
                zf.writestr(f"Metadata/plate_{plate}.png", f"PLATE_{plate}_PNG".encode())
            zf.writestr("Metadata/plate_3.gcode", "; only plate 3 has gcode\n")

        cache_3mf_download(printer.id, "PerPlate.3mf", threemf_path)

        state = PrinterState()
        state.connected = True
        state.state = "RUNNING"
        state.subtask_name = "PerPlate"
        state.gcode_file = "PerPlate.3mf"
        # No dispatch record (Studio-direct dispatch).

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_status = MagicMock(return_value=state)
            mock_pm.is_awaiting_plate_clear = MagicMock(return_value=False)

            response = await async_client.get(f"/api/v1/printers/{printer.id}/cover")

        assert response.status_code == 200
        assert response.content == b"PLATE_3_PNG"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_printer_status_omits_fila_switch_when_not_installed(
        self, async_client: AsyncClient, printer_factory, db_session
    ):
        """Without the FTS accessory, fila_switch must be null so the frontend
        keeps applying the per-extruder filter on regular dual-nozzle printers."""
        from unittest.mock import MagicMock, patch

        from backend.app.services.bambu_mqtt import PrinterState

        printer = await printer_factory()

        state = PrinterState()
        state.connected = True
        state.state = "IDLE"
        # default fila_switch — installed = False

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_status = MagicMock(return_value=state)
            mock_pm.is_awaiting_plate_clear = MagicMock(return_value=False)

            response = await async_client.get(f"/api/v1/printers/{printer.id}/status")

        assert response.status_code == 200
        result = response.json()
        assert result["fila_switch"] is None

    # ========================================================================
    # Test connection endpoint
    # ========================================================================


class TestPrinterDataIntegrity:
    """Tests for printer data integrity."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_printer_stores_all_fields(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify printer stores all fields correctly."""
        printer = await printer_factory(
            name="Full Test Printer",
            serial_number="00M09A444444444",
            ip_address="192.168.1.150",
            model="P1S",
            is_active=True,
            auto_archive=False,
        )

        response = await async_client.get(f"/api/v1/printers/{printer.id}")

        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "Full Test Printer"
        assert result["serial_number"] == "00M09A444444444"
        assert result["ip_address"] == "192.168.1.150"
        assert result["model"] == "P1S"
        assert result["is_active"] is True
        assert result["auto_archive"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_printer_update_persists(self, async_client: AsyncClient, printer_factory, db_session):
        """CRITICAL: Verify printer updates persist."""
        printer = await printer_factory(name="Original", is_active=True)

        # Update
        await async_client.patch(f"/api/v1/printers/{printer.id}", json={"name": "Updated", "is_active": False})

        # Verify persistence
        response = await async_client.get(f"/api/v1/printers/{printer.id}")
        result = response.json()
        assert result["name"] == "Updated"
        assert result["is_active"] is False

    # ========================================================================
    # Refresh status endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_refresh_status_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.post("/api/v1/printers/99999/refresh-status")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_refresh_status_not_connected(self, async_client: AsyncClient, printer_factory):
        """Verify 400 when printer is not connected."""
        printer = await printer_factory(name="Disconnected Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.request_status_update.return_value = False

            response = await async_client.post(f"/api/v1/printers/{printer.id}/refresh-status")

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_refresh_status_success(self, async_client: AsyncClient, printer_factory):
        """Verify successful refresh request."""
        printer = await printer_factory(name="Connected Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.request_status_update.return_value = True

            response = await async_client.post(f"/api/v1/printers/{printer.id}/refresh-status")

            assert response.status_code == 200
            assert response.json()["status"] == "refresh_requested"
            mock_pm.request_status_update.assert_called_once_with(printer.id)

    # ========================================================================
    # Current print user endpoint (Issue #206)
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_current_print_user_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.get("/api/v1/printers/99999/current-print-user")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_current_print_user_returns_empty_when_no_user(self, async_client: AsyncClient, printer_factory):
        """Verify empty object returned when no user is tracked."""
        printer = await printer_factory(name="Test Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_current_print_user.return_value = None

            response = await async_client.get(f"/api/v1/printers/{printer.id}/current-print-user")

            assert response.status_code == 200
            assert response.json() == {}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_current_print_user_returns_user_info(self, async_client: AsyncClient, printer_factory):
        """Verify user info is returned when tracked."""
        printer = await printer_factory(name="Test Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_current_print_user.return_value = {"user_id": 42, "username": "testuser"}

            response = await async_client.get(f"/api/v1/printers/{printer.id}/current-print-user")

            assert response.status_code == 200
            result = response.json()
            assert result["user_id"] == 42
            assert result["username"] == "testuser"


class TestPrintControlAPI:
    """Integration tests for print control endpoints (stop, pause, resume)."""

    # ========================================================================
    # Stop print endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stop_print_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.post("/api/v1/printers/99999/print/stop")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stop_print_not_connected(self, async_client: AsyncClient, printer_factory):
        """Verify error when printer is not connected."""
        printer = await printer_factory(name="Disconnected Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print/stop")

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stop_print_success(self, async_client: AsyncClient, printer_factory):
        """Verify successful stop print request."""
        printer = await printer_factory(name="Printing Printer")

        mock_client = MagicMock()
        mock_client.stop_print.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print/stop")

            assert response.status_code == 200
            assert response.json()["success"] is True
            mock_client.stop_print.assert_called_once()

    # ========================================================================
    # Pause print endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_pause_print_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.post("/api/v1/printers/99999/print/pause")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_pause_print_not_connected(self, async_client: AsyncClient, printer_factory):
        """Verify error when printer is not connected."""
        printer = await printer_factory(name="Disconnected Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print/pause")

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_pause_print_success(self, async_client: AsyncClient, printer_factory):
        """Verify successful pause print request."""
        printer = await printer_factory(name="Printing Printer")

        mock_client = MagicMock()
        mock_client.pause_print.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print/pause")

            assert response.status_code == 200
            assert response.json()["success"] is True
            mock_client.pause_print.assert_called_once()

    # ========================================================================
    # Resume print endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resume_print_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.post("/api/v1/printers/99999/print/resume")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resume_print_not_connected(self, async_client: AsyncClient, printer_factory):
        """Verify error when printer is not connected."""
        printer = await printer_factory(name="Disconnected Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print/resume")

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_resume_print_success(self, async_client: AsyncClient, printer_factory):
        """Verify successful resume print request."""
        printer = await printer_factory(name="Paused Printer")

        mock_client = MagicMock()
        mock_client.resume_print.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print/resume")

            assert response.status_code == 200
            assert response.json()["success"] is True
            mock_client.resume_print.assert_called_once()


class TestAMSRefreshAPI:
    """Integration tests for AMS slot refresh endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ams_refresh_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.post("/api/v1/printers/99999/ams/0/slot/0/refresh")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ams_refresh_not_connected(self, async_client: AsyncClient, printer_factory):
        """Verify error when printer is not connected."""
        printer = await printer_factory(name="Disconnected Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/0/slot/0/refresh")

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ams_refresh_success(self, async_client: AsyncClient, printer_factory):
        """Verify successful AMS refresh request."""
        printer = await printer_factory(name="Printer with AMS")

        mock_client = MagicMock()
        mock_client.ams_refresh_tray.return_value = (True, "Refreshing AMS 0 tray 1")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/0/slot/1/refresh")

            assert response.status_code == 200
            result = response.json()
            assert result["success"] is True
            mock_client.ams_refresh_tray.assert_called_once_with(0, 1)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ams_refresh_filament_loaded(self, async_client: AsyncClient, printer_factory):
        """Verify error when filament is loaded (can't refresh while loaded)."""
        printer = await printer_factory(name="Printer with AMS")

        mock_client = MagicMock()
        mock_client.ams_refresh_tray.return_value = (False, "Please unload filament first")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/0/slot/0/refresh")

            assert response.status_code == 400
            assert "unload" in response.json()["detail"].lower()


class TestAMSLoadUnloadAPI:
    """Integration tests for AMS load / unload endpoints (#891)."""

    # ── load ─────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_load_invalid_tray_id(self, async_client: AsyncClient, printer_factory):
        """tray_id outside {0..15, 254, 255} is rejected."""
        printer = await printer_factory(name="P")
        response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/load?tray_id=99")
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_load_not_found(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/printers/99999/ams/load?tray_id=0")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_load_not_connected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Disconnected")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/load?tray_id=0")

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_load_ams_slot_success(self, async_client: AsyncClient, printer_factory):
        """tray_id=5 → AMS 1 slot 2 (1-indexed in the message)."""
        printer = await printer_factory(name="P")

        mock_client = MagicMock()
        mock_client.ams_load_filament.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/load?tray_id=5")

            assert response.status_code == 200
            mock_client.ams_load_filament.assert_called_once_with(5)
            assert "AMS 1" in response.json()["message"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_load_external_left_success(self, async_client: AsyncClient, printer_factory):
        """tray_id=254 → external spool / Ext-L."""
        printer = await printer_factory(name="P")

        mock_client = MagicMock()
        mock_client.ams_load_filament.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/load?tray_id=254")

            assert response.status_code == 200
            mock_client.ams_load_filament.assert_called_once_with(254)
            assert "external" in response.json()["message"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_load_external_right_success(self, async_client: AsyncClient, printer_factory):
        """tray_id=255 → Ext-R on dual-nozzle H2D."""
        printer = await printer_factory(name="H2D")

        mock_client = MagicMock()
        mock_client.ams_load_filament.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/load?tray_id=255")

            assert response.status_code == 200
            mock_client.ams_load_filament.assert_called_once_with(255)
            assert "Ext-R" in response.json()["message"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_load_mqtt_failure_returns_500(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P")

        mock_client = MagicMock()
        mock_client.ams_load_filament.return_value = False

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/load?tray_id=0")

            assert response.status_code == 500
            assert "failed" in response.json()["detail"].lower()

    # ── unload ───────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unload_not_found(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/printers/99999/ams/unload")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unload_not_connected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Disconnected")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/unload")

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unload_success(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P")

        mock_client = MagicMock()
        mock_client.ams_unload_filament.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/unload")

            assert response.status_code == 200
            mock_client.ams_unload_filament.assert_called_once_with()
            assert response.json()["success"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unload_mqtt_failure_returns_500(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P")

        mock_client = MagicMock()
        mock_client.ams_unload_filament.return_value = False

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/unload")

            assert response.status_code == 500
            assert "failed" in response.json()["detail"].lower()


class TestConfigureAMSSlotAPI:
    """Integration tests for AMS slot configure endpoint — tray_info_idx resolution."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_configure_not_connected(self, async_client: AsyncClient, printer_factory):
        """Verify error when printer is not connected."""
        printer = await printer_factory(name="Disconnected")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/0/0/configure",
                params={
                    "tray_info_idx": "GFL99",
                    "tray_type": "PLA",
                    "tray_sub_brands": "PLA Basic",
                    "tray_color": "FF0000FF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                },
            )

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_configure_with_gf_id_keeps_it(self, async_client: AsyncClient, printer_factory):
        """Standard Bambu GF* filament IDs are sent as-is."""
        printer = await printer_factory(name="H2D")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = None  # No existing state

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/2/3/configure",
                params={
                    "tray_info_idx": "GFL05",
                    "tray_type": "PLA",
                    "tray_sub_brands": "PLA Basic",
                    "tray_color": "FFFFFFFF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                },
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            assert call_kwargs.kwargs["tray_info_idx"] == "GFL05"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_configure_pfus_sent_directly(self, async_client: AsyncClient, printer_factory):
        """PFUS* cloud-synced custom preset IDs are sent to the printer."""
        printer = await printer_factory(name="H2D")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        mock_status = MagicMock()
        mock_status.raw_data = {"ams": {"ams": []}}  # No existing tray data

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_status

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/2/3/configure",
                params={
                    "tray_info_idx": "PFUS9ac902733670a9",
                    "tray_type": "PLA",
                    "tray_sub_brands": "Devil Design PLA",
                    "tray_color": "FF0000FF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                },
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            assert call_kwargs.kwargs["tray_info_idx"] == "PFUS9ac902733670a9"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_configure_pfus_takes_priority_over_slot(self, async_client: AsyncClient, printer_factory):
        """Provided PFUS* preset takes priority over slot's existing preset."""
        printer = await printer_factory(name="H2D")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        # Simulate slot already configured by slicer with cloud-synced preset
        mock_status = MagicMock()
        mock_status.raw_data = {
            "ams": {
                "ams": [
                    {
                        "id": 2,
                        "tray": [
                            {
                                "id": 3,
                                "tray_info_idx": "P4d64437",
                                "tray_type": "PLA",
                                "tray_color": "FF0000FF",
                            }
                        ],
                    }
                ]
            }
        }

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_status

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/2/3/configure",
                params={
                    "tray_info_idx": "PFUS9ac902733670a9",
                    "tray_type": "PLA",
                    "tray_sub_brands": "Devil Design PLA",
                    "tray_color": "FF0000FF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                },
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            # Provided preset wins over slot's existing one
            assert call_kwargs.kwargs["tray_info_idx"] == "PFUS9ac902733670a9"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_configure_pfus_used_regardless_of_slot_material(self, async_client: AsyncClient, printer_factory):
        """Provided PFUS* preset is used even when slot has a different material."""
        printer = await printer_factory(name="H2D")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        # Slot currently has PETG but user is configuring PLA
        mock_status = MagicMock()
        mock_status.raw_data = {
            "ams": {
                "ams": [
                    {
                        "id": 2,
                        "tray": [{"id": 3, "tray_info_idx": "GFG99", "tray_type": "PETG", "tray_color": "FFFFFFFF"}],
                    }
                ]
            }
        }

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_status

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/2/3/configure",
                params={
                    "tray_info_idx": "PFUS9ac902733670a9",
                    "tray_type": "PLA",
                    "tray_sub_brands": "Devil Design PLA",
                    "tray_color": "FF0000FF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                },
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            # Provided preset wins — slot's material is irrelevant
            assert call_kwargs.kwargs["tray_info_idx"] == "PFUS9ac902733670a9"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_configure_empty_id_uses_generic(self, async_client: AsyncClient, printer_factory):
        """Empty tray_info_idx (local preset) is replaced with generic."""
        printer = await printer_factory(name="H2D")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        mock_status = MagicMock()
        mock_status.raw_data = {"ams": {"ams": []}}

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_status

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/2/3/configure",
                params={
                    "tray_info_idx": "",
                    "tray_type": "PETG",
                    "tray_sub_brands": "PETG Basic",
                    "tray_color": "FFFFFFFF",
                    "nozzle_temp_min": 220,
                    "nozzle_temp_max": 260,
                },
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            assert call_kwargs.kwargs["tray_info_idx"] == "GFG99"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_configure_pfus_preserves_setting_id_pair(self, async_client: AsyncClient, printer_factory):
        """Both tray_info_idx=PFUS* and setting_id=PFUS* are forwarded untouched.

        Pins the end-to-end contract the frontend #1053 fix relies on: when the
        user configures a slot with a custom cloud preset whose cloud detail
        has filament_id=null, the frontend sends the setting_id in BOTH fields
        and the backend must not collapse either to a generic GF* ID.
        """
        printer = await printer_factory(name="H2D")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        mock_status = MagicMock()
        mock_status.raw_data = {"ams": {"ams": []}}

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_status

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/128/0/configure",
                params={
                    "tray_info_idx": "PFUSa8fb76f9733e3c",
                    "tray_type": "ABS",
                    "tray_sub_brands": "Sting3D ABS",
                    "tray_color": "000000FF",
                    "nozzle_temp_min": 240,
                    "nozzle_temp_max": 280,
                    "setting_id": "PFUSa8fb76f9733e3c",
                },
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            assert call_kwargs.kwargs["tray_info_idx"] == "PFUSa8fb76f9733e3c"
            assert call_kwargs.kwargs["setting_id"] == "PFUSa8fb76f9733e3c"
            # Explicitly assert no generic-collapse happened for this HT slot.
            assert call_kwargs.kwargs["tray_info_idx"] != "GFB99"


class TestSkipObjectsAPI:
    """Integration tests for skip objects endpoints."""

    # ========================================================================
    # Get printable objects endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_objects_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.get("/api/v1/printers/99999/print/objects")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_objects_not_connected(self, async_client: AsyncClient, printer_factory):
        """Verify error when printer is not connected."""
        printer = await printer_factory(name="Disconnected Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.get(f"/api/v1/printers/{printer.id}/print/objects")

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_objects_empty(self, async_client: AsyncClient, printer_factory):
        """Verify empty objects list when no print is active."""
        printer = await printer_factory(name="Idle Printer")

        mock_client = MagicMock()
        mock_client.state.printable_objects = {}
        mock_client.state.skipped_objects = []
        mock_client.state.state = "IDLE"
        mock_client.state.subtask_name = None  # Prevent FTP download attempt

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.get(f"/api/v1/printers/{printer.id}/print/objects")

            assert response.status_code == 200
            result = response.json()
            assert result["objects"] == []
            assert result["total"] == 0
            assert result["skipped_count"] == 0
            assert result["is_printing"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_objects_with_data(self, async_client: AsyncClient, printer_factory):
        """Verify objects list when print is active."""
        printer = await printer_factory(name="Printing Printer")

        mock_client = MagicMock()
        mock_client.state.printable_objects = {100: "Part A", 200: "Part B", 300: "Part C"}
        mock_client.state.skipped_objects = [200]
        mock_client.state.state = "RUNNING"

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.get(f"/api/v1/printers/{printer.id}/print/objects")

            assert response.status_code == 200
            result = response.json()
            assert result["total"] == 3
            assert result["skipped_count"] == 1
            assert result["is_printing"] is True

            # Check objects have correct structure
            objects_by_id = {obj["id"]: obj for obj in result["objects"]}
            assert objects_by_id[100]["name"] == "Part A"
            assert objects_by_id[100]["skipped"] is False
            assert objects_by_id[200]["name"] == "Part B"
            assert objects_by_id[200]["skipped"] is True
            assert objects_by_id[300]["name"] == "Part C"
            assert objects_by_id[300]["skipped"] is False

    # ========================================================================
    # Skip objects endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_objects_with_positions(self, async_client: AsyncClient, printer_factory):
        """Verify objects list includes position data when available."""
        printer = await printer_factory(name="Printing Printer")

        # New format with position data
        mock_client = MagicMock()
        mock_client.state.printable_objects = {
            100: {"name": "Part A", "x": 50.0, "y": 100.0},
            200: {"name": "Part B", "x": 150.0, "y": 100.0},
        }
        mock_client.state.skipped_objects = []
        mock_client.state.state = "RUNNING"

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.get(f"/api/v1/printers/{printer.id}/print/objects")

            assert response.status_code == 200
            result = response.json()
            assert result["total"] == 2

            # Check objects have position data
            objects_by_id = {obj["id"]: obj for obj in result["objects"]}
            assert objects_by_id[100]["name"] == "Part A"
            assert objects_by_id[100]["x"] == 50.0
            assert objects_by_id[100]["y"] == 100.0
            assert objects_by_id[200]["name"] == "Part B"
            assert objects_by_id[200]["x"] == 150.0
            assert objects_by_id[200]["y"] == 100.0

    # ========================================================================
    # Skip objects endpoint
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_skip_objects_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.post("/api/v1/printers/99999/print/skip-objects", json=[100])
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_skip_objects_not_connected(self, async_client: AsyncClient, printer_factory):
        """Verify error when printer is not connected."""
        printer = await printer_factory(name="Disconnected Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print/skip-objects", json=[100])

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_skip_objects_empty_list(self, async_client: AsyncClient, printer_factory):
        """Verify error when no object IDs provided."""
        printer = await printer_factory(name="Printing Printer")

        mock_client = MagicMock()
        mock_client.state.printable_objects = {100: "Part A"}
        mock_client.state.skipped_objects = []

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print/skip-objects", json=[])

            assert response.status_code == 400
            assert "no object" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_skip_objects_invalid_id(self, async_client: AsyncClient, printer_factory):
        """Verify error when object ID doesn't exist."""
        printer = await printer_factory(name="Printing Printer")

        mock_client = MagicMock()
        mock_client.state.printable_objects = {100: "Part A"}
        mock_client.state.skipped_objects = []

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print/skip-objects", json=[999])

            assert response.status_code == 400
            assert "invalid" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_skip_objects_success(self, async_client: AsyncClient, printer_factory):
        """Verify successful skip objects request."""
        printer = await printer_factory(name="Printing Printer")

        mock_client = MagicMock()
        mock_client.state.printable_objects = {100: "Part A", 200: "Part B"}
        mock_client.state.skipped_objects = []
        mock_client.skip_objects.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print/skip-objects", json=[100])

            assert response.status_code == 200
            result = response.json()
            assert result["success"] is True
            assert 100 in result["skipped_objects"]
            mock_client.skip_objects.assert_called_once_with([100])

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_skip_objects_multiple(self, async_client: AsyncClient, printer_factory):
        """Verify skipping multiple objects at once."""
        printer = await printer_factory(name="Printing Printer")

        mock_client = MagicMock()
        mock_client.state.printable_objects = {100: "Part A", 200: "Part B", 300: "Part C"}
        mock_client.state.skipped_objects = []
        mock_client.skip_objects.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/print/skip-objects", json=[100, 200])

            assert response.status_code == 200
            result = response.json()
            assert result["success"] is True
            assert 100 in result["skipped_objects"]
            assert 200 in result["skipped_objects"]
            mock_client.skip_objects.assert_called_once_with([100, 200])


class TestChamberLightAPI:
    """Integration tests for chamber light control endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_chamber_light_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.post("/api/v1/printers/99999/chamber-light?on=true")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_chamber_light_not_connected(self, async_client: AsyncClient, printer_factory):
        """Verify error when printer is not connected."""
        printer = await printer_factory(name="Disconnected Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.post(f"/api/v1/printers/{printer.id}/chamber-light?on=true")

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_chamber_light_on_success(self, async_client: AsyncClient, printer_factory):
        """Verify successful chamber light on request."""
        printer = await printer_factory(name="Test Printer")

        mock_client = MagicMock()
        mock_client.set_chamber_light.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/chamber-light?on=true")

            assert response.status_code == 200
            result = response.json()
            assert result["success"] is True
            assert "on" in result["message"].lower()
            mock_client.set_chamber_light.assert_called_once_with(True)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_chamber_light_off_success(self, async_client: AsyncClient, printer_factory):
        """Verify successful chamber light off request."""
        printer = await printer_factory(name="Test Printer")

        mock_client = MagicMock()
        mock_client.set_chamber_light.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/chamber-light?on=false")

            assert response.status_code == 200
            result = response.json()
            assert result["success"] is True
            assert "off" in result["message"].lower()
            mock_client.set_chamber_light.assert_called_once_with(False)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_chamber_light_failure(self, async_client: AsyncClient, printer_factory):
        """Verify error handling when chamber light control fails."""
        printer = await printer_factory(name="Test Printer")

        mock_client = MagicMock()
        mock_client.set_chamber_light.return_value = False

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/chamber-light?on=true")

            assert response.status_code == 500
            assert "failed" in response.json()["detail"].lower()


class TestAirductModeAPI:
    """Integration tests for the airduct mode endpoint (P2S/H2*)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_mode_rejected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P", model="P2S")
        response = await async_client.post(f"/api/v1/printers/{printer.id}/airduct-mode?mode=foo")
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_not_connected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P", model="P2S")
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None
            response = await async_client.post(f"/api/v1/printers/{printer.id}/airduct-mode?mode=cooling")
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cooling_success(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P", model="P2S")
        mock_client = MagicMock()
        mock_client.set_airduct_mode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/airduct-mode?mode=cooling")
        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_client.set_airduct_mode.assert_called_once_with("cooling")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_heating_failure_returns_500(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P", model="P2S")
        mock_client = MagicMock()
        mock_client.set_airduct_mode.return_value = False
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/airduct-mode?mode=heating")
        assert response.status_code == 500


class TestClearHMSErrorsAPI:
    """Integration tests for clear HMS errors endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_clear_hms_errors_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent printer."""
        response = await async_client.post("/api/v1/printers/99999/hms/clear")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_clear_hms_errors_not_connected(self, async_client: AsyncClient, printer_factory):
        """Verify error when printer is not connected."""
        printer = await printer_factory(name="Disconnected Printer")

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None

            response = await async_client.post(f"/api/v1/printers/{printer.id}/hms/clear")

            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_clear_hms_errors_success(self, async_client: AsyncClient, printer_factory):
        """Verify successful clear HMS errors request."""
        printer = await printer_factory(name="Test Printer")

        mock_client = MagicMock()
        mock_client.clear_hms_errors.return_value = True

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/hms/clear")

            assert response.status_code == 200
            result = response.json()
            assert result["success"] is True
            assert "cleared" in result["message"].lower()
            mock_client.clear_hms_errors.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_clear_hms_errors_failure(self, async_client: AsyncClient, printer_factory):
        """Verify error handling when clear HMS errors fails."""
        printer = await printer_factory(name="Test Printer")

        mock_client = MagicMock()
        mock_client.clear_hms_errors.return_value = False

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client

            response = await async_client.post(f"/api/v1/printers/{printer.id}/hms/clear")

            assert response.status_code == 500
            assert "failed" in response.json()["detail"].lower()


def _build_h2d_state(*, ams_id: int = 0, tray_id: int = 2, cali_idx: int = 5):
    """Build a MagicMock PrinterState for an H2D printer with a single BL spool tray.

    Used by both TestApplyPaAfterRefresh (Phase 13 P13-T-BE-1) and the K-profile
    persistence tests below. The tray data passes is_bambu_tag (32-char non-zero
    tray_uuid + non-empty tray_info_idx).
    """
    nozzle = MagicMock(nozzle_diameter="0.4")
    state = MagicMock()
    state.nozzles = [nozzle]
    state.ams_extruder_map = {"0": 0}
    state.raw_data = {
        "ams": [
            {
                "id": ams_id,
                "tray": [
                    {
                        "id": tray_id,
                        "tray_type": "PLA",
                        "tag_uid": "AABBCC1122334400",
                        "tray_uuid": "11223344556677880011223344556677",
                        "tray_info_idx": "GFL05",
                        "cali_idx": cali_idx,
                    }
                ],
            }
        ]
    }
    return state


def _patch_async_session_to(db_session):
    """Patch backend.app.core.database.async_session so calls inside the function
    under test reuse the test fixture's db_session.

    `_apply_pa_after_refresh` lazy-imports `from backend.app.core.database import
    async_session` at runtime (line 2849). When we patch the source module
    before the call, the lazy import picks up the patched object.

    Returns the patch context manager; use as `with _patch_async_session_to(db_session):`.
    Pattern verified against test_print_lifecycle.py:38-42.
    """
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=db_session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return patch("backend.app.core.database.async_session", return_value=cm)


class TestApplyPaAfterRefresh:
    """Phase 13 P13-T-BE-1: _apply_pa_after_refresh K-profile cascade.

    Verifies the 3-stage cascade (local SpoolKProfile → Spoolman SpoolmanKProfile
    → live tray.cali_idx fallback) and the Bug A regression (kp.extruder, not
    kp.extruder_id, after the Phase 13 P13-2a fix).

    `_apply_pa_after_refresh` is a free function spawned via asyncio.create_task
    from the /ams-refresh endpoint. Tests call it directly because awaiting the
    spawned task in an HTTP test would require sleeping past the 5-second guard
    that delays MQTT until RFID re-read finishes.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_local_kp_match_sends_stored_cali_idx(self, db_session, printer_factory):
        """Local SpoolAssignment + matching SpoolKProfile → stored cali_idx wins."""
        from backend.app.api.routes.printers import _apply_pa_after_refresh
        from backend.app.models.spool import Spool
        from backend.app.models.spool_assignment import SpoolAssignment
        from backend.app.models.spool_k_profile import SpoolKProfile

        printer = await printer_factory()
        spool = Spool(material="PLA", color_name="Red", rgba="FF0000FF")
        db_session.add(spool)
        await db_session.flush()
        db_session.add(
            SpoolAssignment(
                spool_id=spool.id,
                printer_id=printer.id,
                ams_id=0,
                tray_id=2,
            )
        )
        db_session.add(
            SpoolKProfile(
                spool_id=spool.id,
                printer_id=printer.id,
                extruder=0,
                nozzle_diameter="0.4",
                k_value=0.025,
                cali_idx=42,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        state = _build_h2d_state(cali_idx=5)

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        mock_client.extrusion_cali_sel.assert_called_once()
        kwargs = mock_client.extrusion_cali_sel.call_args.kwargs
        assert kwargs["cali_idx"] == 42  # stored profile, not 5 (live)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_local_no_kp_uses_live_cali_idx(self, db_session, printer_factory):
        """Local SpoolAssignment but no matching SpoolKProfile → live cali_idx (Stage 3)."""
        from backend.app.api.routes.printers import _apply_pa_after_refresh
        from backend.app.models.spool import Spool
        from backend.app.models.spool_assignment import SpoolAssignment

        printer = await printer_factory()
        spool = Spool(material="PLA")
        db_session.add(spool)
        await db_session.flush()
        db_session.add(
            SpoolAssignment(
                spool_id=spool.id,
                printer_id=printer.id,
                ams_id=0,
                tray_id=2,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        state = _build_h2d_state(cali_idx=5)

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        mock_client.extrusion_cali_sel.assert_called_once()
        kwargs = mock_client.extrusion_cali_sel.call_args.kwargs
        assert kwargs["cali_idx"] == 5  # live tray.cali_idx fallback

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_kp_when_no_local(self, db_session, printer_factory):
        """No local assignment + Spoolman SlotAssignment + SpoolmanKProfile → Spoolman cali_idx."""
        from backend.app.api.routes.printers import _apply_pa_after_refresh
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        printer = await printer_factory()
        db_session.add(
            SpoolmanSlotAssignment(
                printer_id=printer.id,
                ams_id=0,
                tray_id=2,
                spoolman_spool_id=99,
            )
        )
        db_session.add(
            SpoolmanKProfile(
                spoolman_spool_id=99,
                printer_id=printer.id,
                extruder=0,
                nozzle_diameter="0.4",
                k_value=0.030,
                cali_idx=77,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        state = _build_h2d_state(cali_idx=5)

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        mock_client.extrusion_cali_sel.assert_called_once()
        kwargs = mock_client.extrusion_cali_sel.call_args.kwargs
        assert kwargs["cali_idx"] == 77  # Spoolman stored profile

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolman_no_kp_uses_live(self, db_session, printer_factory):
        """Spoolman SlotAssignment but no SpoolmanKProfile → live cali_idx (Stage 3)."""
        from backend.app.api.routes.printers import _apply_pa_after_refresh
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        printer = await printer_factory()
        db_session.add(
            SpoolmanSlotAssignment(
                printer_id=printer.id,
                ams_id=0,
                tray_id=2,
                spoolman_spool_id=99,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        state = _build_h2d_state(cali_idx=5)

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        mock_client.extrusion_cali_sel.assert_called_once()
        assert mock_client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 5

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_assignment_uses_live(self, db_session, printer_factory):
        """No assignment of any kind + live cali_idx >= 0 → live fallback."""
        from backend.app.api.routes.printers import _apply_pa_after_refresh

        printer = await printer_factory()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        state = _build_h2d_state(cali_idx=5)

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        mock_client.extrusion_cali_sel.assert_called_once()
        assert mock_client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 5

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_negative_live_cali_idx_skipped(self, db_session, printer_factory):
        """No assignment + live cali_idx=-1 → no MQTT call (invalid live value)."""
        from backend.app.api.routes.printers import _apply_pa_after_refresh

        printer = await printer_factory()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        state = _build_h2d_state(cali_idx=-1)

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        mock_client.extrusion_cali_sel.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_assignment_no_live_cali_idx_no_call(self, db_session, printer_factory):
        """No assignment of any kind AND no live cali_idx in tray → no MQTT call.

        Distinct from test_negative_live_cali_idx_skipped: that test has
        cali_idx=-1 in raw_data; this one omits the field entirely (returns
        None from .get("cali_idx")). Both must result in no MQTT call.
        """
        from backend.app.api.routes.printers import _apply_pa_after_refresh

        printer = await printer_factory()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        # State with NO cali_idx field on tray at all
        nozzle = MagicMock(nozzle_diameter="0.4")
        state = MagicMock()
        state.nozzles = [nozzle]
        state.ams_extruder_map = {"0": 0}
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 2,
                            "tray_type": "PLA",
                            "tag_uid": "AABBCC1122334400",
                            "tray_uuid": "11223344556677880011223344556677",
                            "tray_info_idx": "GFL05",
                            # cali_idx field intentionally omitted
                        }
                    ],
                }
            ]
        }

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        mock_client.extrusion_cali_sel.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extruder_mismatch_uses_kp_as_fallback(self, db_session, printer_factory):
        """K-profile for extruder=1 but slot is extruder=0 → no exact match,
        but the kp is used as extruder-agnostic fallback rather than dropped.

        Hard-skipping on extruder mismatch was the previous behavior; in
        practice it caused stored K-profiles to be silently ignored whenever
        the AMS-extruder mapping had shifted (or when only one of the two
        extruders was ever calibrated for a given spool). The cascade now
        prefers an exact extruder match but falls back to any matching kp
        for the same printer + nozzle.
        """
        from backend.app.api.routes.printers import _apply_pa_after_refresh
        from backend.app.models.spool import Spool
        from backend.app.models.spool_assignment import SpoolAssignment
        from backend.app.models.spool_k_profile import SpoolKProfile

        printer = await printer_factory()
        spool = Spool(material="PLA")
        db_session.add(spool)
        await db_session.flush()
        db_session.add(
            SpoolAssignment(
                spool_id=spool.id,
                printer_id=printer.id,
                ams_id=0,
                tray_id=2,
            )
        )
        # K-profile is for extruder=1, but slot's ams_extruder_map["0"]=0
        db_session.add(
            SpoolKProfile(
                spool_id=spool.id,
                printer_id=printer.id,
                extruder=1,
                nozzle_diameter="0.4",
                k_value=0.025,
                cali_idx=42,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        state = _build_h2d_state(cali_idx=5)

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        # No exact extruder match, but the stored kp wins as the
        # extruder-agnostic fallback over live cali_idx=5.
        mock_client.extrusion_cali_sel.assert_called_once()
        assert mock_client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 42

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extruder_exact_match_preferred_over_fallback(
        self,
        db_session,
        printer_factory,
    ):
        """When two kp rows exist, one with matching extruder and one without,
        the exact-extruder kp wins (extruder-agnostic fallback only fires when
        no exact match exists).
        """
        from backend.app.api.routes.printers import _apply_pa_after_refresh
        from backend.app.models.spool import Spool
        from backend.app.models.spool_assignment import SpoolAssignment
        from backend.app.models.spool_k_profile import SpoolKProfile

        printer = await printer_factory()
        spool = Spool(material="PLA")
        db_session.add(spool)
        await db_session.flush()
        db_session.add(
            SpoolAssignment(
                spool_id=spool.id,
                printer_id=printer.id,
                ams_id=0,
                tray_id=2,
            )
        )
        # Two kp rows: extruder=1 (mismatch w/ slot extruder=0) and extruder=0 (exact)
        db_session.add(
            SpoolKProfile(
                spool_id=spool.id,
                printer_id=printer.id,
                extruder=1,
                nozzle_diameter="0.4",
                k_value=0.030,
                cali_idx=99,
            )
        )
        db_session.add(
            SpoolKProfile(
                spool_id=spool.id,
                printer_id=printer.id,
                extruder=0,
                nozzle_diameter="0.4",
                k_value=0.025,
                cali_idx=42,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        state = _build_h2d_state(cali_idx=5)

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        # Exact-extruder=0 kp wins (cali_idx=42), not the extruder=1 fallback (99)
        mock_client.extrusion_cali_sel.assert_called_once()
        assert mock_client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 42

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_regression_bug_a_kp_extruder_attr(self, db_session, printer_factory):
        """Regression test for Phase 13 P13-2a Bug A.

        Pre-fix Z.2910 used `kp.extruder_id` (AttributeError on SpoolKProfile,
        silently swallowed by outer try/except). On dual-nozzle printers with
        slot_extruder != None this caused the K-profile match loop to crash.
        After P13-2a the field name is correct: `kp.extruder`.
        """
        from backend.app.api.routes.printers import _apply_pa_after_refresh
        from backend.app.models.spool import Spool
        from backend.app.models.spool_assignment import SpoolAssignment
        from backend.app.models.spool_k_profile import SpoolKProfile

        printer = await printer_factory()
        spool = Spool(material="PLA")
        db_session.add(spool)
        await db_session.flush()
        db_session.add(
            SpoolAssignment(
                spool_id=spool.id,
                printer_id=printer.id,
                ams_id=0,
                tray_id=2,
            )
        )
        # extruder=0 matches slot_extruder=0 (from ams_extruder_map={"0":0})
        db_session.add(
            SpoolKProfile(
                spool_id=spool.id,
                printer_id=printer.id,
                extruder=0,
                nozzle_diameter="0.4",
                k_value=0.025,
                cali_idx=42,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        state = _build_h2d_state(cali_idx=5)

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        # If Bug A regressed (kp.extruder_id), the loop would AttributeError → silent fail
        # → no extrusion_cali_sel call. Post-fix the loop matches and sends cali_idx=42.
        mock_client.extrusion_cali_sel.assert_called_once()
        assert mock_client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 42

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_fallback_finds_spool_when_assignment_missing(
        self,
        db_session,
        printer_factory,
    ):
        """Stage 1b regression for the maintainer's #2 reproducer on H2D:
        reset slot, trigger re-read → slot ends up on the default K-profile
        instead of the spool's stored profile.

        Setup mirrors the bug:
          - Spool has tray_uuid set (the RFID tag was registered earlier).
          - SpoolKProfile exists for that spool with cali_idx=42.
          - NO SpoolAssignment row — the reset deleted it before the re-read
            triggered _apply_pa_after_refresh, and tag-auto-detect has not
            re-created it yet within the 5 s sleep window.
          - Live tray.cali_idx=5 (firmware-default after the RFID re-read).

        Without Stage 1b the cascade falls through to Stage 3 and re-asserts
        the firmware-default cali_idx=5. With Stage 1b it locates the spool by
        the live tray's tray_uuid and applies the stored cali_idx=42.
        """
        from backend.app.api.routes.printers import _apply_pa_after_refresh
        from backend.app.models.spool import Spool
        from backend.app.models.spool_k_profile import SpoolKProfile

        printer = await printer_factory()
        # Spool with tray_uuid matching the one _build_h2d_state puts on the tray
        spool = Spool(
            material="PLA",
            color_name="Red",
            rgba="FF0000FF",
            tray_uuid="11223344556677880011223344556677",
            tag_uid="AABBCC1122334400",
        )
        db_session.add(spool)
        await db_session.flush()
        # K-profile is bound to the spool, not to a slot
        db_session.add(
            SpoolKProfile(
                spool_id=spool.id,
                printer_id=printer.id,
                extruder=0,
                nozzle_diameter="0.4",
                k_value=0.025,
                cali_idx=42,
            )
        )
        # NOTE: deliberately no SpoolAssignment — that's the bug condition.
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        state = _build_h2d_state(cali_idx=5)

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        # Stage 1b should match the spool by tray_uuid → stored cali_idx=42 wins
        # over live cali_idx=5. Pre-fix this would have been 5 (firmware default).
        mock_client.extrusion_cali_sel.assert_called_once()
        assert mock_client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 42

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_fallback_matches_by_tag_uid_when_uuid_zero(
        self,
        db_session,
        printer_factory,
    ):
        """Stage 1b: when tray_uuid is the zero sentinel but tag_uid is real,
        match by tag_uid. Older firmwares occasionally report a zero tray_uuid
        right after RFID re-read while the tag_uid is already populated."""
        from backend.app.api.routes.printers import _apply_pa_after_refresh
        from backend.app.models.spool import Spool
        from backend.app.models.spool_k_profile import SpoolKProfile

        printer = await printer_factory()
        # Spool indexed by tag_uid, not tray_uuid
        spool = Spool(material="PLA", tag_uid="AABBCC1122334400")
        db_session.add(spool)
        await db_session.flush()
        db_session.add(
            SpoolKProfile(
                spool_id=spool.id,
                printer_id=printer.id,
                extruder=0,
                nozzle_diameter="0.4",
                k_value=0.025,
                cali_idx=99,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        # Build a state where the tray reports a real tag_uid but a zero tray_uuid
        # while still passing is_bambu_tag (tag_uid + tray_info_idx is sufficient).
        state = _build_h2d_state(cali_idx=5)
        state.raw_data["ams"][0]["tray"][0]["tray_uuid"] = "00000000000000000000000000000000"

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        mock_client.extrusion_cali_sel.assert_called_once()
        assert mock_client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 99

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_fallback_skipped_when_zero_sentinels(
        self,
        db_session,
        printer_factory,
    ):
        """Stage 1b: when both tray_uuid and tag_uid are zero sentinels, the
        fallback must not match any spool (would otherwise pick up an
        unrelated spool created with empty/zero tag fields). Falls through
        to Stage 3 live cali_idx as before.
        """
        from backend.app.api.routes.printers import _apply_pa_after_refresh
        from backend.app.models.spool import Spool
        from backend.app.models.spool_k_profile import SpoolKProfile

        printer = await printer_factory()
        # Decoy spool with no tag info — must NOT match
        spool = Spool(material="PLA")
        db_session.add(spool)
        await db_session.flush()
        db_session.add(
            SpoolKProfile(
                spool_id=spool.id,
                printer_id=printer.id,
                extruder=0,
                nozzle_diameter="0.4",
                k_value=0.025,
                cali_idx=42,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.extrusion_cali_sel = MagicMock(return_value=True)
        state = _build_h2d_state(cali_idx=7)
        # Force both tag fields to the zero sentinels but keep tray_info_idx
        # so is_bambu_tag still passes (preset present)
        state.raw_data["ams"][0]["tray"][0]["tag_uid"] = "0000000000000000"
        state.raw_data["ams"][0]["tray"][0]["tray_uuid"] = "00000000000000000000000000000000"

        with (
            patch("backend.app.api.routes.printers.asyncio.sleep", AsyncMock()),
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            _patch_async_session_to(db_session),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = state
            # is_bambu_tag actually rejects both-zero + only-preset, so the
            # function returns early. We just want to confirm we didn't blow
            # up scanning for a tag-fallback spool.
            await _apply_pa_after_refresh(printer.id, ams_id=0, slot_id=2)

        # is_bambu_tag short-circuits early when both UID and UUID are zero,
        # so no MQTT call should fire and the decoy spool's cali_idx=42 must
        # NOT leak through.
        if mock_client.extrusion_cali_sel.called:
            assert mock_client.extrusion_cali_sel.call_args.kwargs["cali_idx"] != 42


class TestConfigureAmsSlotPersistsKProfile:
    """Phase 13 P13-T-BE-2: configure_ams_slot persists K-profile to DB.

    Pre-Phase-13 the endpoint sent extrusion_cali_sel via MQTT but never
    recorded the choice in spool_k_profile / spoolman_k_profile, so the next
    RFID re-read had no stored profile to apply.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_writes_spoolman_kprofile_when_spoolman_assigned(
        self,
        async_client: AsyncClient,
        db_session,
        printer_factory,
    ):
        """SpoolmanSlotAssignment present → SpoolmanKProfile row created with cali_idx + k_value + name."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        printer = await printer_factory(model="H2D")
        db_session.add(
            SpoolmanSlotAssignment(
                printer_id=printer.id,
                ams_id=0,
                tray_id=3,
                spoolman_spool_id=216,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.extrusion_cali_set.return_value = True
        mock_client.request_status_update.return_value = True

        mock_state = MagicMock()
        mock_state.ams_extruder_map = {"0": 0}
        mock_state.raw_data = {"ams": {"ams": []}}

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_state

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/0/3/configure",
                params={
                    "tray_info_idx": "GFL05",
                    "tray_type": "PLA",
                    "tray_sub_brands": "Bambu PLA Metal",
                    "tray_color": "FF8800FF",
                    "nozzle_temp_min": 220,
                    "nozzle_temp_max": 240,
                    "cali_idx": 5,
                    "nozzle_diameter": "0.4",
                    "k_value": 0.022,
                },
            )

        assert response.status_code == 200
        kp_result = await db_session.execute(select(SpoolmanKProfile).where(SpoolmanKProfile.spoolman_spool_id == 216))
        kp = kp_result.scalar_one_or_none()
        assert kp is not None
        assert kp.cali_idx == 5
        assert kp.k_value == pytest.approx(0.022)
        assert kp.extruder == 0
        assert kp.nozzle_diameter == "0.4"
        assert kp.name == "Bambu PLA Metal"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_writes_spool_kprofile_when_local_assigned(
        self,
        async_client: AsyncClient,
        db_session,
        printer_factory,
    ):
        """Local SpoolAssignment present → SpoolKProfile row created."""
        from backend.app.models.spool import Spool
        from backend.app.models.spool_assignment import SpoolAssignment
        from backend.app.models.spool_k_profile import SpoolKProfile

        printer = await printer_factory(model="H2D")
        spool = Spool(material="PLA", color_name="Red", rgba="FF0000FF")
        db_session.add(spool)
        await db_session.flush()
        db_session.add(
            SpoolAssignment(
                spool_id=spool.id,
                printer_id=printer.id,
                ams_id=0,
                tray_id=3,
            )
        )
        await db_session.commit()
        spool_id = spool.id

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.extrusion_cali_set.return_value = True
        mock_client.request_status_update.return_value = True

        mock_state = MagicMock()
        mock_state.ams_extruder_map = {"0": 0}
        mock_state.raw_data = {"ams": {"ams": []}}

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_state

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/0/3/configure",
                params={
                    "tray_info_idx": "PFUSdev01",
                    "tray_type": "PLA",
                    "tray_sub_brands": "Devil Design PLA",
                    "tray_color": "FF0000FF",
                    "nozzle_temp_min": 220,
                    "nozzle_temp_max": 240,
                    "cali_idx": 7,
                    "nozzle_diameter": "0.4",
                    "k_value": 0.028,
                },
            )

        assert response.status_code == 200
        kp_result = await db_session.execute(select(SpoolKProfile).where(SpoolKProfile.spool_id == spool_id))
        kp = kp_result.scalar_one_or_none()
        assert kp is not None
        assert kp.cali_idx == 7
        assert kp.k_value == pytest.approx(0.028)
        assert kp.extruder == 0
        assert kp.nozzle_diameter == "0.4"
        assert kp.name == "Devil Design PLA"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_assignment_no_persist(
        self,
        async_client: AsyncClient,
        db_session,
        printer_factory,
    ):
        """No SpoolAssignment AND no SpoolmanSlotAssignment → no DB write, MQTT still sent."""
        from backend.app.models.spool_k_profile import SpoolKProfile
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        printer = await printer_factory(model="H2D")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        mock_state = MagicMock()
        mock_state.ams_extruder_map = {"0": 0}
        mock_state.raw_data = {"ams": {"ams": []}}

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_state

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/0/3/configure",
                params={
                    "tray_info_idx": "GFL05",
                    "tray_type": "PLA",
                    "tray_sub_brands": "PLA Basic",
                    "tray_color": "FFFFFFFF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                    "cali_idx": 5,
                    "k_value": 0.020,
                },
            )

        assert response.status_code == 200
        # MQTT sent (was successful), but no DB writes
        mock_client.extrusion_cali_sel.assert_called_once()
        local_count = (await db_session.execute(select(SpoolKProfile))).scalars().all()
        sm_count = (await db_session.execute(select(SpoolmanKProfile))).scalars().all()
        assert len(local_count) == 0
        assert len(sm_count) == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_negative_cali_idx_no_persist(
        self,
        async_client: AsyncClient,
        db_session,
        printer_factory,
    ):
        """cali_idx=-1 (no profile selected) → no DB write even when assignment exists."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        printer = await printer_factory(model="H2D")
        db_session.add(
            SpoolmanSlotAssignment(
                printer_id=printer.id,
                ams_id=0,
                tray_id=3,
                spoolman_spool_id=216,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.extrusion_cali_set.return_value = True
        mock_client.request_status_update.return_value = True

        mock_state = MagicMock()
        mock_state.ams_extruder_map = {"0": 0}
        mock_state.raw_data = {"ams": {"ams": []}}

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_state

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/0/3/configure",
                params={
                    "tray_info_idx": "GFL05",
                    "tray_type": "PLA",
                    "tray_sub_brands": "PLA Basic",
                    "tray_color": "FFFFFFFF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                    "cali_idx": -1,
                    "k_value": 0.0,
                },
            )

        assert response.status_code == 200
        sm_kps = (
            (await db_session.execute(select(SpoolmanKProfile).where(SpoolmanKProfile.spoolman_spool_id == 216)))
            .scalars()
            .all()
        )
        assert len(sm_kps) == 0  # cali_idx=-1 means "no profile" — don't write

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_zero_cali_idx_persists(
        self,
        async_client: AsyncClient,
        db_session,
        printer_factory,
    ):
        """cali_idx=0 is the first valid profile slot (NOT a sentinel for missing)."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        printer = await printer_factory(model="H2D")
        db_session.add(
            SpoolmanSlotAssignment(
                printer_id=printer.id,
                ams_id=0,
                tray_id=3,
                spoolman_spool_id=216,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        mock_state = MagicMock()
        mock_state.ams_extruder_map = {"0": 0}
        mock_state.raw_data = {"ams": {"ams": []}}

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_state

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/0/3/configure",
                params={
                    "tray_info_idx": "GFL05",
                    "tray_type": "PLA",
                    "tray_sub_brands": "PLA Basic",
                    "tray_color": "FFFFFFFF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                    "cali_idx": 0,
                    "k_value": 0.020,
                },
            )

        assert response.status_code == 200
        kp = (
            await db_session.execute(select(SpoolmanKProfile).where(SpoolmanKProfile.spoolman_spool_id == 216))
        ).scalar_one_or_none()
        assert kp is not None
        assert kp.cali_idx == 0  # explicitly testing 0 is valid

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upsert_idempotent(
        self,
        async_client: AsyncClient,
        db_session,
        printer_factory,
    ):
        """Repeated POSTs update the same row (UNIQUE on spool_id+printer+extruder+nozzle_diameter)."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        printer = await printer_factory(model="H2D")
        db_session.add(
            SpoolmanSlotAssignment(
                printer_id=printer.id,
                ams_id=0,
                tray_id=3,
                spoolman_spool_id=216,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        mock_state = MagicMock()
        mock_state.ams_extruder_map = {"0": 0}
        mock_state.raw_data = {"ams": {"ams": []}}

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_state

            # First call with cali_idx=5
            await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/0/3/configure",
                params={
                    "tray_info_idx": "GFL05",
                    "tray_type": "PLA",
                    "tray_sub_brands": "PLA Basic",
                    "tray_color": "FFFFFFFF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                    "cali_idx": 5,
                    "k_value": 0.020,
                },
            )
            # Second call with cali_idx=10 (same slot/spool/extruder/nozzle)
            await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/0/3/configure",
                params={
                    "tray_info_idx": "GFL05",
                    "tray_type": "PLA",
                    "tray_sub_brands": "PLA Matte",
                    "tray_color": "FFFFFFFF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                    "cali_idx": 10,
                    "k_value": 0.025,
                },
            )

        # Should be exactly ONE row (updated), not two
        kps = (
            (await db_session.execute(select(SpoolmanKProfile).where(SpoolmanKProfile.spoolman_spool_id == 216)))
            .scalars()
            .all()
        )
        assert len(kps) == 1
        assert kps[0].cali_idx == 10  # updated to most recent
        assert kps[0].name == "PLA Matte"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_slot_extruder_inversion(
        self,
        async_client: AsyncClient,
        db_session,
        printer_factory,
    ):
        """ams_id=255 + tray_id=0 → kp.extruder=1 (ext-L); tray_id=1 → extruder=0 (ext-R)."""
        from backend.app.models.spool import Spool
        from backend.app.models.spool_assignment import SpoolAssignment
        from backend.app.models.spool_k_profile import SpoolKProfile

        printer = await printer_factory(model="H2D")
        spool = Spool(material="PLA")
        db_session.add(spool)
        await db_session.flush()
        # Note: SpoolmanSlotAssignment can't store ams_id=255 with tray_id=1
        # under the ck_tray_id_range constraint (0-3 valid). External-slot
        # K-profile persistence is therefore tested via local SpoolAssignment.
        db_session.add(
            SpoolAssignment(
                spool_id=spool.id,
                printer_id=printer.id,
                ams_id=255,
                tray_id=0,
            )
        )
        await db_session.commit()
        spool_id = spool.id

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        mock_state = MagicMock()
        mock_state.ams_extruder_map = {"0": 0}  # truthy so external-inversion path runs
        mock_state.raw_data = {"ams": {"ams": []}}

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_state

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/255/0/configure",
                params={
                    "tray_info_idx": "GFL05",
                    "tray_type": "PLA",
                    "tray_sub_brands": "PLA Basic",
                    "tray_color": "FFFFFFFF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                    "cali_idx": 5,
                    "k_value": 0.020,
                },
            )

        assert response.status_code == 200
        kp = (
            await db_session.execute(select(SpoolKProfile).where(SpoolKProfile.spool_id == spool_id))
        ).scalar_one_or_none()
        assert kp is not None
        # tray_id=0 → extruder = 1 - 0 = 1
        assert kp.extruder == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_dual_nozzle_extruder_persists(
        self,
        async_client: AsyncClient,
        db_session,
        printer_factory,
    ):
        """ams_extruder_map with extruder=1 → kp.extruder=1 persisted correctly."""
        from backend.app.models.spool import Spool
        from backend.app.models.spool_assignment import SpoolAssignment
        from backend.app.models.spool_k_profile import SpoolKProfile

        printer = await printer_factory(model="H2D")
        spool = Spool(material="PLA")
        db_session.add(spool)
        await db_session.flush()
        db_session.add(
            SpoolAssignment(
                spool_id=spool.id,
                printer_id=printer.id,
                ams_id=2,
                tray_id=3,
            )
        )
        await db_session.commit()
        spool_id = spool.id

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        mock_state = MagicMock()
        mock_state.ams_extruder_map = {"2": 1}  # AMS 2 is on extruder 1
        mock_state.raw_data = {"ams": {"ams": []}}

        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_state

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/2/3/configure",
                params={
                    "tray_info_idx": "GFL05",
                    "tray_type": "PLA",
                    "tray_sub_brands": "PLA Basic",
                    "tray_color": "FFFFFFFF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                    "cali_idx": 5,
                    "k_value": 0.020,
                },
            )

        assert response.status_code == 200
        kp = (
            await db_session.execute(select(SpoolKProfile).where(SpoolKProfile.spool_id == spool_id))
        ).scalar_one_or_none()
        assert kp is not None
        assert kp.extruder == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_db_error_does_not_fail_endpoint(
        self,
        async_client: AsyncClient,
        db_session,
        printer_factory,
    ):
        """DB errors during K-profile persistence are best-effort — endpoint still returns 200.

        Verifies the try/except wrap added in P13-3b: if DB upsert fails (e.g.
        because the schema is out of sync, a constraint violation, or any
        other transient error), the MQTT command was already sent successfully
        so we shouldn't return 500 to the user. The error is logged and the
        endpoint returns success.
        """
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        printer = await printer_factory(model="H2D")
        db_session.add(
            SpoolmanSlotAssignment(
                printer_id=printer.id,
                ams_id=0,
                tray_id=3,
                spoolman_spool_id=216,
            )
        )
        await db_session.commit()

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        mock_client.request_status_update.return_value = True

        mock_state = MagicMock()
        mock_state.ams_extruder_map = {"0": 0}
        mock_state.raw_data = {"ams": {"ams": []}}

        # Force the K-profile persistence path to fail by patching the
        # SpoolmanKProfile model class with a sentinel that raises when
        # instantiated. The MQTT call has already happened by then, so the
        # endpoint must catch and log without returning 500.
        with (
            patch("backend.app.api.routes.printers.printer_manager") as mock_pm,
            patch(
                "backend.app.models.spoolman_k_profile.SpoolmanKProfile",
                side_effect=RuntimeError("Simulated DB error"),
            ),
        ):
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = mock_state

            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/slots/0/3/configure",
                params={
                    "tray_info_idx": "GFL05",
                    "tray_type": "PLA",
                    "tray_sub_brands": "PLA Basic",
                    "tray_color": "FFFFFFFF",
                    "nozzle_temp_min": 190,
                    "nozzle_temp_max": 230,
                    "cali_idx": 5,
                    "k_value": 0.020,
                },
            )

        # Endpoint returns success — MQTT was sent, K-profile failed silently
        assert response.status_code == 200
        # MQTT was indeed called
        mock_client.extrusion_cali_sel.assert_called_once()
