"""Force color match on a printer-pinned queue item (#3280).

A virtual printer in queue mode with Force color match queued a job for one
A1. The file wanted light grey PLA (#A4AAAC); the AMS held black, blue grey and
white PLA, none of them that grey. The model-based path would have held the
job, but the pinned path went straight to the matcher, which was never told the
slot was forced: with no exact or similar colour it took the first PLA tray
(``type_only``) and the job printed in black.

A forced slot is satisfied only by what ``_get_missing_force_color_slots``
accepts: the same type, the exact colour, and the same variant when both sides
carry a ``tray_info_idx``. Anything less leaves the slot unresolved, and the
unmatched-filament hold (#2799) stages the job instead of dispatching it.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler

# The 3MF as sliced; the force override below rewrites slot 1.
REQUIREMENTS = [{"slot_id": 1, "type": "PLA", "color": "#A4AAAC", "tray_info_idx": "GFA00"}]

FORCED = [{"slot_id": 1, "type": "PLA", "color": "#A4AAAC", "tray_info_idx": "GFA00", "force_color_match": True}]

# The reporter's A1 AMS Lite at dispatch.
A1_TRAYS = [
    {"global_tray_id": 0, "type": "PETG", "color": "FFFFFFFF", "tray_info_idx": "GFG00"},
    {"global_tray_id": 1, "type": "PLA", "color": "000000FF", "tray_info_idx": "GFA00"},
    {"global_tray_id": 2, "type": "PLA", "color": "5A657BFF", "tray_info_idx": "GFA00"},
    {"global_tray_id": 3, "type": "PLA", "color": "FFFFFFFF", "tray_info_idx": "GFA00"},
]


def _item(**overrides):
    item = MagicMock()
    item.id = 495
    item.printer_id = 4
    item.archive_id = 460
    item.library_file_id = None
    item.plate_id = None
    item.ams_mapping = None
    item.skip_filament_check = False
    item.filament_overrides = json.dumps(FORCED)
    item.required_filament_types = None
    item.manual_start = False
    item.filament_short = False
    item.waiting_reason = None
    for key, value in overrides.items():
        setattr(item, key, value)
    return item


def _scheduler(trays, requirements=REQUIREMENTS):
    scheduler = PrintScheduler()
    scheduler._build_loaded_filaments = MagicMock(return_value=[dict(t) for t in trays])
    scheduler._get_filament_requirements = AsyncMock(
        return_value=[dict(r) for r in requirements] if requirements else None
    )
    scheduler._get_bool_setting = AsyncMock(return_value=False)
    scheduler._get_job_name = AsyncMock(return_value="bracket")
    scheduler._get_printer = AsyncMock(return_value=MagicMock(model="A1"))
    return scheduler


def _forced_req(color="#A4AAAC", idx="GFA00"):
    return {"slot_id": 1, "type": "PLA", "color": color, "tray_info_idx": idx, "force_color_match": True}


def _tray(gtid, color, idx="GFA00", type_="PLA"):
    return {"global_tray_id": gtid, "type": type_, "color": color, "tray_info_idx": idx}


class TestMatcherHonoursForcedSlots:
    """``_match_filaments_to_slots`` never satisfies a forced slot loosely."""

    def test_the_reported_case_is_left_unresolved(self):
        """No #A4AAAC PLA loaded: type_only used to pick the black tray."""
        assert PrintScheduler()._match_filaments_to_slots([_forced_req()], A1_TRAYS) == [-1]

    def test_a_similar_colour_does_not_satisfy_a_forced_slot(self):
        """Within the similar-colour box, but not the colour the user forced."""
        trays = [_tray(0, "A0A8AAFF")]
        assert PrintScheduler()._match_filaments_to_slots([_forced_req()], trays) == [-1]

    def test_a_unique_variant_in_the_wrong_colour_does_not_satisfy_it(self):
        """The idx bucket alone never looked at colour."""
        trays = [_tray(0, "000000FF", idx="GFA00"), _tray(1, "A4AAACFF", idx="GFA01")]
        assert PrintScheduler()._match_filaments_to_slots([_forced_req()], trays) == [-1]

    def test_the_exact_colour_is_matched(self):
        trays = [_tray(0, "000000FF"), _tray(1, "A4AAACFF")]
        assert PrintScheduler()._match_filaments_to_slots([_forced_req()], trays) == [1]

    def test_the_exact_colour_in_the_right_variant_wins_over_another_variant(self):
        """#2650: two same-colour spools, the forced variant picks between them."""
        trays = [_tray(0, "A4AAACFF", idx="GFA01"), _tray(1, "A4AAACFF", idx="GFA00")]
        assert PrintScheduler()._match_filaments_to_slots([_forced_req()], trays) == [1]

    def test_the_named_variant_wins_over_a_spool_that_names_none(self):
        """Both are acceptable; the one that is known to be right comes first."""
        trays = [_tray(0, "A4AAACFF", idx=""), _tray(1, "A4AAACFF", idx="GFA00")]
        assert PrintScheduler()._match_filaments_to_slots([_forced_req()], trays) == [1]

    def test_a_spool_without_a_variant_still_counts(self):
        """Third-party spools report no idx; the force check accepts them too."""
        trays = [_tray(0, "A4AAACFF", idx="")]
        assert PrintScheduler()._match_filaments_to_slots([_forced_req()], trays) == [0]

    def test_an_unforced_slot_does_not_take_the_forced_slots_only_tray(self):
        """Slot 1 would take any PLA; slot 2 can only use tray 0. Matched in slot
        order, slot 1 took tray 0 and slot 2 was held with its spool loaded."""
        unforced = {"slot_id": 1, "type": "PLA", "color": "#FFFFFF", "tray_info_idx": ""}
        forced = {**_forced_req(), "slot_id": 2}
        trays = [_tray(0, "A4AAACFF"), _tray(1, "000000FF")]
        assert PrintScheduler()._match_filaments_to_slots([unforced, forced], trays) == [1, 0]

    def test_an_unforced_slot_still_falls_back_to_type_only(self):
        """Nothing changes for jobs that did not ask for strict matching."""
        req = {"slot_id": 1, "type": "PLA", "color": "#A4AAAC", "tray_info_idx": ""}
        assert PrintScheduler()._match_filaments_to_slots([req], A1_TRAYS) == [1]


