"""Unit tests for Spoolman tracking service helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.spoolman_tracking import (
    _get_fallback_spool_tag,
    _global_tray_id_to_ams_slot,
    _hash_serial_to_hex32,
    _resolve_global_tray_id,
    _resolve_spool_tag,
    build_ams_tray_lookup,
    store_print_data,
)


class TestResolveSpoolTag:
    """Tests for _resolve_spool_tag()."""

    def test_prefers_tray_uuid(self):
        tray = {"tray_uuid": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4", "tag_uid": "DEADBEEF"}
        assert _resolve_spool_tag(tray) == "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"

    def test_falls_back_to_tag_uid(self):
        tray = {"tray_uuid": "", "tag_uid": "DEADBEEF"}
        assert _resolve_spool_tag(tray) == "DEADBEEF"

    def test_skips_zero_uuid(self):
        tray = {"tray_uuid": "00000000000000000000000000000000", "tag_uid": "DEADBEEF"}
        assert _resolve_spool_tag(tray) == "DEADBEEF"

    def test_rejects_zero_tag_uid(self):
        tray = {"tray_uuid": "", "tag_uid": "0000000000000000"}
        assert _resolve_spool_tag(tray) == ""

    def test_uses_fallback_tag_when_ids_missing(self):
        tray = {"tray_uuid": "", "tag_uid": ""}
        # global_tray_id 0 -> ams_id 0, tray_id 0
        assert _resolve_spool_tag(tray, "01P00A000000000", 0) == "96D80B9100000000"

    def test_uses_fallback_tag_when_ids_zero(self):
        tray = {"tray_uuid": "00000000000000000000000000000000", "tag_uid": "0000000000000000"}
        # global_tray_id 5 -> ams_id 1, tray_id 1
        assert _resolve_spool_tag(tray, "01P00A000000000", 5) == "96D80B9100010001"

    def test_empty_both(self):
        tray = {"tray_uuid": "", "tag_uid": ""}
        assert _resolve_spool_tag(tray) == ""

    def test_missing_keys(self):
        assert _resolve_spool_tag({}) == ""

    def test_zero_uuid_no_tag(self):
        tray = {"tray_uuid": "00000000000000000000000000000000", "tag_uid": ""}
        assert _resolve_spool_tag(tray) == ""


class TestResolveGlobalTrayId:
    """Tests for _resolve_global_tray_id()."""

    def test_default_mapping(self):
        """slot 1 -> tray 0, slot 2 -> tray 1, etc."""
        assert _resolve_global_tray_id(1, None) == 0
        assert _resolve_global_tray_id(2, None) == 1
        assert _resolve_global_tray_id(4, None) == 3

    def test_custom_mapping(self):
        """Custom slot_to_tray overrides default."""
        mapping = [5, 2, -1, 0]
        assert _resolve_global_tray_id(1, mapping) == 5
        assert _resolve_global_tray_id(2, mapping) == 2
        assert _resolve_global_tray_id(4, mapping) == 0

    def test_unmapped_slot(self):
        """Slot with -1 in mapping uses default."""
        mapping = [5, -1, 2, 0]
        assert _resolve_global_tray_id(2, mapping) == 1  # default: slot 2 -> tray 1

    def test_slot_beyond_mapping(self):
        """Slot beyond mapping length uses default."""
        mapping = [5, 2]
        assert _resolve_global_tray_id(3, mapping) == 2  # default: slot 3 -> tray 2

    def test_empty_mapping(self):
        mapping = []
        assert _resolve_global_tray_id(1, mapping) == 0


class TestFallbackTagHelpers:
    """Tests for frontend-mirrored fallback tag helpers."""

    def test_hash_serial_matches_frontend_algorithm(self):
        assert _hash_serial_to_hex32("01P00A000000000") == "96D80B91"
        # Frontend trims and uppercases before hashing
        assert _hash_serial_to_hex32(" 01p00a000000000 ") == "96D80B91"

    def test_global_tray_to_ams_slot_standard_ams(self):
        assert _global_tray_id_to_ams_slot(0) == (0, 0)
        assert _global_tray_id_to_ams_slot(7) == (1, 3)

    def test_global_tray_to_ams_slot_ams_ht(self):
        assert _global_tray_id_to_ams_slot(128) == (128, 0)
        assert _global_tray_id_to_ams_slot(135) == (135, 0)

    def test_global_tray_to_ams_slot_external(self):
        assert _global_tray_id_to_ams_slot(254) == (255, 0)
        assert _global_tray_id_to_ams_slot(255) == (255, 1)

    def test_get_fallback_spool_tag_standard(self):
        assert _get_fallback_spool_tag("01P00A000000000", 5) == "96D80B9100010001"

    def test_get_fallback_spool_tag_ams_ht(self):
        assert _get_fallback_spool_tag("01P00A000000000", 128) == "96D80B9100800000"

    def test_get_fallback_spool_tag_external(self):
        assert _get_fallback_spool_tag("01P00A000000000", 255) == "96D80B9100FF0001"


class TestBuildAmsTrayLookup:
    """Tests for build_ams_tray_lookup()."""

    def test_single_ams_unit(self):
        raw = {
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_uuid": "AAA", "tag_uid": "111", "tray_type": "PLA"},
                        {"id": 1, "tray_uuid": "BBB", "tag_uid": "222", "tray_type": "ABS"},
                    ],
                }
            ]
        }
        lookup = build_ams_tray_lookup(raw)
        assert lookup[0] == {"tray_uuid": "AAA", "tag_uid": "111", "tray_type": "PLA"}
        assert lookup[1] == {"tray_uuid": "BBB", "tag_uid": "222", "tray_type": "ABS"}

    def test_multiple_ams_units(self):
        raw = {
            "ams": [
                {"id": 0, "tray": [{"id": 0, "tray_uuid": "A", "tag_uid": "", "tray_type": "PLA"}]},
                {"id": 1, "tray": [{"id": 0, "tray_uuid": "B", "tag_uid": "", "tray_type": "PETG"}]},
            ]
        }
        lookup = build_ams_tray_lookup(raw)
        assert 0 in lookup  # AMS 0, tray 0
        assert 4 in lookup  # AMS 1, tray 0 (1*4+0)
        assert lookup[4]["tray_uuid"] == "B"

    def test_external_spool(self):
        raw = {
            "ams": [],
            "vt_tray": [{"tray_uuid": "EXT", "tag_uid": "X", "tray_type": "TPU"}],
        }
        lookup = build_ams_tray_lookup(raw)
        assert 254 in lookup
        assert lookup[254]["tray_type"] == "TPU"

    def test_empty_external_spool_skipped(self):
        raw = {"ams": [], "vt_tray": [{"tray_type": ""}]}
        lookup = build_ams_tray_lookup(raw)
        assert 254 not in lookup

    def test_no_ams_data(self):
        assert build_ams_tray_lookup({}) == {}
        assert build_ams_tray_lookup({"ams": []}) == {}

    def test_missing_fields_default(self):
        raw = {"ams": [{"id": 0, "tray": [{"id": 0}]}]}
        lookup = build_ams_tray_lookup(raw)
        assert lookup[0] == {"tray_uuid": "", "tag_uid": "", "tray_type": ""}


class TestStorePrintData:
    """Tests for store_print_data()."""

    @pytest.mark.asyncio
    async def test_prefers_explicit_ams_mapping_over_queue_mapping(self):
        db = AsyncMock()
        delete_result = MagicMock()
        db.execute = AsyncMock(side_effect=[delete_result])
        db.add = MagicMock()
        db.commit = AsyncMock()

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA"}, {"id": 1, "tray_type": "PLA"}]}]}
        )

        mock_settings = MagicMock()
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_settings.base_dir.__truediv__.return_value = mock_path

        with (
            patch("backend.app.services.spoolman_tracking.app_settings", mock_settings),
            patch("backend.app.api.routes.settings.get_setting", AsyncMock(side_effect=["true", "true"])),
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=[{"slot_id": 1, "used_g": 3.83, "type": "PLA", "color": "#FF0000"}],
            ),
            patch("backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf", return_value=None),
            patch("backend.app.utils.threemf_tools.extract_filament_properties_from_3mf", return_value={}),
        ):
            await store_print_data(
                printer_id=1,
                archive_id=15,
                file_path="archives/test.3mf",
                db=db,
                printer_manager=printer_manager,
                ams_mapping=[1, -1, -1, -1],
            )

        db.add.assert_called_once()
        tracking = db.add.call_args.args[0]
        assert tracking.slot_to_tray == [1, -1, -1, -1]
        db.execute.assert_called_once()
