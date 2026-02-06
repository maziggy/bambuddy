"""Unit tests for Spoolman tracking service helpers."""

import pytest

from backend.app.services.spoolman_tracking import (
    _resolve_global_tray_id,
    _resolve_spool_tag,
    build_ams_tray_lookup,
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
            "vt_tray": {"tray_uuid": "EXT", "tag_uid": "X", "tray_type": "TPU"},
        }
        lookup = build_ams_tray_lookup(raw)
        assert 254 in lookup
        assert lookup[254]["tray_type"] == "TPU"

    def test_empty_external_spool_skipped(self):
        raw = {"ams": [], "vt_tray": {"tray_type": ""}}
        lookup = build_ams_tray_lookup(raw)
        assert 254 not in lookup

    def test_no_ams_data(self):
        assert build_ams_tray_lookup({}) == {}
        assert build_ams_tray_lookup({"ams": []}) == {}

    def test_missing_fields_default(self):
        raw = {"ams": [{"id": 0, "tray": [{"id": 0}]}]}
        lookup = build_ams_tray_lookup(raw)
        assert lookup[0] == {"tray_uuid": "", "tag_uid": "", "tray_type": ""}
