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

        status = _make_mock_status(ams_data=[{"id": 2, "tray": [{"id": 3, "tray_info_idx": "", "tray_type": "PLA"}]}])

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

        status = _make_mock_status(ams_data=[{"id": 0, "tray": [{"id": 0, "tray_type": "PLA"}]}])

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

        status = _make_mock_status(ams_data=[{"id": 0, "tray": [{"id": 0, "tray_type": "ABS"}]}])

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


class TestAssignSpoolPresetMapping:
    """Tests that assign_spool saves the slot preset mapping for correct UI display."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_preset_mapping_saved_with_slicer_filament_name(
        self, async_client: AsyncClient, printer_factory, spool_factory
    ):
        """Slot preset mapping uses slicer_filament_name (not material+subtype)."""

        printer = await printer_factory(name="X1C")
        spool = await spool_factory(
            slicer_filament="GFA05",
            slicer_filament_name="Bambu PLA Silk",
            material="PLA",
            subtype="Silk",
            brand="Bambu",
        )

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        status = _make_mock_status(ams_data=[{"id": 0, "tray": [{"id": 1, "tray_type": "PLA"}]}])

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 1},
            )

        assert response.status_code == 200

        # Verify via the slot presets API
        presets_resp = await async_client.get(f"/api/v1/printers/{printer.id}/slot-presets")
        assert presets_resp.status_code == 200
        presets = presets_resp.json()
        # Key is str(ams_id * 4 + tray_id) — ams 0, tray 1 → "1"
        assert "1" in presets
        # Must use slicer_filament_name, NOT "PLA Silk" from material+subtype
        assert presets["1"]["preset_name"] == "Bambu PLA Silk"
        assert presets["1"]["preset_id"] == "GFSA05"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_preset_mapping_overwrites_old_mapping(
        self, async_client: AsyncClient, printer_factory, spool_factory, db_session: AsyncSession
    ):
        """Assigning a new spool overwrites the old slot preset mapping."""
        from backend.app.models.slot_preset import SlotPresetMapping

        printer = await printer_factory(name="X1C")

        # Pre-existing mapping (e.g. from previous manual configuration)
        old_mapping = SlotPresetMapping(
            printer_id=printer.id,
            ams_id=0,
            tray_id=2,
            preset_id="GFSA01",
            preset_name="Bambu PLA Matte",
            preset_source="cloud",
        )
        db_session.add(old_mapping)
        await db_session.commit()

        # Assign a "Generic PLA Silk" spool to same slot
        spool = await spool_factory(
            slicer_filament="GFL96",
            slicer_filament_name="Generic PLA Silk",
            material="PLA",
            subtype="Silk",
        )

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        status = _make_mock_status(ams_data=[{"id": 0, "tray": [{"id": 2, "tray_type": "PLA"}]}])

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 2},
            )

        assert response.status_code == 200

        # Verify via the slot presets API to avoid stale session cache
        presets_resp = await async_client.get(f"/api/v1/printers/{printer.id}/slot-presets")
        assert presets_resp.status_code == 200
        presets = presets_resp.json()
        # Key is str(ams_id * 4 + tray_id) — ams 0, tray 2 → "2"
        assert "2" in presets
        # Old "Bambu PLA Matte" must be overwritten
        assert presets["2"]["preset_name"] == "Generic PLA Silk"
        assert presets["2"]["preset_id"] == "GFSL96"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_preset_mapping_fallback_to_tray_sub_brands(
        self, async_client: AsyncClient, printer_factory, spool_factory
    ):
        """When slicer_filament_name is null, falls back to tray_sub_brands."""
        from backend.app.models.slot_preset import SlotPresetMapping

        printer = await printer_factory(name="A1M")
        spool = await spool_factory(
            slicer_filament="GFL05",
            slicer_filament_name=None,
            material="PLA",
            subtype="Matte",
            brand="Overture",
        )

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        status = _make_mock_status(ams_data=[{"id": 0, "tray": [{"id": 0, "tray_type": "PLA"}]}])

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
            )

        assert response.status_code == 200

        # Verify via the slot presets API
        presets_resp = await async_client.get(f"/api/v1/printers/{printer.id}/slot-presets")
        assert presets_resp.status_code == 200
        presets = presets_resp.json()
        # Key is str(ams_id * 4 + tray_id) — ams 0, tray 0 → "0"
        assert "0" in presets
        # Falls back to tray_sub_brands ("Overture PLA Matte")
        assert presets["0"]["preset_name"] == "Overture PLA Matte"


class TestAssignSpoolLiveCaliIdx:
    """P9-TEST-BE-3: assign_spool falls back to live tray cali_idx when no K-profile stored."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_kprofile_uses_live_cali_idx(self, async_client: AsyncClient, printer_factory, spool_factory):
        """When no KProfile row exists, live tray cali_idx is sent via extrusion_cali_sel."""
        printer = await printer_factory()
        spool = await spool_factory()

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        tray_data = {
            "id": 1,
            "cali_idx": 42,
            "tray_color": "FF0000FF",
            "tray_type": "PLA",
            "tray_sub_brands": "PLA Basic",
            "tray_id_name": "GFL99",
        }
        status = _make_mock_status(ams_data=[{"id": 0, "tray": [tray_data]}])

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 1},
            )

        assert response.status_code == 200
        mock_client.extrusion_cali_sel.assert_called_once()
        call_kwargs = mock_client.extrusion_cali_sel.call_args[1]
        assert call_kwargs["cali_idx"] == 42

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_kprofile_no_live_cali_idx_nothing_sent(
        self, async_client: AsyncClient, printer_factory, spool_factory
    ):
        """When tray has no cali_idx, extrusion_cali_sel is not called."""
        printer = await printer_factory()
        spool = await spool_factory()

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        tray_data = {
            "id": 0,
            "cali_idx": None,
            "tray_color": "FF0000FF",
            "tray_type": "PLA",
            "tray_sub_brands": "PLA Basic",
            "tray_id_name": "GFL99",
        }
        status = _make_mock_status(ams_data=[{"id": 0, "tray": [tray_data]}])

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
            )

        assert response.status_code == 200
        mock_client.extrusion_cali_sel.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_negative_live_cali_idx_not_sent(self, async_client: AsyncClient, printer_factory, spool_factory):
        """A negative live cali_idx (-1) is invalid and must not be sent."""
        printer = await printer_factory()
        spool = await spool_factory()

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        tray_data = {
            "id": 0,
            "cali_idx": -1,
            "tray_color": "FF0000FF",
            "tray_type": "PLA",
            "tray_sub_brands": "PLA Basic",
            "tray_id_name": "GFL99",
        }
        status = _make_mock_status(ams_data=[{"id": 0, "tray": [tray_data]}])

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
            )

        assert response.status_code == 200
        mock_client.extrusion_cali_sel.assert_not_called()


