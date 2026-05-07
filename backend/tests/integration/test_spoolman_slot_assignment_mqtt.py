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
        # Legacy attribute — production never had it set; keep for any code
        # path that still reads `mqtt_client.printer_state` directly. State
        # for the K-profile cascade now comes from printer_manager.get_status.
        mqtt_mock.printer_state = printer_state
        # Empty list = no printer-side kprofiles, so the realignment skips
        # printer_kp lookup. Tests that exercise realignment explicitly
        # populate this list themselves.
        if (
            not hasattr(printer_state, "kprofiles")
            or printer_state.kprofiles is None
            or isinstance(printer_state.kprofiles, MagicMock)
        ):
            printer_state.kprofiles = []

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

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
        # Legacy attribute — production never had it set; keep for any code
        # path that still reads `mqtt_client.printer_state` directly. State
        # for the K-profile cascade now comes from printer_manager.get_status.
        mqtt_mock.printer_state = printer_state
        # Empty list = no printer-side kprofiles, so the realignment skips
        # printer_kp lookup. Tests that exercise realignment explicitly
        # populate this list themselves.
        if (
            not hasattr(printer_state, "kprofiles")
            or printer_state.kprofiles is None
            or isinstance(printer_state.kprofiles, MagicMock)
        ):
            printer_state.kprofiles = []

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

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
        # Legacy attribute — production never had it set; keep for any code
        # path that still reads `mqtt_client.printer_state` directly. State
        # for the K-profile cascade now comes from printer_manager.get_status.
        mqtt_mock.printer_state = printer_state
        # Empty list = no printer-side kprofiles, so the realignment skips
        # printer_kp lookup. Tests that exercise realignment explicitly
        # populate this list themselves.
        if (
            not hasattr(printer_state, "kprofiles")
            or printer_state.kprofiles is None
            or isinstance(printer_state.kprofiles, MagicMock)
        ):
            printer_state.kprofiles = []

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

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


# ---------------------------------------------------------------------------
# F7: ams_id=255 External-Slot Extruder-Inversion
# ---------------------------------------------------------------------------


