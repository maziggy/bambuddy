"""Integration tests for inventory spool assignment — tray_info_idx resolution.

Tests that PFUS* user-local preset IDs are replaced with generic Bambu IDs,
and that existing recognised presets on slots are reused when the material matches.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool


@pytest.fixture
async def spool_factory(db_session: AsyncSession):
    """Factory to create test spools."""
    _counter = [0]

    async def _create_spool(**kwargs):
        _counter[0] += 1
        defaults = {
            "material": "PLA",
            "subtype": "Basic",
            "brand": "Devil Design",
            "color_name": "Red",
            "rgba": "FF0000FF",
            "label_weight": 1000,
            "weight_used": 0,
            "slicer_filament": "PFUS9ac902733670a9",
        }
        defaults.update(kwargs)
        spool = Spool(**defaults)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    return _create_spool


def _make_mock_status(ams_data=None, vt_tray=None, nozzles=None, ams_extruder_map=None):
    """Build a mock printer status with optional AMS/nozzle data."""
    status = MagicMock()
    raw = {}
    if ams_data is not None:
        raw["ams"] = {"ams": ams_data}
    if vt_tray is not None:
        raw["vt_tray"] = vt_tray
    status.raw_data = raw
    status.nozzles = nozzles or [MagicMock(nozzle_diameter="0.4")]
    status.ams_extruder_map = ams_extruder_map
    return status


class TestAssignSpoolTrayInfoIdx:
    """Tests for tray_info_idx resolution during spool assignment."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_pfus_replaced_with_generic(self, async_client: AsyncClient, printer_factory, spool_factory):
        """PFUS* user-local IDs are replaced with generic Bambu IDs."""
        printer = await printer_factory(name="H2D")
        spool = await spool_factory(slicer_filament="PFUS9ac902733670a9", material="PLA")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        status = _make_mock_status(ams_data=[{"id": 2, "tray": [{"id": 3, "tray_info_idx": "", "tray_type": ""}]}])

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 2, "tray_id": 3},
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            assert call_kwargs.kwargs["tray_info_idx"] == "GFL99"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reuses_existing_recognised_preset(self, async_client: AsyncClient, printer_factory, spool_factory):
        """When slot already has a recognised preset for same material, reuse it."""
        printer = await printer_factory(name="H2D")
        spool = await spool_factory(slicer_filament="PFUS9ac902733670a9", material="PLA")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        # Slot already configured by slicer with cloud-synced preset
        status = _make_mock_status(
            ams_data=[{"id": 2, "tray": [{"id": 3, "tray_info_idx": "P4d64437", "tray_type": "PLA"}]}]
        )

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 2, "tray_id": 3},
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            # Should reuse the slicer's cloud-synced ID
            assert call_kwargs.kwargs["tray_info_idx"] == "P4d64437"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_different_material_uses_generic(self, async_client: AsyncClient, printer_factory, spool_factory):
        """When slot has a preset for a DIFFERENT material, use generic ID."""
        printer = await printer_factory(name="H2D")
        spool = await spool_factory(slicer_filament="PFUS9ac902733670a9", material="PETG")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        # Slot currently has PLA but spool is PETG
        status = _make_mock_status(
            ams_data=[{"id": 2, "tray": [{"id": 3, "tray_info_idx": "P4d64437", "tray_type": "PLA"}]}]
        )

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 2, "tray_id": 3},
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            assert call_kwargs.kwargs["tray_info_idx"] == "GFG99"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_gf_slicer_filament_kept(self, async_client: AsyncClient, printer_factory, spool_factory):
        """Standard GF* IDs from spool.slicer_filament are used directly."""
        printer = await printer_factory(name="X1C")
        spool = await spool_factory(slicer_filament="GFL05", material="PLA")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        status = _make_mock_status(ams_data=[])

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            assert call_kwargs.kwargs["tray_info_idx"] == "GFL05"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_slicer_filament_uses_generic(self, async_client: AsyncClient, printer_factory, spool_factory):
        """Spool with no slicer_filament gets a generic ID from material type."""
        printer = await printer_factory(name="X1C")
        spool = await spool_factory(slicer_filament=None, material="ABS")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        status = _make_mock_status(ams_data=[])

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            assert call_kwargs.kwargs["tray_info_idx"] == "GFB99"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_existing_pfus_on_slot_not_reused(self, async_client: AsyncClient, printer_factory, spool_factory):
        """A PFUS* ID already on the slot should NOT be reused (it's also user-local)."""
        printer = await printer_factory(name="H2D")
        spool = await spool_factory(slicer_filament="PFUS1111111111", material="PLA")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        # Slot has a PFUS* ID from some previous config
        status = _make_mock_status(
            ams_data=[{"id": 0, "tray": [{"id": 0, "tray_info_idx": "PFUS2222222222", "tray_type": "PLA"}]}]
        )

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            # Should NOT reuse the PFUS on the slot — use generic instead
            assert call_kwargs.kwargs["tray_info_idx"] == "GFL99"
