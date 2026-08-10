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
    async def test_explicit_external_selection_is_left_alone(self):
        """>=254 is the user pointing at the spool holder, not an AMS tray."""
        scheduler = _scheduler(P2S_4_TRAYS)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 9, _item(), [254, -1, 255])
        assert conflict is None

    @pytest.mark.asyncio
    async def test_unresolved_required_slot_conflicts(self):
        scheduler = _scheduler(P2S_4_TRAYS)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 9, _item(), [2, -1, -1])
        assert conflict is not None
        assert "unresolved" in conflict

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


class TestBlockOnMissingFilamentType:
    """A filament the file needs and the printer lacks holds the job."""

    @pytest.mark.asyncio
    async def test_missing_type_promotes_to_manual_start(self):
        scheduler = PrintScheduler()
        scheduler._get_missing_filament_types = MagicMock(return_value=["PETG"])
        scheduler._get_job_name = AsyncMock(return_value="body1")
        scheduler._get_printer = AsyncMock(return_value=MagicMock(model="P2S"))
        item = _item(required_filament_types=json.dumps(["PETG", "ASA"]))
        db = AsyncMock()

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            blocked = await scheduler._block_on_missing_filament_type(db, item)

        assert blocked is True
        assert item.manual_start is True

    @pytest.mark.asyncio
    async def test_all_types_loaded_dispatches(self):
        scheduler = PrintScheduler()
        scheduler._get_missing_filament_types = MagicMock(return_value=[])
        item = _item(required_filament_types=json.dumps(["PETG"]))

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            blocked = await scheduler._block_on_missing_filament_type(AsyncMock(), item)

        assert blocked is False
        assert item.manual_start is False

    @pytest.mark.asyncio
    async def test_print_anyway_dispatches(self):
        scheduler = PrintScheduler()
        scheduler._get_missing_filament_types = MagicMock(return_value=["PETG"])
        item = _item(required_filament_types=json.dumps(["PETG"]), skip_filament_check=True)

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = MagicMock()
            blocked = await scheduler._block_on_missing_filament_type(AsyncMock(), item)

        assert blocked is False

    @pytest.mark.asyncio
    async def test_no_status_does_not_hold(self):
        """_get_missing_filament_types reports everything missing without status;
        holding on that would wedge a job on absence of evidence."""
        scheduler = PrintScheduler()
        scheduler._get_missing_filament_types = MagicMock(return_value=["PETG"])
        item = _item(required_filament_types=json.dumps(["PETG"]))

        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = None
            blocked = await scheduler._block_on_missing_filament_type(AsyncMock(), item)

        assert blocked is False

    @pytest.mark.asyncio
    async def test_item_without_recorded_types_dispatches(self):
        scheduler = PrintScheduler()
        item = _item(required_filament_types=None)
        blocked = await scheduler._block_on_missing_filament_type(AsyncMock(), item)
        assert blocked is False
