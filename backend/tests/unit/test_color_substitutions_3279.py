"""A job sent with a spool of a colour the file did not ask for is recorded (#3279).

When no spool of the file's colour is loaded, the matcher can still give a slot
a spool of the same material in another colour (``type_only``), and a slot
picked by hand can be any colour. Nothing said so: the reporter found out from
the print. Now the colours are compared when the job is sent, and the slots
that differ are kept on the queue item for the queue row to show.

Compared as the matcher compares: the same hex or a similar one is a match.
The file's colour is the one after overrides, so a colour swapped on purpose
is not reported.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.print_scheduler import PrintScheduler
from backend.tests.unit.test_scheduler_cancel_race import (
    _dispatch,
    queue_factory,  # noqa: F401 - fixture
)

GREY = {"slot_id": 1, "type": "PLA", "color": "#A4AAAC", "tray_info_idx": "GFA00"}

TRAYS = [
    {"global_tray_id": 0, "type": "PLA", "color": "#A4AAAC"},
    {"global_tray_id": 1, "type": "PLA", "color": "#000000"},
    {"global_tray_id": 2, "type": "PLA", "color": "#A0A8AA"},
    {"global_tray_id": 254, "type": "PLA", "color": "#FFFFFF"},
]


class TestFindColorSubstitutions:
    def find(self, required, mapping, loaded=TRAYS):
        return PrintScheduler()._find_color_substitutions(required, mapping, loaded)

    def test_a_spool_of_another_colour_is_reported(self):
        assert self.find([GREY], [1]) == [
            {"slot_id": 1, "type": "PLA", "wanted": "#A4AAAC", "got": "#000000", "tray": 1}
        ]

    def test_the_external_spool_is_compared_too(self):
        assert self.find([GREY], [254])[0]["got"] == "#FFFFFF"

    def test_the_same_colour_is_not(self):
        assert self.find([GREY], [0]) == []

    def test_a_similar_colour_is_not(self):
        """The matcher calls it a match, so the queue row does too."""
        assert self.find([GREY], [2]) == []

    def test_an_unmapped_slot_is_not(self):
        """Not a colour the job printed in; the hold covers it."""
        assert self.find([GREY], [-1]) == []
        assert self.find([{**GREY, "slot_id": 3}], [0]) == []

    def test_a_tray_the_printer_does_not_report_is_not(self):
        assert self.find([GREY], [7]) == []

    def test_a_boolean_is_not_read_as_tray_one(self):
        assert self.find([GREY], [True]) == []

    def test_a_spool_without_a_colour_is_not(self):
        """The loaded list reads it as #808080; that is no colour, not grey."""
        loaded = [{"global_tray_id": 1, "type": "PLA", "color": "#808080", "color_known": False}]
        assert self.find([GREY], [1], loaded) == []

    def test_the_loaded_list_says_whether_the_spool_has_a_colour(self):
        status = MagicMock(
            raw_data={
                "ams": [
                    {
                        "id": 0,
                        "tray": [
                            {"id": 0, "tray_type": "PLA"},
                            {"id": 1, "tray_type": "PLA", "tray_color": "000000FF"},
                        ],
                    }
                ],
                "vt_tray": [{"id": 254, "tray_type": "PLA"}],
            }
        )
        loaded = PrintScheduler()._build_loaded_filaments(status)
        assert [f["color_known"] for f in loaded] == [False, True, False]

    def test_a_slot_without_a_colour_is_not(self):
        assert self.find([{**GREY, "color": ""}], [1]) == []

    def test_only_the_slots_that_differ(self):
        second = {"slot_id": 2, "type": "PLA", "color": "#000000"}
        assert [s["slot_id"] for s in self.find([GREY, second], [1, 1])] == [1]


