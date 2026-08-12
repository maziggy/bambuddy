"""Regression tests for the cross-printer AMS mapping bug (#2799).

One model queued to three P2S at once produced three queue items carrying a
byte-identical ``ams_mapping``. That mapping was correct for the printer it was
computed against and wrong for the other two, whose AMS held the same spools in
different slots — so two of the three printed the lettering in ASA where the 3MF
strictly requires PETG. An explicit ``ams_mapping`` bypasses the firmware's own
type check, so nothing downstream caught it.

The reproduction below is the real incident: the 3MF needs light-blue PETG in
slot 1 and dark-blue PETG in slot 3; P2S-4 holds them in trays 2 and 1, while
P2S-5 holds them in trays 0 and 2 with ASA in tray 1. ``[2, -1, 1]`` is right on
the first and feeds ASA into slot 3 on the second.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler

# 3MF requirements: slot 1 light-blue PETG (body), slot 3 dark-blue PETG (text).
REQUIREMENTS = [
    {"slot_id": 1, "type": "PETG", "color": "#76D9F4", "tray_info_idx": "GFG99"},
    {"slot_id": 3, "type": "PETG", "color": "#2850E0", "tray_info_idx": "GFG99"},
]

# AMS layouts as reported over MQTT, same spools in a different slot order.
P2S_4_TRAYS = [
    {"global_tray_id": 0, "type": "ASA", "color": "161616FF"},
    {"global_tray_id": 1, "type": "PETG", "color": "2850E0FF"},
    {"global_tray_id": 2, "type": "PETG", "color": "76D9F4FF"},
    {"global_tray_id": 3, "type": "ASA", "color": "898989FF"},
]
P2S_5_TRAYS = [
    {"global_tray_id": 0, "type": "PETG", "color": "76D9F4FF"},
    {"global_tray_id": 1, "type": "ASA", "color": "161616FF"},
    {"global_tray_id": 2, "type": "PETG", "color": "2850E0FF"},
    {"global_tray_id": 3, "type": "ASA", "color": "898989FF"},
]

# The mapping the print dialog stamped onto all three items.
SHARED_MAPPING = [2, -1, 1]


def _item(**overrides):
    item = MagicMock()
    item.id = 276
    item.printer_id = 8
    item.ams_mapping = json.dumps(SHARED_MAPPING)
    item.skip_filament_check = False
    item.filament_overrides = None
    item.required_filament_types = None
    item.manual_start = False
    item.filament_short = False
    for key, value in overrides.items():
        setattr(item, key, value)
    return item


def _scheduler(trays, requirements=REQUIREMENTS):
    scheduler = PrintScheduler()
    scheduler._build_loaded_filaments = MagicMock(return_value=list(trays))
    scheduler._get_filament_requirements = AsyncMock(
        return_value=[dict(r) for r in requirements] if requirements else None
    )
    return scheduler


class TestStoredMappingConflict:
    """The stored mapping is re-checked against the printer about to run it."""

    @pytest.mark.asyncio
    async def test_mapping_fits_the_printer_it_was_computed_for(self):
        scheduler = _scheduler(P2S_4_TRAYS)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 9, _item(), SHARED_MAPPING)
        assert conflict is None

    @pytest.mark.asyncio
    async def test_same_mapping_on_a_differently_loaded_printer_conflicts(self):
        """The incident: slot 3 would have printed in ASA."""
        scheduler = _scheduler(P2S_5_TRAYS)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 8, _item(), SHARED_MAPPING)
        assert conflict is not None
        assert "slot 3" in conflict
        assert "ASA" in conflict

    @pytest.mark.asyncio
    async def test_tray_not_loaded_conflicts(self):
        scheduler = _scheduler(P2S_4_TRAYS[:2])  # trays 2 and 3 removed
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 9, _item(), SHARED_MAPPING)
        assert conflict is not None
        assert "does not have loaded" in conflict

    @pytest.mark.asyncio
    async def test_mapping_too_short_for_the_plate_conflicts(self):
        scheduler = _scheduler(P2S_4_TRAYS)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 9, _item(), [2])
        assert conflict is not None
        assert "slot 3" in conflict

    @pytest.mark.asyncio
    async def test_unreported_external_feed_is_not_a_conflict(self):
        """An external spool we have not heard about is absence of evidence."""
        scheduler = _scheduler(P2S_4_TRAYS)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 9, _item(), [254, -1, 255])
        assert conflict is None

    @pytest.mark.asyncio
    async def test_external_feed_is_type_checked_like_any_tray(self):
        """Two printers with different filament in the external feed is the same
        failure this check exists for, so >=254 is not a free pass."""
        trays = P2S_4_TRAYS + [{"global_tray_id": 254, "type": "ASA", "color": "161616FF"}]
        scheduler = _scheduler(trays)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 9, _item(), [254, -1, 1])
        assert conflict is not None
        assert "ASA" in conflict

    @pytest.mark.asyncio
    async def test_unresolved_required_slot_is_not_a_conflict(self):
        """-1 says the matcher had nothing, not that the mapping is foreign.
        Recomputing on it would discard the slots the user did resolve by hand;
        _block_on_unmatched_filament owns this case instead."""
        scheduler = _scheduler(P2S_4_TRAYS)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 9, _item(), [2, -1, -1])
        assert conflict is None

    @pytest.mark.asyncio
    async def test_foreign_tray_is_caught_without_reading_the_3mf(self):
        """The cheap pass runs on live status alone; the parse is not cached and
        this method runs for every pending item on every tick."""
        scheduler = _scheduler(P2S_4_TRAYS)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 9, _item(), [7, -1, 1])
        assert conflict is not None
        assert "does not have loaded" in conflict
        scheduler._get_filament_requirements.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_status_is_not_a_conflict(self):
        """Absence of evidence never triggers a recompute."""
        scheduler = _scheduler(P2S_5_TRAYS)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = None
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 8, _item(), SHARED_MAPPING)
        assert conflict is None

    @pytest.mark.asyncio
    async def test_no_trays_reported_yet_is_not_a_conflict(self):
        scheduler = _scheduler([])
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 8, _item(), SHARED_MAPPING)
        assert conflict is None

    @pytest.mark.asyncio
    async def test_unreadable_requirements_is_not_a_conflict(self):
        scheduler = _scheduler(P2S_5_TRAYS, requirements=None)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 8, _item(), SHARED_MAPPING)
        assert conflict is None


class TestEnsureAmsMappingRevalidates:
    """``_ensure_ams_mapping`` acts on the conflict verdict."""

    @pytest.mark.asyncio
    async def test_conflicting_mapping_is_recomputed(self):
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[0, -1, 2])
        item = _item()
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            await scheduler._ensure_ams_mapping(db, 8, item)

        assert json.loads(item.ams_mapping) == [0, -1, 2]
        scheduler._compute_ams_mapping_for_printer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fitting_mapping_is_left_untouched(self):
        scheduler = _scheduler(P2S_4_TRAYS)
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[0, -1, 2])
        item = _item(printer_id=9)
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            await scheduler._ensure_ams_mapping(db, 9, item)

        assert json.loads(item.ams_mapping) == SHARED_MAPPING
        scheduler._compute_ams_mapping_for_printer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_print_anyway_keeps_the_users_mapping(self):
        """skip_filament_check is the user overriding exactly this judgement."""
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[0, -1, 2])
        item = _item(skip_filament_check=True)
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            await scheduler._ensure_ams_mapping(db, 8, item)

        assert json.loads(item.ams_mapping) == SHARED_MAPPING
        scheduler._compute_ams_mapping_for_printer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_recompute_does_not_fall_back_to_the_bad_mapping(self):
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=None)
        scheduler._unmappable_without_ams_message = AsyncMock(return_value=None)
        item = _item()
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            await scheduler._ensure_ams_mapping(db, 8, item)

        assert item.ams_mapping is None


class TestBlockOnUnmatchedFilament:
    """A slot the plate prints with no tray on this printer holds the job."""

    @pytest.mark.asyncio
    async def test_unresolved_required_slot_holds(self):
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._get_job_name = AsyncMock(return_value="body1")
        scheduler._get_printer = AsyncMock(return_value=MagicMock(model="P2S"))
        item = _item(ams_mapping=json.dumps([0, -1, -1]))
        db = AsyncMock()

        blocked = await scheduler._block_on_unmatched_filament(db, item)

        assert blocked is True
        assert item.manual_start is True
        # The reason names the filament, not a machine token, because it renders
        # on the queue row.
        assert item.waiting_reason.startswith("Needs ")
        assert "PETG" in item.waiting_reason
        assert "#2850E0" in item.waiting_reason

    @pytest.mark.asyncio
    async def test_padding_for_a_slot_this_plate_skips_does_not_hold(self):
        """-1 at slot 2, which this plate never prints, is not a missing filament.
        This is why the hold intersects with the requirement list rather than
        reading the mapping array on its own."""
        scheduler = _scheduler(P2S_5_TRAYS)
        item = _item(ams_mapping=json.dumps([0, -1, 2]))

        blocked = await scheduler._block_on_unmatched_filament(AsyncMock(), item)

        assert blocked is False
        assert item.manual_start is False

    @pytest.mark.asyncio
    async def test_mapping_too_short_for_the_plate_holds(self):
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._get_job_name = AsyncMock(return_value="body1")
        scheduler._get_printer = AsyncMock(return_value=MagicMock(model="P2S"))
        item = _item(ams_mapping=json.dumps([0]))

        blocked = await scheduler._block_on_unmatched_filament(AsyncMock(), item)

        assert blocked is True

    @pytest.mark.asyncio
    async def test_print_anyway_dispatches(self):
        scheduler = _scheduler(P2S_5_TRAYS)
        item = _item(ams_mapping=json.dumps([0, -1, -1]), skip_filament_check=True)

        blocked = await scheduler._block_on_unmatched_filament(AsyncMock(), item)

        assert blocked is False

    @pytest.mark.asyncio
    async def test_cleared_mapping_is_left_to_the_2589_path(self):
        """A mapping cleared to None is the unresolvable path, which has its own
        handling — this gate must not also fail it."""
        scheduler = _scheduler(P2S_5_TRAYS)
        item = _item(ams_mapping=None)

        blocked = await scheduler._block_on_unmatched_filament(AsyncMock(), item)

        assert blocked is False

    @pytest.mark.asyncio
    async def test_unreadable_requirements_dispatches(self):
        scheduler = _scheduler(P2S_5_TRAYS, requirements=None)
        item = _item(ams_mapping=json.dumps([0, -1, -1]))

        blocked = await scheduler._block_on_unmatched_filament(AsyncMock(), item)

        assert blocked is False

    @pytest.mark.asyncio
    async def test_nozzle_bound_filament_on_the_wrong_side_holds(self):
        """The H2D case the type-only scan waved through: PETG is loaded, but on
        the other nozzle's AMS, so the nozzle-aware matcher left the slot at -1.
        Reading the mapping inherits that restriction for free."""
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._get_job_name = AsyncMock(return_value="body1")
        scheduler._get_printer = AsyncMock(return_value=MagicMock(model="H2D"))
        item = _item(ams_mapping=json.dumps([0, -1, -1]))

        blocked = await scheduler._block_on_unmatched_filament(AsyncMock(), item)

        assert blocked is True

    @pytest.mark.asyncio
    async def test_stale_hold_reason_is_cleared_once_the_spool_is_loaded(self):
        """The route that clears manual_start does not clear waiting_reason, and
        a held item never reaches this gate — so the pass that lets it through
        has to drop its own stale message."""
        scheduler = _scheduler(P2S_5_TRAYS)
        item = _item(ams_mapping=json.dumps([0, -1, 2]), waiting_reason="Needs PETG #2850E0")
        db = AsyncMock()

        blocked = await scheduler._block_on_unmatched_filament(db, item)

        assert blocked is False
        assert item.waiting_reason is None
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_reason_set_by_another_path_is_left_alone(self):
        scheduler = _scheduler(P2S_5_TRAYS)
        item = _item(ams_mapping=json.dumps([0, -1, 2]), waiting_reason="Waiting for matching printer")

        await scheduler._block_on_unmatched_filament(AsyncMock(), item)

        assert item.waiting_reason == "Waiting for matching printer"


class TestModelBasedVirtualPrinterMapping:
    """A model-based Virtual Printer stamps a mapping nothing can attribute.

    `virtual_printer/manager.py` writes the slicer's own AMS pick onto the queue
    item it creates, on the line below `printer_id=self.target_printer_id` —
    which is None for an "Any P2S" VP. So the tray IDs the slicer resolved are
    carried by an item the scheduler will hand to whichever printer of that
    model happens to be free, and the row itself records no printer they could
    be checked against. Same failure as the print dialog's, arriving from a
    different direction, and reachable whenever the per-VP `save_ams_mapping`
    opt-in is on without `queue_force_color_match`.

    The item has no `printer_id` until the model-based path assigns one, so
    these go through `_ensure_ams_mapping`'s `printer_id` argument rather than
    `item.printer_id` — which is how that path calls it.
    """

    @pytest.mark.asyncio
    async def test_slicer_mapping_is_rechecked_against_the_printer_chosen_for_it(self):
        scheduler = _scheduler(P2S_5_TRAYS)
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[0, -1, 2])
        # Fresh from the VP: no printer_id, mapping resolved by the slicer.
        item = _item(printer_id=None)
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            await scheduler._ensure_ams_mapping(db, 10, item)

        assert json.loads(item.ams_mapping) == [0, -1, 2]
        # Judged against the printer the scheduler picked, not the row's own
        # (absent) one.
        scheduler._compute_ams_mapping_for_printer.assert_awaited_once()
        assert scheduler._compute_ams_mapping_for_printer.await_args.args[1] == 10

    @pytest.mark.asyncio
    async def test_slicer_mapping_survives_on_a_printer_it_happens_to_fit(self):
        """Not every VP mapping is wrong — one that fits the chosen printer is
        kept, so the slicer's deliberate slot pick is not thrown away."""
        scheduler = _scheduler(P2S_4_TRAYS)
        scheduler._compute_ams_mapping_for_printer = AsyncMock(return_value=[0, -1, 2])
        item = _item(printer_id=None)
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            await scheduler._ensure_ams_mapping(db, 9, item)

        assert json.loads(item.ams_mapping) == SHARED_MAPPING
        scheduler._compute_ams_mapping_for_printer.assert_not_awaited()