class TestAssignSpoolEmptySlotPreConfig:
    """SpoolBuddy primary workflow: weigh-then-assign before the spool is in the AMS.

    Bambu firmware silently drops ams_filament_setting / extrusion_cali_sel for
    unloaded slots — there's no filament context for the cali_idx to attach to.
    The endpoint persists the SpoolAssignment row with an empty fingerprint_type
    (the "pending config" marker) and skips the MQTT publish; on_ams_change
    re-fires the full configuration when filament is later inserted.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_slot_skips_mqtt_but_persists_assignment(
        self, async_client: AsyncClient, printer_factory, spool_factory
    ):
        """Assigning to an empty slot skips MQTT and returns pending_config=True."""
        printer = await printer_factory(name="H2D")
        spool = await spool_factory(slicer_filament="GFL05", material="PLA")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        # Slot found but empty (tray_type=""): the SpoolBuddy scenario
        status = _make_mock_status(ams_data=[{"id": 2, "tray": [{"id": 3, "tray_type": ""}]}])

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 2, "tray_id": 3},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["pending_config"] is True
        assert body["configured"] is False
        # Critical: no MQTT was published (firmware would drop it)
        mock_client.ams_set_filament_setting.assert_not_called()
        mock_client.extrusion_cali_sel.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_slot_no_ams_data_skips_mqtt(self, async_client: AsyncClient, printer_factory, spool_factory):
        """No AMS data at all (printer offline, no telemetry yet) → still pre-config."""
        printer = await printer_factory(name="X1C")
        spool = await spool_factory(slicer_filament="GFL05", material="PLA")

        mock_client = MagicMock()

        # No AMS data — fingerprint_type stays None, treated as empty
        status = _make_mock_status(ams_data=[])

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
            )

        assert response.status_code == 200
        assert response.json()["pending_config"] is True
        mock_client.ams_set_filament_setting.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_loaded_slot_publishes_mqtt_immediately(
        self, async_client: AsyncClient, printer_factory, spool_factory
    ):
        """Loaded slot (tray_type non-empty) → MQTT fires + pending_config=False."""
        printer = await printer_factory(name="X1C")
        spool = await spool_factory(slicer_filament="GFL05", material="PLA")

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        status = _make_mock_status(
            ams_data=[{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_info_idx": "GFL05"}]}]
        )

        with patch("backend.app.services.printer_manager.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            mock_pm.get_status.return_value = status

            response = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["pending_config"] is False
        assert body["configured"] is True
        mock_client.ams_set_filament_setting.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_on_ams_change_fires_config_when_pre_assigned_slot_loads(
        self, async_client: AsyncClient, printer_factory, spool_factory, db_session: AsyncSession
    ):
        """Pre-config replay: SpoolAssignment with empty fingerprint + slot now loaded → MQTT fires."""
        from unittest.mock import AsyncMock

        from backend.app.main import on_ams_change
        from backend.app.models.spool_assignment import SpoolAssignment

        printer = await printer_factory(name="H2D")
        spool = await spool_factory(slicer_filament="GFL05", material="PLA")

        # Pre-existing assignment with empty fingerprint (the SpoolBuddy state)
        pre_assignment = SpoolAssignment(
            spool_id=spool.id,
            printer_id=printer.id,
            ams_id=2,
            tray_id=3,
            fingerprint_color=None,
            fingerprint_type=None,
        )
        db_session.add(pre_assignment)
        await db_session.commit()

        # Filament has now been physically inserted into the slot.
        # state=11 ("filament fed to extruder") is the load signal we trigger on.
        ams_data = [{"id": 2, "tray": [{"id": 3, "tray_type": "PLA", "tray_color": "FF0000FF", "state": 11}]}]

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        status = _make_mock_status(ams_data=ams_data)
        printer_info = MagicMock(name="H2D", serial_number="0948BB540200427")

        with (
            patch("backend.app.main.printer_manager") as mock_pm_main,
            patch("backend.app.services.printer_manager.printer_manager") as mock_pm_inv,
            patch("backend.app.main.mqtt_relay") as mock_relay,
            patch("backend.app.main.ws_manager") as mock_ws,
        ):
            mock_pm_main.get_printer.return_value = printer_info
            mock_pm_main.get_status.return_value = status
            mock_pm_main.get_client.return_value = mock_client
            mock_pm_main.get_model.return_value = "H2D"
            mock_pm_inv.get_client.return_value = mock_client
            mock_pm_inv.get_status.return_value = status
            mock_relay.on_ams_change = AsyncMock()
            mock_ws.send_printer_status = AsyncMock()
            mock_ws.broadcast = AsyncMock()

            await on_ams_change(printer.id, ams_data)

        # Full filament setting was published when the slot transitioned to loaded
        mock_client.ams_set_filament_setting.assert_called_once()
        call_kwargs = mock_client.ams_set_filament_setting.call_args.kwargs
        assert call_kwargs["ams_id"] == 2
        assert call_kwargs["tray_id"] == 3
        assert call_kwargs["tray_info_idx"] == "GFL05"

        # Fingerprint was updated so the next push doesn't re-fire
        await db_session.refresh(pre_assignment)
        assert pre_assignment.fingerprint_type == "PLA"
        assert pre_assignment.fingerprint_color == "FF0000FF"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_on_ams_change_does_not_refire_for_already_configured_slot(
        self, async_client: AsyncClient, printer_factory, spool_factory, db_session: AsyncSession
    ):
        """Once fingerprint_type is set, subsequent AMS pushes must not re-fire MQTT."""
        from unittest.mock import AsyncMock

        from backend.app.main import on_ams_change
        from backend.app.models.spool_assignment import SpoolAssignment

        printer = await printer_factory(name="X1C")
        spool = await spool_factory(slicer_filament="GFL05", material="PLA")

        # Assignment already configured (fingerprint stamped)
        configured_assignment = SpoolAssignment(
            spool_id=spool.id,
            printer_id=printer.id,
            ams_id=0,
            tray_id=0,
            fingerprint_color="FF0000FF",
            fingerprint_type="PLA",
        )
        db_session.add(configured_assignment)
        await db_session.commit()

        ams_data = [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF", "state": 11}]}]

        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True

        status = _make_mock_status(ams_data=ams_data)
        printer_info = MagicMock(name="X1C", serial_number="00M00A391800004")

        with (
            patch("backend.app.main.printer_manager") as mock_pm_main,
            patch("backend.app.services.printer_manager.printer_manager") as mock_pm_inv,
            patch("backend.app.main.mqtt_relay") as mock_relay,
            patch("backend.app.main.ws_manager") as mock_ws,
        ):
            mock_pm_main.get_printer.return_value = printer_info
            mock_pm_main.get_status.return_value = status
            mock_pm_main.get_client.return_value = mock_client
            mock_pm_main.get_model.return_value = "X1C"
            mock_pm_inv.get_client.return_value = mock_client
            mock_pm_inv.get_status.return_value = status
            mock_relay.on_ams_change = AsyncMock()
            mock_ws.send_printer_status = AsyncMock()
            mock_ws.broadcast = AsyncMock()

            await on_ams_change(printer.id, ams_data)

        # Fingerprint was already set — re-fire path skipped
        mock_client.ams_set_filament_setting.assert_not_called()
