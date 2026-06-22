"""Tests for the AMS Filament Backup gate on prefer_lowest sort (#1766).

The reporter set ``prefer_lowest_filament=True`` but the printer kept picking
the first matching spool. Root cause: without the printer's AMS Filament
Backup enabled, switching to the second spool is impossible — so sorting
toward the lowest leaves the print at risk. The gate coerces prefer_lowest
to False whenever the printer reports backup OFF, with None (unknown / A1
family) preserving today's behaviour.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
def scheduler():
    return PrintScheduler()


def _patch_status(backup):
    """Patch ``printer_manager.get_status`` to return a stub PrinterState whose
    ``ams_filament_backup`` is the requested tri-state value."""
    return patch(
        "backend.app.services.print_scheduler.printer_manager.get_status",
        return_value=SimpleNamespace(ams_filament_backup=backup, raw_data={}),
    )


async def _run_with_backup(scheduler, backup_state, prefer_lowest_setting):
    """Drive ``_compute_ams_mapping_for_printer`` past the gate point and
    return whatever ``prefer_lowest`` value gets handed to the matcher."""
    db = MagicMock()
    item = SimpleNamespace(filament_overrides=None)
    filament_reqs = [{"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": ""}]
    loaded = [
        {
            "ams_id": 0,
            "tray_id": 0,
            "global_tray_id": 0,
            "is_external": False,
            "type": "PLA",
            "color": "#000000",
            "tray_info_idx": "",
        },
    ]

    captured: dict = {}

    def _capture_match(reqs, loaded_, prefer, overrides):
        captured["prefer_lowest"] = prefer
        return [0]

    with (
        _patch_status(backup_state),
        patch.object(scheduler, "_get_filament_requirements", new=AsyncMock(return_value=filament_reqs)),
        patch.object(scheduler, "_build_loaded_filaments", return_value=loaded),
        patch.object(scheduler, "_get_bool_setting", new=AsyncMock(return_value=prefer_lowest_setting)),
        patch.object(scheduler, "_build_inventory_remain_overrides", new=AsyncMock(return_value={})),
        patch.object(scheduler, "_match_filaments_to_slots", side_effect=_capture_match),
    ):
        await scheduler._compute_ams_mapping_for_printer(db, printer_id=1, item=item)

    return captured.get("prefer_lowest")


class TestPreferLowestBackupGate:
    @pytest.mark.asyncio
    async def test_backup_off_disables_prefer_lowest(self, scheduler):
        # User setting ON but printer reports backup OFF — gate must coerce.
        # This is the #1766 fix: previously the sort applied and picked the
        # near-empty spool, leaving the print to fail mid-job.
        out = await _run_with_backup(scheduler, backup_state=False, prefer_lowest_setting=True)
        assert out is False

    @pytest.mark.asyncio
    async def test_backup_on_preserves_prefer_lowest(self, scheduler):
        # Backup ON — sort applies as the user intended.
        out = await _run_with_backup(scheduler, backup_state=True, prefer_lowest_setting=True)
        assert out is True

    @pytest.mark.asyncio
    async def test_backup_unknown_preserves_prefer_lowest(self, scheduler):
        # None = unknown / unsupported (A1 family). Must NOT be treated as OFF
        # — that would regress A1 users who currently get the sort applied.
        out = await _run_with_backup(scheduler, backup_state=None, prefer_lowest_setting=True)
        assert out is True

    @pytest.mark.asyncio
    async def test_user_setting_off_short_circuits(self, scheduler):
        # User setting OFF — backup state is irrelevant; sort never applies.
        out = await _run_with_backup(scheduler, backup_state=True, prefer_lowest_setting=False)
        assert out is False
