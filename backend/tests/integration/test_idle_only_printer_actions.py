"""Actions that only an idle printer takes: firmware upload and AMS slot reset.

Firmware goes onto the SD card the printer prints from, and a slot reset
clears the filament setting of a tray a running print may be feeding from.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.firmware_update import FirmwareUpdateService, get_upload_state, reset_upload_state


def _client(state: str) -> MagicMock:
    client = MagicMock()
    client.state = SimpleNamespace(state=state, firmware_version="01.06.00.00", sdcard=True, connected=True)
    client.reset_ams_slot.return_value = True
    return client


def _firmware_service() -> MagicMock:
    service = MagicMock()
    service.check_for_update = AsyncMock(return_value={"update_available": True, "latest_version": "01.07.00.00"})
    service.get_firmware_file_info = AsyncMock(return_value={"filename": "fw.bin"})
    service.download_firmware = AsyncMock()
    service.get_latest_version = AsyncMock(return_value=None)
    return service


def _patched(client: MagicMock, service: MagicMock):
    return (
        patch("backend.app.services.firmware_update.printer_manager.get_client", return_value=client),
        patch("backend.app.services.firmware_update.get_firmware_service", return_value=service),
        patch(
            "backend.app.services.firmware_update.get_storage_info_async",
            AsyncMock(return_value={"free_bytes": 10 * 1024**3}),
        ),
    )


class TestFirmwareUpload:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["RUNNING", "PAUSE", "PREPARE", "SLICING", "unknown"])
    async def test_a_busy_printer_is_refused_in_the_prepare_check(self, async_client, printer_factory, state):
        printer = await printer_factory()
        a, b, c = _patched(_client(state), _firmware_service())
        with a, b, c:
            response = await async_client.get(f"/api/v1/firmware/updates/{printer.id}/prepare")

        assert response.status_code == 200
        body = response.json()
        assert body["can_proceed"] is False
        assert any("idle" in error for error in body["errors"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["IDLE", "FINISH", "FAILED"])
    async def test_an_idle_printer_passes_the_prepare_check(self, async_client, printer_factory, state):
        printer = await printer_factory()
        a, b, c = _patched(_client(state), _firmware_service())
        with a, b, c:
            response = await async_client.get(f"/api/v1/firmware/updates/{printer.id}/prepare")

        assert response.json()["can_proceed"] is True, response.json()["errors"]

    @pytest.mark.asyncio
    async def test_the_upload_route_refuses_a_printing_printer(self, async_client, printer_factory):
        printer = await printer_factory()
        a, b, c = _patched(_client("RUNNING"), _firmware_service())
        with (
            a,
            b,
            c,
            patch.object(FirmwareUpdateService, "start_upload", AsyncMock(return_value=True)) as start,
        ):
            response = await async_client.post(f"/api/v1/firmware/updates/{printer.id}/upload")

        assert response.status_code == 400
        assert "idle" in response.json()["detail"]
        start.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_print_started_during_the_download_gets_no_firmware(self, tmp_path):
        """The download can take minutes; the card is checked again before writing to it."""
        client = _client("IDLE")
        service = _firmware_service()
        firmware = tmp_path / "fw.bin"
        firmware.write_bytes(b"x")

        async def download(*_args, **_kwargs):
            client.state.state = "RUNNING"
            return firmware

        service.download_firmware = AsyncMock(side_effect=download)
        upload = AsyncMock(return_value=True)
        reset_upload_state(9101)
        with (
            patch("backend.app.services.firmware_update.printer_manager.get_client", return_value=client),
            patch("backend.app.services.firmware_update.get_firmware_service", return_value=service),
            patch("backend.app.services.firmware_update.upload_file_async", upload),
            patch(
                "backend.app.services.firmware_update.get_ftp_retry_settings",
                AsyncMock(return_value=(False, 0, 0, 30)),
            ),
        ):
            await FirmwareUpdateService()._do_upload(9101, "192.0.2.10", "12345678", "X1C", "01.07.00.00")

        upload.assert_not_called()
        state = get_upload_state(9101)
        assert state.status.value == "error"
        assert "idle" in (state.error or "")
        reset_upload_state(9101)


class TestAmsSlotReset:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["RUNNING", "PAUSE", "PREPARE", "SLICING"])
    async def test_refused_while_a_print_is_loaded(self, async_client, printer_factory, state):
        printer = await printer_factory()
        client = _client(state)
        with patch("backend.app.api.routes.printers.printer_manager.get_client", return_value=client):
            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/0/tray/1/reset")

        assert response.status_code == 409
        client.reset_ams_slot.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["IDLE", "FINISH", "FAILED"])
    async def test_sent_when_no_print_is_loaded(self, async_client, printer_factory, state):
        printer = await printer_factory()
        client = _client(state)
        with patch("backend.app.api.routes.printers.printer_manager.get_client", return_value=client):
            response = await async_client.post(f"/api/v1/printers/{printer.id}/ams/0/tray/1/reset")

        assert response.status_code == 200
        client.reset_ams_slot.assert_called_once_with(ams_id=0, tray_id=1)


class TestQueueDuringFirmwareUpload:
    """A queued print does not start while firmware is being put on the printer's card."""

    @pytest.fixture
    def idle_printer(self):
        from backend.app.services.printer_manager import printer_manager

        printer_id = 9102
        client = MagicMock()
        client.state = SimpleNamespace(state="IDLE", connected=True)
        printer_manager._clients[printer_id] = client
        reset_upload_state(printer_id)
        try:
            with (
                patch.object(printer_manager, "is_connected", return_value=True),
                patch.object(printer_manager, "get_status", return_value=client.state),
            ):
                yield printer_id
        finally:
            printer_manager._clients.pop(printer_id, None)
            reset_upload_state(printer_id)

    @pytest.mark.parametrize("status", ["preparing", "downloading", "uploading"])
    def test_not_idle_while_an_upload_runs(self, idle_printer, status):
        from backend.app.services.firmware_update import FirmwareUploadStatus
        from backend.app.services.print_scheduler import PrintScheduler

        get_upload_state(idle_printer).status = FirmwareUploadStatus(status)

        assert PrintScheduler()._is_printer_idle(idle_printer, require_plate_clear=False) is False

    @pytest.mark.parametrize("status", ["idle", "complete", "error"])
    def test_idle_again_once_it_is_over(self, idle_printer, status):
        from backend.app.services.firmware_update import FirmwareUploadStatus
        from backend.app.services.print_scheduler import PrintScheduler

        get_upload_state(idle_printer).status = FirmwareUploadStatus(status)

        assert PrintScheduler()._is_printer_idle(idle_printer, require_plate_clear=False) is True
