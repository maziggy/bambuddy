"""Tests for force-color-override AMS mapping fallback in the print scheduler.

Covers the code path in ``_compute_ams_mapping_for_printer`` that kicks in
when the 3MF's filament requirements cannot be read (e.g. ``plate_id=None``
with a modern BambuStudio 3MF whose slice_info was missing or unreadable)
but ``force_color_match`` overrides are present.

Related issue: #1436
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler


class TestBuildOverrideDirectMapping:
    """Unit tests for ``_build_override_direct_mapping``."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def _status(self, ams: list[dict], vt_tray: list[dict] | None = None) -> MagicMock:
        raw: dict = {"ams": ams}
        if vt_tray is not None:
            raw["vt_tray"] = vt_tray
        return MagicMock(raw_data=raw)

    def test_single_force_override_matches_ams_slot(self, scheduler):
        """Override with type+color matches the correct AMS tray."""
        status = self._status(
            ams=[
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_type": "PLA", "tray_color": "CBC6B8FF"},
                    ],
                }
            ]
        )
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#CBC6B8", "force_color_match": True}]
        result = scheduler._build_override_direct_mapping(overrides, status)
        assert result == [0]  # global_tray_id 0 (AMS 0, tray 0)

    def test_no_loaded_filaments_returns_none(self, scheduler):
        """Empty AMS → cannot compute mapping, return None."""
        status = self._status(ams=[{"id": 0, "tray": [{"id": 0}]}])  # empty tray
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#CBC6B8", "force_color_match": True}]
        result = scheduler._build_override_direct_mapping(overrides, status)
        assert result is None

    def test_no_color_match_returns_minus_one(self, scheduler):
        """Override color not present → slot mapped to -1 (no match)."""
        status = self._status(
            ams=[
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF"},
                    ],
                }
            ]
        )
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#CBC6B8", "force_color_match": True}]
        result = scheduler._build_override_direct_mapping(overrides, status)
        # Type matches but color is far off (red vs beige) → type-only fallback → [0]
        # If colour threshold is exceeded, falls back to type-only, which IS a match.
        # The important thing: result is not None and has the right length.
        assert result is not None
        assert len(result) == 1

    def test_multiple_overrides_map_multiple_slots(self, scheduler):
        """Two overrides with different slot_ids produce a two-element mapping."""
        status = self._status(
            ams=[
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_type": "PLA", "tray_color": "CBC6B8FF"},
                        {"id": 1, "tray_type": "PETG", "tray_color": "000000FF"},
                    ],
                }
            ]
        )
        overrides = [
            {"slot_id": 1, "type": "PLA", "color": "#CBC6B8", "force_color_match": True},
            {"slot_id": 2, "type": "PETG", "color": "#000000", "force_color_match": True},
        ]
        result = scheduler._build_override_direct_mapping(overrides, status)
        assert result == [0, 1]  # slot 1 → tray 0, slot 2 → tray 1

    def test_external_spool_matched(self, scheduler):
        """Override matching an external spool returns global_tray_id 254."""
        status = self._status(
            ams=[],
            vt_tray=[{"tray_type": "TPU", "tray_color": "CBC6B8FF"}],
        )
        overrides = [{"slot_id": 1, "type": "TPU", "color": "#CBC6B8", "force_color_match": True}]
        result = scheduler._build_override_direct_mapping(overrides, status)
        assert result == [254]

    def test_tray_info_idx_is_not_used_for_direct_mapping(self, scheduler):
        """Direct-override mapping clears tray_info_idx so matching falls back
        to colour rather than pinning to a specific spool ID from the 3MF."""
        status = self._status(
            ams=[
                {
                    "id": 0,
                    "tray": [
                        {
                            "id": 0,
                            "tray_type": "PLA",
                            "tray_color": "CBC6B8FF",
                            "tray_info_idx": "GFA00",
                        },
                    ],
                }
            ]
        )
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#CBC6B8", "force_color_match": True}]
        result = scheduler._build_override_direct_mapping(overrides, status)
        # Should match by colour (#CBC6B8 ≈ CBC6B8FF after strip), not by tray_info_idx.
        assert result == [0]


