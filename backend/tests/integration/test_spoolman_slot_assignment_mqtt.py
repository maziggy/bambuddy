"""Integration tests for MQTT auto-configuration when assigning a Spoolman spool to an AMS slot.

Covers:
  - ams_set_filament_setting is called with correct parameters on assign
  - extrusion_cali_sel is called when a matching K-profile exists
  - MQTT failure does NOT roll back the slot assignment
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

SAMPLE_SPOOL = {
    "id": 10,
    "filament": {
        "id": 1,
        "name": "PLA Basic",
        "material": "PLA",
        "color_hex": "FF0000",
        "weight": 1000,
        "vendor": {"id": 1, "name": "BrandX"},
    },
    "remaining_weight": 800.0,
    "used_weight": 200.0,
    "location": None,
    "comment": None,
    "first_used": None,
    "last_used": None,
    "registered": "2024-01-01T00:00:00+00:00",
    "archived": False,
    "price": None,
    "extra": {},
}


@pytest.fixture
async def slot_settings(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


@pytest.fixture
async def test_printer(db_session):
    from backend.app.models.printer import Printer

    printer = Printer(
        name="MQTT Printer",
        serial_number="MQTTTEST001",
        ip_address="192.168.1.200",
        access_code="12345678",
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)
    return printer


@pytest.fixture
def mock_spoolman_client():
    client = MagicMock()
    client.base_url = "http://localhost:7912"
    client.health_check = AsyncMock(return_value=True)
    client.get_spool = AsyncMock(return_value=SAMPLE_SPOOL)

    with patch(
        "backend.app.api.routes.spoolman_inventory._get_client",
        AsyncMock(return_value=client),
    ):
        yield client


class TestAssignSlotMqtt:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_mqtt_ams_set_filament_called_on_assign(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client
    ):
        """Assigning a Spoolman spool fires ams_set_filament_setting via MQTT."""
        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        mqtt_mock.printer_state = None

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)

            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": 10,
                    "printer_id": test_printer.id,
                    "ams_id": 0,
                    "tray_id": 1,
                },
            )

        assert response.status_code == 200
        mqtt_mock.ams_set_filament_setting.assert_called_once()
        call_kwargs = mqtt_mock.ams_set_filament_setting.call_args[1]
        assert call_kwargs["ams_id"] == 0
        assert call_kwargs["tray_id"] == 1
        assert call_kwargs["tray_type"] == "PLA"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_mqtt_failure_does_not_rollback_assignment(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client
    ):
        """A crash inside the MQTT block must not un-persist the slot assignment."""
        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock(side_effect=RuntimeError("MQTT down"))
        mqtt_mock.printer_state = None

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)

            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": 10,
                    "printer_id": test_printer.id,
                    "ams_id": 1,
                    "tray_id": 0,
                },
            )

        assert response.status_code == 200

        # Verify the assignment IS in the DB despite the MQTT crash
        all_resp = await async_client.get(
            "/api/v1/spoolman/inventory/slot-assignments/all",
            params={"printer_id": test_printer.id},
        )
        assert all_resp.status_code == 200
        rows = all_resp.json()
        assert any(r["spoolman_spool_id"] == 10 for r in rows)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extrusion_cali_sel_called_when_k_profile_exists(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client, db_session
    ):
        """extrusion_cali_sel is fired when a matching SpoolmanKProfile row exists."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        kp = SpoolmanKProfile(
            spoolman_spool_id=10,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.4",
            k_value=0.02,
            cali_idx=5,
            setting_id="CaliID",
        )
        db_session.add(kp)
        await db_session.commit()

        printer_state = MagicMock()
        printer_state.nozzles = [MagicMock(nozzle_diameter="0.4")]
        printer_state.ams_extruder_map = {"0": 0}

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        mqtt_mock.printer_state = printer_state

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)

            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": 10,
                    "printer_id": test_printer.id,
                    "ams_id": 0,
                    "tray_id": 2,
                },
            )

        assert response.status_code == 200
        mqtt_mock.extrusion_cali_sel.assert_called_once()
        call_kwargs = mqtt_mock.extrusion_cali_sel.call_args[1]
        assert call_kwargs["cali_idx"] == 5
        assert call_kwargs["ams_id"] == 0
        assert call_kwargs["tray_id"] == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extrusion_cali_sel_not_called_on_nozzle_mismatch(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client, db_session
    ):
        """extrusion_cali_sel is NOT called when nozzle diameter does not match K-profile."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        kp = SpoolmanKProfile(
            spoolman_spool_id=10,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.6",
            k_value=0.03,
            cali_idx=7,
            setting_id="CaliID",
        )
        db_session.add(kp)
        await db_session.commit()

        printer_state = MagicMock()
        printer_state.nozzles = [MagicMock(nozzle_diameter="0.4")]
        printer_state.ams_extruder_map = {"0": 0}

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        mqtt_mock.printer_state = printer_state

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)

            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": 10,
                    "printer_id": test_printer.id,
                    "ams_id": 0,
                    "tray_id": 3,
                },
            )

        assert response.status_code == 200
        mqtt_mock.extrusion_cali_sel.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extrusion_cali_sel_not_called_when_cali_idx_none(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client, db_session
    ):
        """extrusion_cali_sel is NOT called when K-profile has cali_idx=None."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        kp = SpoolmanKProfile(
            spoolman_spool_id=10,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.4",
            k_value=0.02,
            cali_idx=None,
            setting_id=None,
        )
        db_session.add(kp)
        await db_session.commit()

        printer_state = MagicMock()
        printer_state.nozzles = [MagicMock(nozzle_diameter="0.4")]
        printer_state.ams_extruder_map = {"0": 0}

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        mqtt_mock.printer_state = printer_state

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)

            response = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": 10,
                    "printer_id": test_printer.id,
                    "ams_id": 0,
                    "tray_id": 3,
                },
            )

        assert response.status_code == 200
        mqtt_mock.extrusion_cali_sel.assert_not_called()
