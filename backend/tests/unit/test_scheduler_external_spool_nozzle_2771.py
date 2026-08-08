"""Regression tests for external-spool nozzle routing on AMS-less printers (#2771).

A fleet of X2Ds with no AMS, printing from external spools, could not be sent a
job with "Any X2D": the print uploaded, then the firmware rejected it with
0700_8012 "Failed to get AMS mapping table" and the item failed after three
dispatch attempts. Sending the same file to a named printer worked, because that
path carries a mapping the *frontend* resolved and the scheduler's matcher never
runs.

Cause: ``_build_loaded_filaments`` derived dual-nozzle status from
``ams_extruder_map``, which is built from AMS info bits — a dual-nozzle printer
with zero AMS units reports an empty map. Every external spool then got
``extruder_id=None``, and the nozzle-aware hard filter in
``_match_filaments_to_slots`` rejected it because ``None`` equals neither 0 nor
1. Nothing matched, the mapping came back all -1 and was cleared to None, and the
print command went out as ``use_ams: true`` with no mapping table at all.

This is the backend half of #1257, which fixed the identical logic in
``useFilamentMapping.ts`` and left this copy behind; the first two tests below
mirror its frontend regression tests.

The second half covers the guard that keeps a genuinely unmappable job from
being uploaded at all, since without an AMS there is no "load another spool and
press Resume" recovery for the firmware error to lead to.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import (
    PrintScheduler,
    _unmatched_filament_message,
)

# Two external feeds, as an X2D/H2D reports them: 254 is Ext-L (deputy/left,
# extruder 1) and 255 is Ext-R (main/right, extruder 0).
DUAL_EXTERNAL = [
    {"id": "254", "tray_type": "PETG", "tray_color": "000000FF", "tray_info_idx": "GFG00"},
    {"id": "255", "tray_type": "PLA", "tray_color": "FFFFFFFF", "tray_info_idx": "GFA00"},
]

REAL_NOZZLES = [
    SimpleNamespace(nozzle_diameter="0.4"),
    SimpleNamespace(nozzle_diameter="0.4"),
]
# The state seeds `nozzles` with two empty NozzleInfo stubs even on single-nozzle
# printers, so the second entry's presence proves nothing — only a diameter does.
STUB_NOZZLES = [
    SimpleNamespace(nozzle_diameter="0.4"),
    SimpleNamespace(nozzle_diameter=""),
]


def _status(raw_data, nozzles=None):
    return SimpleNamespace(raw_data=raw_data, nozzles=nozzles)


@pytest.fixture
def scheduler():
    return PrintScheduler()


class TestExternalSpoolExtruderRouting:
    """``_build_loaded_filaments`` must route external spools without an AMS."""

    def test_dual_nozzle_without_ams_routes_both_external_feeds(self, scheduler):
        """The X2D case from the report: no AMS, so ams_extruder_map is empty."""
        loaded = scheduler._build_loaded_filaments(
            _status({"ams": [], "ams_extruder_map": {}, "vt_tray": DUAL_EXTERNAL}, REAL_NOZZLES)
        )

        by_tray = {f["global_tray_id"]: f for f in loaded}
        assert by_tray[254]["extruder_id"] == 1  # Ext-L -> left
        assert by_tray[255]["extruder_id"] == 0  # Ext-R -> right

    def test_single_nozzle_stub_does_not_fabricate_an_extruder(self, scheduler):
        """A P1S/A1/X1C must keep extruder_id=None, matching pre-fix behaviour.

        Sibling regression to the fix: `nozzles` always has two entries, so
        inferring dual-nozzle from its length would hand every single-nozzle
        printer's external spool a nozzle it does not have.
        """
        loaded = scheduler._build_loaded_filaments(
            _status(
                {"ams": [], "ams_extruder_map": {}, "vt_tray": [DUAL_EXTERNAL[0]]},
                STUB_NOZZLES,
            )
        )

        assert len(loaded) == 1
        assert loaded[0]["extruder_id"] is None

    def test_two_external_feeds_alone_imply_dual_nozzle(self, scheduler):
        """Fallback signal: only dual-nozzle hardware exposes two external feeds.

        Kept for firmware revisions that report the feeds but not the nozzle
        diameters — here `nozzles` is absent entirely.
        """
        loaded = scheduler._build_loaded_filaments(
            _status({"ams": [], "ams_extruder_map": {}, "vt_tray": DUAL_EXTERNAL})
        )

        assert {f["extruder_id"] for f in loaded} == {0, 1}

    def test_populated_ams_extruder_map_still_implies_dual_nozzle(self, scheduler):
        """The original signal keeps working when there IS an AMS."""
        loaded = scheduler._build_loaded_filaments(
            _status(
                {"ams": [], "ams_extruder_map": {"0": 1}, "vt_tray": [DUAL_EXTERNAL[0]]},
                STUB_NOZZLES,
            )
        )

        assert loaded[0]["extruder_id"] == 1

    def test_mapping_resolves_for_the_nozzle_the_spool_feeds(self, scheduler):
        """End to end: the matcher now finds the external spool, as it did for
        the working named-printer dispatch (which sent ams_mapping [254])."""
        loaded = scheduler._build_loaded_filaments(
            _status({"ams": [], "ams_extruder_map": {}, "vt_tray": DUAL_EXTERNAL}, REAL_NOZZLES)
        )
        req = {"slot_id": 1, "type": "PETG", "color": "#000000", "tray_info_idx": "GFG00"}

        assert scheduler._match_filaments_to_slots([{**req, "nozzle_id": 1}], loaded) == [254]
        # Nothing PETG on the right nozzle — still correctly unmatched.
        assert scheduler._match_filaments_to_slots([{**req, "nozzle_id": 0}], loaded) == [-1]


class TestUnmatchedFilamentMessage:
    """The message has to name the filament and, on dual-nozzle, the nozzle."""

    def test_names_type_colour_and_nozzle(self):
        message = _unmatched_filament_message(
            [{"slot_id": 1, "type": "PETG", "color": "#000000", "nozzle_id": 0}],
            [{"type": "PETG", "color": "#000000", "extruder_id": 1}],
        )

        assert "PETG #000000 (right nozzle)" in message
        assert "PETG #000000 (left nozzle)" in message

    def test_omits_nozzle_on_single_nozzle_printers(self):
        message = _unmatched_filament_message(
            [{"slot_id": 1, "type": "ABS", "color": "#FF0000"}],
            [{"type": "PLA", "color": "#000000"}],
        )

        assert "ABS #FF0000" in message
        assert "nozzle" not in message


class TestUnmappableWithoutAmsGuard:
    """``_ensure_ams_mapping`` reports only a positive, unrecoverable finding."""

    def _item(self, ams_mapping=None):
        item = MagicMock()
        item.id = 22
        item.printer_id = 4
        item.ams_mapping = ams_mapping
        item.filament_overrides = None
        return item

    async def _ensure(self, scheduler, computed, status):
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=computed)
        scheduler._get_filament_requirements = AsyncMock(
            return_value=[{"slot_id": 1, "type": "PETG", "color": "#000000", "nozzle_id": 0}]
        )
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = status
            return await scheduler._ensure_ams_mapping(AsyncMock(), 4, self._item())

    @pytest.mark.asyncio
    async def test_reports_when_nothing_matches_and_there_is_no_ams(self, scheduler):
        status = _status({"ams": [], "ams_extruder_map": {}, "vt_tray": DUAL_EXTERNAL}, REAL_NOZZLES)

        message = await self._ensure(scheduler, [-1], status)

        assert message is not None
        assert "no AMS" in message

    @pytest.mark.asyncio
    async def test_silent_when_an_ams_is_attached(self, scheduler):
        """With an AMS the user can load a spool and press Resume, so the
        firmware's own error is worth reaching — behaviour is unchanged."""
        status = _status(
            {
                "ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000"}]}],
                "ams_extruder_map": {},
                "vt_tray": DUAL_EXTERNAL,
            },
            REAL_NOZZLES,
        )

        assert await self._ensure(scheduler, [-1], status) is None

    @pytest.mark.asyncio
    async def test_silent_when_the_ams_field_has_not_arrived_yet(self, scheduler):
        """Absence of an AMS report is not a report of no AMS.

        `raw_data["ams"]` appears only once an AMS push has been handled, so a
        missing key means a reconnect or a cold start — where a fully loaded
        AMS is briefly invisible and everything would look unmappable.
        """
        status = _status({"ams_extruder_map": {}, "vt_tray": DUAL_EXTERNAL}, REAL_NOZZLES)

        assert await self._ensure(scheduler, [-1], status) is None

    @pytest.mark.asyncio
    async def test_silent_when_the_matcher_never_ran(self, scheduler):
        """A None mapping means no requirements parsed or nothing loaded — not
        evidence of a mismatch. Fail-safe: dispatch as before."""
        status = _status({"ams": [], "ams_extruder_map": {}, "vt_tray": DUAL_EXTERNAL}, REAL_NOZZLES)

        assert await self._ensure(scheduler, None, status) is None

    @pytest.mark.asyncio
    async def test_silent_when_the_mapping_resolves(self, scheduler):
        status = _status({"ams": [], "ams_extruder_map": {}, "vt_tray": DUAL_EXTERNAL}, REAL_NOZZLES)
        item = self._item()
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[254])

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = status
            assert await scheduler._ensure_ams_mapping(AsyncMock(), 4, item) is None

        assert json.loads(item.ams_mapping) == [254]

    @pytest.mark.asyncio
    async def test_silent_when_the_printer_status_is_gone(self, scheduler):
        assert await self._ensure(scheduler, [-1], None) is None


class TestFailUnmappableItem:
    """The guard fails the item instead of spending an upload on it."""

    @pytest.mark.asyncio
    async def test_marks_failed_with_the_message(self, scheduler):
        db = AsyncMock()
        item = MagicMock()
        item.id = 22
        item.created_by_id = 1

        with (
            patch("backend.app.services.print_scheduler.notification_service") as notify,
            patch("backend.app.services.print_scheduler.ws_manager"),
        ):
            notify.on_queue_job_failed = AsyncMock()
            scheduler._get_job_name = AsyncMock(return_value="Fidget")
            scheduler._get_printer = AsyncMock(return_value=SimpleNamespace(name="X2D-1"))

            await scheduler._fail_unmappable_item(db, item, 4, "needs PETG")

        assert item.status == "failed"
        assert item.error_message == "needs PETG"
        assert item.completed_at is not None
        db.commit.assert_awaited()
        notify.on_queue_job_failed.assert_awaited_once()