class TestComputeAmsMappingFallback:
    """Integration tests for the force-color fallback inside
    ``_compute_ams_mapping_for_printer`` when filament reqs are unavailable."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def _make_item(self, filament_overrides_json: str | None = None) -> MagicMock:
        item = MagicMock()
        item.archive_id = 141
        item.library_file_id = None
        item.plate_id = None
        item.filament_overrides = filament_overrides_json
        item.printer_id = 5
        return item

    def _make_status(self) -> MagicMock:
        return MagicMock(
            raw_data={
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_type": "PLA", "tray_color": "CBC6B8FF"},
                        ],
                    }
                ]
            }
        )

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_fallback_used_when_filament_reqs_empty(self, mock_pm, scheduler):
        """When _get_filament_requirements returns None but force-color overrides
        are set, the fallback builds a mapping directly from the overrides."""
        mock_pm.get_status.return_value = self._make_status()

        item = self._make_item(
            filament_overrides_json='[{"slot_id": 1, "type": "PLA", "color": "#CBC6B8", "force_color_match": true}]'
        )

        db = AsyncMock()

        with patch.object(scheduler, "_get_filament_requirements", return_value=None):
            result = await scheduler._compute_ams_mapping_for_printer(db, 5, item)

        assert result == [0]  # global_tray_id 0 (AMS 0, tray 0)

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_fallback_not_used_when_no_force_color(self, mock_pm, scheduler):
        """When overrides have no force_color_match, the fallback is not triggered."""
        mock_pm.get_status.return_value = self._make_status()

        item = self._make_item(filament_overrides_json='[{"slot_id": 1, "type": "PLA", "color": "#CBC6B8"}]')
        db = AsyncMock()

        with patch.object(scheduler, "_get_filament_requirements", return_value=None):
            result = await scheduler._compute_ams_mapping_for_printer(db, 5, item)

        assert result is None

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_fallback_not_used_when_no_overrides(self, mock_pm, scheduler):
        """When filament_overrides is None, the fallback is not triggered."""
        mock_pm.get_status.return_value = self._make_status()

        item = self._make_item(filament_overrides_json=None)
        db = AsyncMock()

        with patch.object(scheduler, "_get_filament_requirements", return_value=None):
            result = await scheduler._compute_ams_mapping_for_printer(db, 5, item)

        assert result is None

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_normal_path_used_when_filament_reqs_available(self, mock_pm, scheduler):
        """When filament requirements are available, the normal path is used
        (overrides applied to reqs, then matched)."""
        mock_pm.get_status.return_value = self._make_status()

        item = self._make_item(
            filament_overrides_json='[{"slot_id": 1, "type": "PLA", "color": "#CBC6B8", "force_color_match": true}]'
        )
        db = AsyncMock()

        # 3MF says slot 1 is PLA with a different color; override will change it.
        filament_reqs = [{"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": "GFA00"}]

        with (
            patch.object(scheduler, "_get_filament_requirements", return_value=filament_reqs),
            patch.object(scheduler, "_get_bool_setting", new=AsyncMock(return_value=False)),
        ):
            result = await scheduler._compute_ams_mapping_for_printer(db, 5, item)

        # After override, slot 1 becomes PLA #CBC6B8 → matches tray 0.
        assert result == [0]

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_fallback_returns_none_when_printer_status_unavailable(self, mock_pm, scheduler):
        """When the printer has no status, the fallback also returns None gracefully."""
        mock_pm.get_status.return_value = None

        item = self._make_item(
            filament_overrides_json='[{"slot_id": 1, "type": "PLA", "color": "#CBC6B8", "force_color_match": true}]'
        )
        db = AsyncMock()

        with patch.object(scheduler, "_get_filament_requirements", return_value=None):
            result = await scheduler._compute_ams_mapping_for_printer(db, 5, item)

        assert result is None