class TestExternalSlotExtruderInversion:
    """F7: ams_id=255 maps tray_id→extruder via inversion (0→1, 1→0)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_slot_tray0_maps_to_extruder1(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client, db_session
    ):
        """tray_id=0 on ams_id=255 → extruder=1 (ext-L)."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        # Create K-profiles for both extruders so we can verify which one matches
        kp_extruder_1 = SpoolmanKProfile(
            spoolman_spool_id=10,
            printer_id=test_printer.id,
            extruder=1,
            nozzle_diameter="0.4",
            k_value=0.03,
            cali_idx=1,
            setting_id=None,
        )
        db_session.add(kp_extruder_1)
        await db_session.commit()

        printer_state = MagicMock()
        printer_state.nozzles = [MagicMock(nozzle_diameter="0.4"), MagicMock(nozzle_diameter="0.4")]
        printer_state.ams_extruder_map = {"0": 0}  # present so external inversion logic triggers

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        # Legacy attribute — production never had it set; keep for any code
        # path that still reads `mqtt_client.printer_state` directly. State
        # for the K-profile cascade now comes from printer_manager.get_status.
        mqtt_mock.printer_state = printer_state
        # Empty list = no printer-side kprofiles, so the realignment skips
        # printer_kp lookup. Tests that exercise realignment explicitly
        # populate this list themselves.
        if (
            not hasattr(printer_state, "kprofiles")
            or printer_state.kprofiles is None
            or isinstance(printer_state.kprofiles, MagicMock)
        ):
            printer_state.kprofiles = []

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

            resp = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": 10,
                    "printer_id": test_printer.id,
                    "ams_id": 255,
                    "tray_id": 0,
                },
            )

        assert resp.status_code == 200
        # extrusion_cali_sel should be called with the K-profile for extruder=1 (cali_idx=1)
        # The extruder itself is not passed as an argument — it's used internally to filter profiles
        mqtt_mock.extrusion_cali_sel.assert_called_once()
        call_kwargs = mqtt_mock.extrusion_cali_sel.call_args[1]
        assert call_kwargs["cali_idx"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_slot_tray1_maps_to_extruder0(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client, db_session
    ):
        """tray_id=1 on ams_id=255 → extruder=0 (ext-R)."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        kp_extruder_0 = SpoolmanKProfile(
            spoolman_spool_id=10,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.4",
            k_value=0.02,
            cali_idx=2,
            setting_id=None,
        )
        db_session.add(kp_extruder_0)
        await db_session.commit()

        printer_state = MagicMock()
        printer_state.nozzles = [MagicMock(nozzle_diameter="0.4"), MagicMock(nozzle_diameter="0.4")]
        printer_state.ams_extruder_map = {"0": 0}

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        # Legacy attribute — production never had it set; keep for any code
        # path that still reads `mqtt_client.printer_state` directly. State
        # for the K-profile cascade now comes from printer_manager.get_status.
        mqtt_mock.printer_state = printer_state
        # Empty list = no printer-side kprofiles, so the realignment skips
        # printer_kp lookup. Tests that exercise realignment explicitly
        # populate this list themselves.
        if (
            not hasattr(printer_state, "kprofiles")
            or printer_state.kprofiles is None
            or isinstance(printer_state.kprofiles, MagicMock)
        ):
            printer_state.kprofiles = []

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

            resp = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": 10,
                    "printer_id": test_printer.id,
                    "ams_id": 255,
                    "tray_id": 1,
                },
            )

        assert resp.status_code == 200
        # extrusion_cali_sel should be called with the K-profile for extruder=0 (cali_idx=2)
        mqtt_mock.extrusion_cali_sel.assert_called_once()
        call_kwargs = mqtt_mock.extrusion_cali_sel.call_args[1]
        assert call_kwargs["cali_idx"] == 2


# ---------------------------------------------------------------------------
# P9-TEST-BE: Live cali_idx fallback when no K-profile is stored (Bug #10)
# ---------------------------------------------------------------------------


class TestAssignSpoolmanSlotLiveCaliIdx:
    """When no SpoolmanKProfile exists, live tray cali_idx is used as fallback."""

    def _make_printer_state(self, ams_id: int, tray_id: int, cali_idx: int | None):
        """Build a minimal printer_state mock with one AMS tray."""
        tray_mock = {
            "id": tray_id,
            "cali_idx": cali_idx,
        }
        ams_mock = {"id": ams_id, "tray": [tray_mock]}
        state = MagicMock()
        state.nozzles = [MagicMock(nozzle_diameter="0.4")]
        state.ams_extruder_map = {str(ams_id): 0}
        state.raw_data = {"ams": [ams_mock]}
        return state

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_kprofile_uses_live_cali_idx(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client
    ):
        """When no K-profile exists, live tray cali_idx is sent via extrusion_cali_sel."""
        printer_state = self._make_printer_state(ams_id=0, tray_id=1, cali_idx=42)

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        # Legacy attribute — production never had it set; keep for any code
        # path that still reads `mqtt_client.printer_state` directly. State
        # for the K-profile cascade now comes from printer_manager.get_status.
        mqtt_mock.printer_state = printer_state
        # Empty list = no printer-side kprofiles, so the realignment skips
        # printer_kp lookup. Tests that exercise realignment explicitly
        # populate this list themselves.
        if (
            not hasattr(printer_state, "kprofiles")
            or printer_state.kprofiles is None
            or isinstance(printer_state.kprofiles, MagicMock)
        ):
            printer_state.kprofiles = []

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

            resp = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": 10,
                    "printer_id": test_printer.id,
                    "ams_id": 0,
                    "tray_id": 1,
                },
            )

        assert resp.status_code == 200
        mqtt_mock.extrusion_cali_sel.assert_called_once()
        call_kwargs = mqtt_mock.extrusion_cali_sel.call_args[1]
        assert call_kwargs["cali_idx"] == 42
        assert call_kwargs["ams_id"] == 0
        assert call_kwargs["tray_id"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_kprofile_no_live_cali_idx_nothing_sent(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client
    ):
        """When no K-profile and tray has no cali_idx, extrusion_cali_sel is not called."""
        printer_state = self._make_printer_state(ams_id=0, tray_id=2, cali_idx=None)

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        # Legacy attribute — production never had it set; keep for any code
        # path that still reads `mqtt_client.printer_state` directly. State
        # for the K-profile cascade now comes from printer_manager.get_status.
        mqtt_mock.printer_state = printer_state
        # Empty list = no printer-side kprofiles, so the realignment skips
        # printer_kp lookup. Tests that exercise realignment explicitly
        # populate this list themselves.
        if (
            not hasattr(printer_state, "kprofiles")
            or printer_state.kprofiles is None
            or isinstance(printer_state.kprofiles, MagicMock)
        ):
            printer_state.kprofiles = []

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

            resp = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": 10,
                    "printer_id": test_printer.id,
                    "ams_id": 0,
                    "tray_id": 2,
                },
            )

        assert resp.status_code == 200
        mqtt_mock.extrusion_cali_sel.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_kprofile_takes_priority_over_live_cali_idx(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client, db_session
    ):
        """Stored K-profile cali_idx wins over live tray cali_idx."""
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        kp = SpoolmanKProfile(
            spoolman_spool_id=10,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.4",
            k_value=0.02,
            cali_idx=10,
            setting_id="CaliID",
        )
        db_session.add(kp)
        await db_session.commit()

        # Live tray has a different cali_idx — stored profile must win
        printer_state = self._make_printer_state(ams_id=0, tray_id=3, cali_idx=99)

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        # Legacy attribute — production never had it set; keep for any code
        # path that still reads `mqtt_client.printer_state` directly. State
        # for the K-profile cascade now comes from printer_manager.get_status.
        mqtt_mock.printer_state = printer_state
        # Empty list = no printer-side kprofiles, so the realignment skips
        # printer_kp lookup. Tests that exercise realignment explicitly
        # populate this list themselves.
        if (
            not hasattr(printer_state, "kprofiles")
            or printer_state.kprofiles is None
            or isinstance(printer_state.kprofiles, MagicMock)
        ):
            printer_state.kprofiles = []

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

            resp = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": 10,
                    "printer_id": test_printer.id,
                    "ams_id": 0,
                    "tray_id": 3,
                },
            )

        assert resp.status_code == 200
        mqtt_mock.extrusion_cali_sel.assert_called_once()
        call_kwargs = mqtt_mock.extrusion_cali_sel.call_args[1]
        # Must use stored K-profile (10), NOT live cali_idx (99)
        assert call_kwargs["cali_idx"] == 10

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_live_cali_idx_not_used_if_negative(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client
    ):
        """A negative live cali_idx is invalid and must not be sent."""
        printer_state = self._make_printer_state(ams_id=0, tray_id=0, cali_idx=-1)

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        # Legacy attribute — production never had it set; keep for any code
        # path that still reads `mqtt_client.printer_state` directly. State
        # for the K-profile cascade now comes from printer_manager.get_status.
        mqtt_mock.printer_state = printer_state
        # Empty list = no printer-side kprofiles, so the realignment skips
        # printer_kp lookup. Tests that exercise realignment explicitly
        # populate this list themselves.
        if (
            not hasattr(printer_state, "kprofiles")
            or printer_state.kprofiles is None
            or isinstance(printer_state.kprofiles, MagicMock)
        ):
            printer_state.kprofiles = []

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

            resp = await async_client.post(
                "/api/v1/spoolman/inventory/slot-assignments",
                json={
                    "spoolman_spool_id": 10,
                    "printer_id": test_printer.id,
                    "ams_id": 0,
                    "tray_id": 0,
                },
            )

        assert resp.status_code == 200
        mqtt_mock.extrusion_cali_sel.assert_not_called()


# ---------------------------------------------------------------------------
# Realignment of slot filament context to K-profile preset
# ---------------------------------------------------------------------------
# When the user assigns a Spoolman spool whose stored kp was calibrated under
# a specific filament preset (e.g. P-prefix local, or a named cloud preset),
# the slot must be configured under THAT preset for the printer to find the
# cali_idx in its calibration table. Without realignment the slot ends up on
# generic PLA / default K — the symptom maztiggy reported on x1c-2 (#1114).


class TestAssignSpoolmanSlotKProfileRealignment:
    """assign_spoolman_slot realigns tray_info_idx + setting_id to kp context."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_realigns_to_printer_reported_filament_id(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client, db_session
    ):
        """When state.kprofiles has the cali_idx, use printer_kp.filament_id verbatim.

        The printer keys its calibration table by filament_id, not setting_id.
        For a P-prefix local preset (printer-registered), filament_id and
        tray_info_idx must match for the cali_idx to apply.
        """
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        # Stored kp with setting_id but no filament_id (the schema gap)
        kp = SpoolmanKProfile(
            spoolman_spool_id=10,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.4",
            k_value=0.025,
            cali_idx=8948,
            setting_id="PFUSedbf16b803ff3e",
        )
        db_session.add(kp)
        await db_session.commit()

        printer_state = MagicMock()
        printer_state.nozzles = [MagicMock(nozzle_diameter="0.4")]
        printer_state.ams_extruder_map = {"0": 0}
        printer_state.raw_data = None
        # Live calibration entry from the printer — this is what cali_idx 8948
        # is actually registered under. P-prefix is a printer-local preset
        # (different from PFUS-prefix cloud user presets).
        printer_kp = MagicMock()
        printer_kp.slot_id = 8948
        printer_kp.nozzle_diameter = "0.4"
        printer_kp.filament_id = "P4d64437"
        printer_kp.setting_id = "PFUSedbf16b803ff3e"
        printer_state.kprofiles = [printer_kp]

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        mqtt_mock.printer_state = printer_state

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

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
        # Both MQTT commands must reference the printer-reported filament_id
        # so the slot context and the cali_sel context match.
        amf_kwargs = mqtt_mock.ams_set_filament_setting.call_args[1]
        assert amf_kwargs["tray_info_idx"] == "P4d64437"
        assert amf_kwargs["setting_id"] == "PFUSedbf16b803ff3e"
        cs_kwargs = mqtt_mock.extrusion_cali_sel.call_args[1]
        assert cs_kwargs["cali_idx"] == 8948
        assert cs_kwargs["filament_id"] == "P4d64437"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_skips_realignment_for_pfus_prefix(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client, db_session
    ):
        """PFUS-prefix cloud-user presets are rejected by the slicer in tray_info_idx.

        For those, tray_info_idx must stay as the GF* generic so the slicer
        can render the slot. setting_id can still be realigned to the cloud
        preset (slicer uses that for display), but tray_info_idx stays GF*.
        """
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        kp = SpoolmanKProfile(
            spoolman_spool_id=10,
            printer_id=test_printer.id,
            extruder=0,
            nozzle_diameter="0.4",
            k_value=0.025,
            cali_idx=42,
            setting_id="PFUSedbf16b803ff3e",
        )
        db_session.add(kp)
        await db_session.commit()

        printer_state = MagicMock()
        printer_state.nozzles = [MagicMock(nozzle_diameter="0.4")]
        printer_state.ams_extruder_map = {"0": 0}
        printer_state.raw_data = None
        # Printer-side kp filament_id is PFUS-prefix → realignment must skip
        printer_kp = MagicMock()
        printer_kp.slot_id = 42
        printer_kp.nozzle_diameter = "0.4"
        printer_kp.filament_id = "PFUSedbf16b803ff3e"
        printer_kp.setting_id = "PFUSedbf16b803ff3e"
        printer_state.kprofiles = [printer_kp]

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        mqtt_mock.printer_state = printer_state

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

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
        amf_kwargs = mqtt_mock.ams_set_filament_setting.call_args[1]
        # tray_info_idx stays as the resolved generic (slicer accepts GF*)
        assert amf_kwargs["tray_info_idx"] == "GFL99"
        # setting_id may be realigned to the cloud preset for slicer display
        assert amf_kwargs["setting_id"] == "PFUSedbf16b803ff3e"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extruder_relax_falls_back_to_any_extruder_kp(
        self, async_client: AsyncClient, slot_settings, test_printer, mock_spoolman_client, db_session
    ):
        """Hard-skip on extruder mismatch silently dropped valid stored profiles
        when the AMS-extruder map shifted. The cascade now prefers exact
        extruder match but falls back to any kp on the same printer + nozzle.
        """
        from backend.app.models.spoolman_k_profile import SpoolmanKProfile

        # kp is for extruder=1, but slot will be on extruder=0 (mismatch)
        kp = SpoolmanKProfile(
            spoolman_spool_id=10,
            printer_id=test_printer.id,
            extruder=1,
            nozzle_diameter="0.4",
            k_value=0.025,
            cali_idx=42,
            setting_id="GFSL05",
        )
        db_session.add(kp)
        await db_session.commit()

        printer_state = MagicMock()
        printer_state.nozzles = [MagicMock(nozzle_diameter="0.4")]
        printer_state.ams_extruder_map = {"0": 0}
        printer_state.raw_data = None
        printer_state.kprofiles = []

        mqtt_mock = MagicMock()
        mqtt_mock.ams_set_filament_setting = MagicMock()
        mqtt_mock.extrusion_cali_sel = MagicMock()
        mqtt_mock.printer_state = printer_state

        with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm_mock:
            pm_mock.get_client = MagicMock(return_value=mqtt_mock)
            pm_mock.get_status = MagicMock(return_value=printer_state)

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
        # extruder mismatch was hard-skipped pre-fix; now used as fallback
        cs_kwargs = mqtt_mock.extrusion_cali_sel.call_args[1]
        assert cs_kwargs["cali_idx"] == 42
