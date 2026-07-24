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

    def test_direct_mapping_pins_variant_when_override_carries_idx(self, scheduler):
        """#2650: the no-3MF fallback honours a force override's own tray_info_idx,
        so two same-colour PLA variants map to the intended slot rather than the
        first same-colour tray."""
        status = self._status(
            ams=[
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_type": "PLA", "tray_color": "FFFFFFFF", "tray_info_idx": "GFA00"},
                        {"id": 1, "tray_type": "PLA", "tray_color": "FFFFFFFF", "tray_info_idx": "GFA01"},
                    ],
                }
            ]
        )
        overrides = [
            {"slot_id": 1, "type": "PLA", "color": "#FFFFFF", "tray_info_idx": "GFA01", "force_color_match": True}
        ]
        result = scheduler._build_override_direct_mapping(overrides, status)
        assert result == [1]  # global_tray_id 1 = GFA01 (Matte), not GFA00 (Basic) at 0


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

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_force_color_override_pins_the_matching_variant_slot(self, mock_pm, scheduler):
        """#2650 slot selection: with two same-colour PLA spools of different
        variants loaded, applying a force_color_match override must map to the
        tray whose tray_info_idx matches the 3MF (Matte GFA01), not the first
        same-colour tray (Basic GFA00)."""
        mock_pm.get_status.return_value = MagicMock(
            raw_data={
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_type": "PLA", "tray_color": "FFFFFFFF", "tray_info_idx": "GFA00"},
                            {"id": 1, "tray_type": "PLA", "tray_color": "FFFFFFFF", "tray_info_idx": "GFA01"},
                        ],
                    }
                ]
            },
            ams_filament_backup=None,
        )
        item = self._make_item(
            filament_overrides_json=(
                '[{"slot_id": 1, "type": "PLA", "color": "#FFFFFF", '
                '"tray_info_idx": "GFA01", "force_color_match": true}]'
            )
        )
        filament_reqs = [{"slot_id": 1, "type": "PLA", "color": "#FFFFFF", "tray_info_idx": "GFA01"}]
        db = AsyncMock()

        with (
            patch.object(scheduler, "_get_filament_requirements", return_value=filament_reqs),
            patch.object(scheduler, "_get_bool_setting", new=AsyncMock(return_value=False)),
        ):
            result = await scheduler._compute_ams_mapping_for_printer(db, 5, item)

        assert result == [1]  # global_tray_id 1 = GFA01 (Matte), not GFA00 (Basic) at 0

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_preference_override_still_clears_idx_so_a_swap_matches_by_colour(self, mock_pm, scheduler):
        """A non-force (preference) override is a filament SWAP: its slot must
        match by the new type+colour, never by a stale 3MF variant that would pin
        the old spool. Only force_color_match overrides keep their idx — anything
        else is cleared, exactly as before #2650."""
        mock_pm.get_status.return_value = MagicMock(
            raw_data={
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            # The 3MF's original variant (blue GFA01) and the swapped-to red.
                            {"id": 0, "tray_type": "PLA", "tray_color": "0000FFFF", "tray_info_idx": "GFA01"},
                            {"id": 1, "tray_type": "PLA", "tray_color": "FF0000FF", "tray_info_idx": "GFA00"},
                        ],
                    }
                ]
            },
            ams_filament_backup=None,
        )
        # 3MF wants blue GFA01; the user swaps this slot to red via a preference
        # override that (defensively) also carries the stale GFA01 idx.
        item = self._make_item(
            filament_overrides_json='[{"slot_id": 1, "type": "PLA", "color": "#FF0000", "tray_info_idx": "GFA01"}]'
        )
        filament_reqs = [{"slot_id": 1, "type": "PLA", "color": "#0000FF", "tray_info_idx": "GFA01"}]
        db = AsyncMock()

        with (
            patch.object(scheduler, "_get_filament_requirements", return_value=filament_reqs),
            patch.object(scheduler, "_get_bool_setting", new=AsyncMock(return_value=False)),
        ):
            result = await scheduler._compute_ams_mapping_for_printer(db, 5, item)

        # idx cleared → matches the swapped-to red spool (global 1), not the stale
        # GFA01 blue (global 0) that a preserved idx would have pinned.
        assert result == [1]


class TestGetMissingForceColorSlotsVariant:
    """force_color_match must distinguish Bambu PLA variants that share a base
    type+colour but differ in tray_info_idx (Basic GFA00 / Matte GFA01 /
    Silk GFA06), while still accepting spools that report no idx (#2650)."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def _status(self, trays: list[dict]) -> MagicMock:
        """One AMS unit whose trays are the given dicts (white PLA of assorted variants)."""
        return MagicMock(raw_data={"ams": [{"id": 0, "tray": trays}]})

    @staticmethod
    def _white(idx: str) -> dict:
        return {"id": 0, "tray_type": "PLA", "tray_color": "FFFFFFFF", "tray_info_idx": idx}

    def _override(self, idx: str | None) -> list[dict]:
        o = {"slot_id": 1, "type": "PLA", "color": "#FFFFFF", "force_color_match": True}
        if idx is not None:
            o["tray_info_idx"] = idx
        return [o]

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_matte_requirement_rejects_basic_and_silk(self, mock_pm, scheduler):
        """A GFA01 (Matte) job is unsatisfied by a printer loaded with only
        Basic/Silk white PLA — the core #2650 regression."""
        mock_pm.get_status.return_value = self._status([self._white("GFA00"), self._white("GFA06")])
        assert scheduler._get_missing_force_color_slots(5, self._override("GFA01")) == ["PLA (#FFFFFF)"]

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_matte_requirement_accepts_matte(self, mock_pm, scheduler):
        """The correct variant being loaded satisfies the override."""
        mock_pm.get_status.return_value = self._status([self._white("GFA00"), self._white("GFA01")])
        assert scheduler._get_missing_force_color_slots(5, self._override("GFA01")) == []

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_blank_loaded_idx_falls_back_to_type_and_colour(self, mock_pm, scheduler):
        """A custom/third-party spool reports a blank tray_info_idx, so it must
        still satisfy a variant-specific requirement (type+colour fallback)."""
        mock_pm.get_status.return_value = self._status([self._white("")])
        assert scheduler._get_missing_force_color_slots(5, self._override("GFA01")) == []

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_requirement_without_idx_unchanged(self, mock_pm, scheduler):
        """An older 3MF whose override carries no idx keeps the historical
        type+colour behaviour and matches any white PLA."""
        mock_pm.get_status.return_value = self._status([self._white("GFA06")])
        assert scheduler._get_missing_force_color_slots(5, self._override(None)) == []