class TestColorSubstitutionsFor:
    def _scheduler(self, requirements=(GREY,)):
        scheduler = PrintScheduler()
        scheduler._build_loaded_filaments = MagicMock(return_value=[dict(t) for t in TRAYS])
        scheduler._get_filament_requirements = AsyncMock(
            return_value=[dict(r) for r in requirements] if requirements else None
        )
        return scheduler

    @staticmethod
    def _item(**fields):
        item = MagicMock()
        item.id = 5
        item.printer_id = 4
        item.ams_mapping = json.dumps([1])
        item.filament_overrides = None
        for key, value in fields.items():
            setattr(item, key, value)
        return item

    @pytest.mark.asyncio
    async def test_reports_the_substituted_slot(self):
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            found = await self._scheduler()._color_substitutions_for(AsyncMock(), self._item())
        assert json.loads(found) == [{"slot_id": 1, "type": "PLA", "wanted": "#A4AAAC", "got": "#000000", "tray": 1}]

    @pytest.mark.asyncio
    async def test_a_colour_swapped_on_purpose_is_not_reported(self):
        swapped = json.dumps([{"slot_id": 1, "type": "PLA", "color": "#000000"}])
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            found = await self._scheduler()._color_substitutions_for(
                AsyncMock(), self._item(filament_overrides=swapped)
            )
        assert found is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fields",
        [{"ams_mapping": None}, {"ams_mapping": "not json"}, {"ams_mapping": '{"0": 1}'}, {"printer_id": None}],
    )
    async def test_nothing_to_compare_records_nothing(self, fields):
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            found = await self._scheduler()._color_substitutions_for(AsyncMock(), self._item(**fields))
        assert found is None

    @pytest.mark.asyncio
    async def test_no_status_or_no_file_records_nothing(self):
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = None
            assert await self._scheduler()._color_substitutions_for(AsyncMock(), self._item()) is None
            pm.get_status.return_value = MagicMock()
            assert await self._scheduler(requirements=None)._color_substitutions_for(AsyncMock(), self._item()) is None

    @pytest.mark.asyncio
    async def test_a_failing_check_records_nothing(self):
        scheduler = self._scheduler()
        scheduler._get_filament_requirements = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            assert await scheduler._color_substitutions_for(AsyncMock(), self._item()) is None


SUBSTITUTED = json.dumps([{"slot_id": 1, "type": "PLA", "wanted": "#A4AAAC", "got": "#000000", "tray": 1}])


class TestRecordedWhenSent:
    @pytest.mark.asyncio
    async def test_stored_with_the_move_to_printing(self, queue_factory):
        ctx = await queue_factory()

        with patch.object(PrintScheduler, "_color_substitutions_for", AsyncMock(return_value=SUBSTITUTED)):
            await _dispatch(ctx)

        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.queue_item_id)
        assert item.status == "printing"
        assert item.color_substitutions == SUBSTITUTED

    @pytest.mark.asyncio
    async def test_a_new_send_replaces_the_last_answer(self, queue_factory):
        """A retried job is compared again; a stale note must not survive."""
        ctx = await queue_factory()
        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.queue_item_id)
            item.color_substitutions = SUBSTITUTED
            await db.commit()

        with patch.object(PrintScheduler, "_color_substitutions_for", AsyncMock(return_value=None)):
            await _dispatch(ctx)

        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.queue_item_id)
        assert item.status == "printing"
        assert item.color_substitutions is None


class TestDroppedWhenThePrintNeverStarts:
    """Written with the move to printing, which comes before the print command.
    A send that goes back to the queue or fails there printed in no colour."""

    async def _printing_with_a_note(self, ctx):
        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.queue_item_id)
            item.status = "printing"
            item.color_substitutions = SUBSTITUTED
            await db.commit()

    @pytest.mark.asyncio
    async def test_back_to_the_queue(self, queue_factory):
        ctx = await queue_factory()
        await self._printing_with_a_note(ctx)

        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.queue_item_id)
            await PrintScheduler._defer_until_printer_idle(db, item)

        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.queue_item_id)
        assert (item.status, item.color_substitutions) == ("pending", None)

    @pytest.mark.asyncio
    async def test_failed_before_it_started(self, queue_factory):
        ctx = await queue_factory()
        await self._printing_with_a_note(ctx)

        with patch("backend.app.services.print_batch.refresh_batches_after", AsyncMock()):
            async with ctx.session_maker() as db:
                item = await db.get(PrintQueueItem, ctx.queue_item_id)
                assert await PrintScheduler()._fail_dispatch(db, item, "start command failed")

        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.queue_item_id)
        assert (item.status, item.color_substitutions) == ("failed", None)