class TestOverridesCarryTheForceFlag:
    def test_a_force_override_marks_the_requirement(self):
        scheduler = PrintScheduler()
        reqs = [dict(r) for r in REQUIREMENTS]
        scheduler._apply_filament_overrides(_item(), reqs)
        assert reqs[0]["force_color_match"] is True

    def test_a_preference_override_does_not(self):
        scheduler = PrintScheduler()
        reqs = [dict(r) for r in REQUIREMENTS]
        pref = [{"slot_id": 1, "type": "PLA", "color": "#000000"}]
        scheduler._apply_filament_overrides(_item(filament_overrides=json.dumps(pref)), reqs)
        assert not reqs[0].get("force_color_match")

    def test_the_no_3mf_fallback_is_strict_too(self):
        """#1436's direct mapping from the overrides alone."""
        status = MagicMock(
            raw_data={
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_type": "PLA", "tray_color": "000000FF", "tray_info_idx": "GFA00"},
                        ],
                    }
                ]
            }
        )
        assert PrintScheduler()._build_override_direct_mapping(FORCED, status) == [-1]


class TestPinnedItemIsHeld:
    """The pinned dispatch path: ``_ensure_ams_mapping`` then the unmatched hold."""

    @pytest.mark.asyncio
    async def test_the_reported_job_is_held_instead_of_printing_in_black(self):
        scheduler = _scheduler(A1_TRAYS)
        item = _item()
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock(raw_data={"ams": [{"id": 0}]}, ams_filament_backup=None)
            unmappable = await scheduler._ensure_ams_mapping(db, 4, item)
            blocked = await scheduler._block_on_unmatched_filament(db, item)

        assert unmappable is None
        assert blocked is True
        assert item.manual_start is True
        assert item.waiting_reason == "Needs PLA #A4AAAC"

    @pytest.mark.asyncio
    async def test_the_job_goes_out_once_the_colour_is_loaded(self):
        trays = [*A1_TRAYS[:3], _tray(3, "A4AAACFF")]
        scheduler = _scheduler(trays)
        item = _item()
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock(raw_data={"ams": [{"id": 0}]}, ams_filament_backup=None)
            await scheduler._ensure_ams_mapping(db, 4, item)
            blocked = await scheduler._block_on_unmatched_filament(db, item)

        assert json.loads(item.ams_mapping) == [3]
        assert blocked is False

    @pytest.mark.asyncio
    async def test_a_stored_mapping_onto_the_wrong_colour_is_not_trusted(self):
        """A mapping computed earlier (or by hand) that points the forced slot at
        the black spool fits by type, which was all the conflict check asked."""
        scheduler = _scheduler(A1_TRAYS)
        item = _item(ams_mapping=json.dumps([1]))
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock(raw_data={"ams": [{"id": 0}]}, ams_filament_backup=None)
            conflict = await scheduler._stored_mapping_conflict(db, 4, item, [1])
            await scheduler._ensure_ams_mapping(db, 4, item)
            blocked = await scheduler._block_on_unmatched_filament(db, item)

        assert conflict is not None
        assert "#A4AAAC" in conflict
        assert blocked is True

    @pytest.mark.asyncio
    async def test_a_stored_mapping_onto_the_forced_colour_is_kept(self):
        trays = [*A1_TRAYS[:3], _tray(3, "A4AAACFF")]
        scheduler = _scheduler(trays)
        item = _item(ams_mapping=json.dumps([3]))
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock(raw_data={"ams": [{"id": 0}]}, ams_filament_backup=None)
            assert await scheduler._stored_mapping_conflict(db, 4, item, [3]) is None

    @pytest.mark.asyncio
    async def test_print_anyway_still_dispatches(self):
        scheduler = _scheduler(A1_TRAYS)
        item = _item(ams_mapping=json.dumps([-1]), skip_filament_check=True)
        assert await scheduler._block_on_unmatched_filament(AsyncMock(), item) is False

    @pytest.mark.asyncio
    async def test_an_unreadable_3mf_is_held_on_the_overrides(self):
        """No requirements from the file: the forced overrides are the requirements."""
        scheduler = _scheduler(A1_TRAYS, requirements=None)
        item = _item(ams_mapping=json.dumps([-1]))
        db = AsyncMock()

        blocked = await scheduler._block_on_unmatched_filament(db, item)

        assert blocked is True
        assert item.waiting_reason == "Needs PLA #A4AAAC"

    @pytest.mark.asyncio
    async def test_start_on_an_unreadable_3mf_names_the_forced_colour(self):
        """Start must answer like the hold, or it releases a job the next pass
        holds again, with no Print Anyway offered in between."""
        scheduler = _scheduler(A1_TRAYS, requirements=None)
        item = _item(ams_mapping=json.dumps([-1]))

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock(raw_data={"ams": [{"id": 0}]}, ams_filament_backup=None)
            missing = await scheduler.missing_filament_for_start(AsyncMock(), item)

        assert missing == ["PLA #A4AAAC"]


