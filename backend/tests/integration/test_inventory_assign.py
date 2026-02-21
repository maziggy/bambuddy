"""Integration tests for inventory spool assignment — tray_info_idx resolution.

Tests that the spool's own slicer_filament (including PFUS* cloud-synced
custom presets) takes priority, with slot reuse and generic fallback as
lower-priority fallbacks.
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
    async def test_pfus_slicer_filament_used_directly(self, async_client: AsyncClient, printer_factory, spool_factory):
        """PFUS* cloud-synced custom preset IDs are sent to the printer."""
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
            assert call_kwargs.kwargs["tray_info_idx"] == "PFUS9ac902733670a9"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_preset_takes_priority_over_slot(
        self, async_client: AsyncClient, printer_factory, spool_factory
    ):
        """Spool's own slicer_filament takes priority over slot's existing preset."""
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
            # Spool's own preset wins over slot's existing one
            assert call_kwargs.kwargs["tray_info_idx"] == "PFUS9ac902733670a9"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spool_preset_used_even_if_different_material_on_slot(
        self, async_client: AsyncClient, printer_factory, spool_factory
    ):
        """Spool's own slicer_filament is used regardless of what's on the slot."""
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
            assert call_kwargs.kwargs["tray_info_idx"] == "PFUS9ac902733670a9"

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
    async def test_spool_pfus_used_over_slot_pfus(self, async_client: AsyncClient, printer_factory, spool_factory):
        """Spool's own PFUS preset is used even when slot has a different PFUS."""
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
            # Spool's own preset wins
            assert call_kwargs.kwargs["tray_info_idx"] == "PFUS1111111111"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_generic_on_slot_not_reused_over_spool_preset(
        self, async_client: AsyncClient, printer_factory, spool_factory
    ):
        """Generic ID on slot (e.g. GFB99) must not override spool's own preset."""
        printer = await printer_factory(name="P2S")
        spool = await spool_factory(slicer_filament="PFUScda4c46fc9031", material="ABS")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        # Slot stuck on generic ABS from a previous assignment
        status = _make_mock_status(
            ams_data=[{"id": 0, "tray": [{"id": 1, "tray_info_idx": "GFB99", "tray_type": "ABS"}]}]
        )

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 1},
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            # Spool's preset wins — generic on slot must not be sticky
            assert call_kwargs.kwargs["tray_info_idx"] == "PFUScda4c46fc9031"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_preset_with_generic_on_slot_still_uses_generic(
        self, async_client: AsyncClient, printer_factory, spool_factory
    ):
        """Spool without preset + generic on slot → generic fallback (not slot reuse)."""
        printer = await printer_factory(name="P2S")
        spool = await spool_factory(slicer_filament=None, material="ABS")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        # Slot has generic ABS
        status = _make_mock_status(
            ams_data=[{"id": 0, "tray": [{"id": 1, "tray_info_idx": "GFB99", "tray_type": "ABS"}]}]
        )

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 1},
            )

            assert response.status_code == 200
            call_kwargs = mock_client.ams_set_filament_setting.call_args
            # Still gets generic, but via fallback — not via sticky reuse
            assert call_kwargs.kwargs["tray_info_idx"] == "GFB99"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_preset_reuses_specific_slot_preset(
        self, async_client: AsyncClient, printer_factory, spool_factory
    ):
        """Spool without preset + specific preset on slot → reuse slot's preset."""
        printer = await printer_factory(name="X1C")
        spool = await spool_factory(slicer_filament=None, material="PLA")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        # Slot has a specific Bambu PLA preset (not generic)
        status = _make_mock_status(
            ams_data=[{"id": 0, "tray": [{"id": 0, "tray_info_idx": "GFA05", "tray_type": "PLA"}]}]
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
            # Slot's specific preset is reused when spool has no own preset
            assert call_kwargs.kwargs["tray_info_idx"] == "GFA05"
