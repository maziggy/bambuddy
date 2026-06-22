"""API coverage for full printer calibration without contacting a printer."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from backend.app.services.bambu_mqtt import (
    FullCalibrationInvalidSelectionError,
    FullCalibrationPublishError,
    FullCalibrationUnsupportedError,
    PlateClearConfirmationRequiredError,
    PrinterAlreadyCalibratingError,
    PrinterBusyForCalibrationError,
    PrinterDisconnectedForCalibrationError,
)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_calibration_route_delegates_to_requested_printer(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(model="P1S")

    with patch("backend.app.api.routes.printers.printer_manager.start_full_calibration") as start:
        response = await async_client.post(
            f"/api/v1/printers/{printer.id}/calibration/full",
            json={"stages": ["bed_leveling"], "plate_clear_confirmed": True},
        )

    assert response.status_code == 202
    assert response.json() == {"status": "calibration_command_sent"}
    start.assert_called_once_with(printer.id, ["bed_leveling"], plate_clear_confirmed=True)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_calibration_route_returns_not_found_for_unknown_printer(async_client: AsyncClient):
    with patch("backend.app.api.routes.printers.printer_manager.start_full_calibration") as start:
        response = await async_client.post("/api/v1/printers/999999/calibration/full")

    assert response.status_code == 404
    start.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (PrinterDisconnectedForCalibrationError("ignored"), 409, "printer_disconnected"),
        (PrinterBusyForCalibrationError("ignored"), 409, "printer_not_idle"),
        (PlateClearConfirmationRequiredError("ignored"), 409, "plate_clear_confirmation_required"),
        (PrinterAlreadyCalibratingError("ignored"), 409, "calibration_already_running"),
        (FullCalibrationUnsupportedError("ignored"), 422, "calibration_unsupported"),
        (FullCalibrationInvalidSelectionError("ignored"), 422, "invalid_calibration_selection"),
        (FullCalibrationPublishError("ignored"), 502, "calibration_delivery_failed"),
    ],
)
async def test_full_calibration_route_returns_safe_structured_errors(
    async_client: AsyncClient,
    printer_factory,
    error: Exception,
    status_code: int,
    code: str,
):
    printer = await printer_factory(model="P1S")

    with patch(
        "backend.app.api.routes.printers.printer_manager.start_full_calibration",
        side_effect=error,
    ):
        response = await async_client.post(f"/api/v1/printers/{printer.id}/calibration/full")

    assert response.status_code == status_code
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert "access" not in detail["message"].lower()
    assert "code" not in detail["message"].lower()