class TestPrinterWithoutAms:
    """#2771 fails an unmatchable item on a printer with only the external spool.
    A forced colour is something the user can load there, so it is held instead."""

    @pytest.mark.asyncio
    async def test_a_forced_colour_is_not_failed(self):
        external = [{"global_tray_id": 254, "type": "PLA", "color": "000000FF", "tray_info_idx": "GFA00"}]
        scheduler = _scheduler(external)
        item = _item()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock(raw_data={"ams": [], "vt_tray": [{}]})
            message = await scheduler._unmappable_without_ams_message(AsyncMock(), 4, item, [-1])

        assert message is None

    @pytest.mark.asyncio
    async def test_an_unforced_mismatch_still_fails(self):
        external = [{"global_tray_id": 254, "type": "PETG", "color": "000000FF", "tray_info_idx": ""}]
        scheduler = _scheduler(external)
        item = _item(filament_overrides=None)

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock(raw_data={"ams": [], "vt_tray": [{}]})
            message = await scheduler._unmappable_without_ams_message(AsyncMock(), 4, item, [-1])

        assert message is not None


class TestOneSpoolPerForcedSlot:
    """Two forced slots asking for the same filament need two spools of it.

    The matcher never gives one tray to two slots, so a pre-check that let one
    spool satisfy both picked a printer the matcher then could not map, and an
    "Any model" job was held, released and picked again without end.
    """

    @staticmethod
    def _status(trays):
        return MagicMock(raw_data={"ams": [{"id": 0, "tray": trays}]})

    @staticmethod
    def _white(tray_id, idx="GFA00"):
        return {"id": tray_id, "tray_type": "PLA", "tray_color": "FFFFFFFF", "tray_info_idx": idx}

    @staticmethod
    def _override(slot_id, idx=None):
        o = {"slot_id": slot_id, "type": "PLA", "color": "#FFFFFF", "force_color_match": True}
        if idx is not None:
            o["tray_info_idx"] = idx
        return o

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_one_spool_does_not_satisfy_two_slots(self, mock_pm):
        mock_pm.get_status.return_value = self._status([self._white(0)])
        missing = PrintScheduler()._get_missing_force_color_slots(5, [self._override(1), self._override(2)])
        assert missing == ["PLA (#FFFFFF)"]

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_two_spools_satisfy_two_slots(self, mock_pm):
        mock_pm.get_status.return_value = self._status([self._white(0), self._white(1)])
        assert PrintScheduler()._get_missing_force_color_slots(5, [self._override(1), self._override(2)]) == []

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_a_slot_open_to_any_variant_leaves_the_named_variant_alone(self, mock_pm):
        """Slot 1 takes any white PLA, slot 2 only Basic. Taken in slot order,
        slot 1 would use up the Basic spool and slot 2 would read as missing."""
        mock_pm.get_status.return_value = self._status([self._white(0, "GFA00"), self._white(1, "GFA06")])
        overrides = [self._override(1), self._override(2, "GFA00")]
        assert PrintScheduler()._get_missing_force_color_slots(5, overrides) == []

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_a_named_variant_takes_its_own_spool_before_one_naming_none(self, mock_pm):
        """Slot 1 wants Basic, slot 2 Silk. The spool naming no variant is the
        only one Silk can use, so Basic must take the Basic spool."""
        mock_pm.get_status.return_value = self._status([self._white(0, ""), self._white(1, "GFA00")])
        overrides = [self._override(1, "GFA00"), self._override(2, "GFA06")]
        assert PrintScheduler()._get_missing_force_color_slots(5, overrides) == []

    def test_the_matcher_agrees(self):
        """Whatever the pre-check accepts, the matcher maps."""
        open_slot = {"slot_id": 1, "type": "PLA", "color": "#FFFFFF", "tray_info_idx": "", "force_color_match": True}
        basic = {"slot_id": 2, "type": "PLA", "color": "#FFFFFF", "tray_info_idx": "GFA00", "force_color_match": True}
        trays = [_tray(0, "FFFFFFFF", idx="GFA00"), _tray(1, "FFFFFFFF", idx="GFA06")]
        assert PrintScheduler()._match_filaments_to_slots([open_slot, basic], trays) == [1, 0]

    def test_unforced_slots_keep_their_order(self):
        """Only forced slots are reordered; plain jobs match as they always did."""
        plain = {"slot_id": 1, "type": "PLA", "color": "#123456", "tray_info_idx": ""}
        named = {"slot_id": 2, "type": "PLA", "color": "#654321", "tray_info_idx": "GFA00"}
        trays = [_tray(0, "000000FF", idx="GFA06"), _tray(1, "FFFFFFFF", idx="GFA06")]
        assert PrintScheduler()._match_filaments_to_slots([plain, named], trays) == [0, 1]


class TestASpoolWithoutAColour:
    """The pre-check reads a tray the way the matcher does: no colour is #808080."""

    @staticmethod
    def _status():
        return MagicMock(raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA"}]}]})

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_grey_is_found_on_a_tray_without_a_colour(self, mock_pm):
        mock_pm.get_status.return_value = self._status()
        override = [{"slot_id": 1, "type": "PLA", "color": "#808080", "force_color_match": True}]
        assert PrintScheduler()._get_missing_force_color_slots(5, override) == []

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_an_empty_colour_is_not_found_on_it(self, mock_pm):
        mock_pm.get_status.return_value = self._status()
        override = [{"slot_id": 1, "type": "PLA", "color": "", "force_color_match": True}]
        assert PrintScheduler()._get_missing_force_color_slots(5, override) != []
