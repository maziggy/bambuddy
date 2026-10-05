"""A mapping made for an external spool, sent to a printer whose AMS holds it (#3239).

Two A1 in one location, both with black PETG: A1-018 has no AMS and feeds it
from the external spool (``[254]``), A1-002 holds it in AMS tray 0 and has an
empty external spool. A failed job of A1-018 was queued again and moved to the
location, kept ``[254]``, and was picked by A1-002, which then stopped at once
with "External filament is missing". The #2799 check let it through because an
external tray the printer doesn't report as loaded counted as no evidence.

The printers' trays below are the reporter's, from the support bundle.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler

BLACK_PETG = [{"slot_id": 1, "type": "PETG", "color": "#000000", "tray_info_idx": "GFG99"}]

EMPTY_EXTERNAL = {"id": "254", "tray_type": "", "tray_color": "00000000", "remain": 0, "tray_info_idx": ""}
LOADED_EXTERNAL = {"id": "254", "tray_type": "PETG", "tray_color": "000000FF", "remain": 80, "tray_info_idx": "GFG99"}

A1_002_AMS = [
    {
        "id": "0",
        "tray": [
            {"id": "0", "tray_type": "PETG", "tray_color": "000000FF", "tray_info_idx": "GFG99"},
            {"id": "1", "tray_type": "PETG", "tray_color": "C52C18FF", "tray_info_idx": "GFG99"},
            {"id": "2", "tray_type": "PETG", "tray_color": "AC95D5FF", "tray_info_idx": "GFG99"},
            {"id": "3", "tray_type": "PETG", "tray_color": "0085D5FF", "tray_info_idx": "GFG99"},
        ],
    }
]


def _status(ams, vt_tray):
    return SimpleNamespace(
        raw_data={"ams": ams, "vt_tray": [vt_tray], "ams_extruder_map": {}},
        nozzles=[],
        fila_switch=None,
    )


def _item():
    item = MagicMock()
    item.id = 2453
    item.printer_id = None
    item.ams_mapping = json.dumps([254])
    item.skip_filament_check = False
    item.filament_overrides = None
    item.required_filament_types = None
    item.manual_start = False
    item.filament_short = False
    return item


def _scheduler():
    scheduler = PrintScheduler()
    scheduler._get_filament_requirements = AsyncMock(return_value=[dict(r) for r in BLACK_PETG])
    scheduler._get_bool_setting = AsyncMock(return_value=False)
    return scheduler


async def _ensure(status):
    scheduler = _scheduler()
    item = _item()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = status
        await scheduler._ensure_ams_mapping(AsyncMock(), 12, item)
    return json.loads(item.ams_mapping) if item.ams_mapping else None


@pytest.mark.asyncio
async def test_the_reporters_job_is_remapped_to_the_ams_tray():
    assert await _ensure(_status(A1_002_AMS, EMPTY_EXTERNAL)) == [0]


@pytest.mark.asyncio
async def test_the_printer_it_was_made_for_keeps_the_external_spool():
    """A1-018: no AMS, black PETG on the external spool."""
    assert await _ensure(_status([], LOADED_EXTERNAL)) == [254]


@pytest.mark.asyncio
async def test_an_external_spool_without_its_filament_set_keeps_printing():
    """No AMS, external spool loaded but never given a filament type: there is
    nothing else to feed from, so the job goes out as it always did."""
    assert await _ensure(_status([], EMPTY_EXTERNAL)) == [254]


@pytest.mark.asyncio
async def test_an_ams_without_the_filament_does_not_take_the_slot():
    """An empty external spool alone proves nothing when the AMS can't print the
    slot either; the mapping is left for the existing checks."""
    other_material = [
        {"id": "0", "tray": [{"id": "0", "tray_type": "PLA", "tray_color": "000000FF", "tray_info_idx": "GFL99"}]}
    ]
    scheduler = _scheduler()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _status(other_material, EMPTY_EXTERNAL)
        conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 12, _item(), [254])
    assert conflict is None


@pytest.mark.asyncio
async def test_another_colour_in_the_ams_does_not_take_the_slot():
    """A job for this very printer whose external spool is just empty for now:
    the printer asking for the spool beats printing it in white."""
    white_only = [
        {"id": "0", "tray": [{"id": "0", "tray_type": "PETG", "tray_color": "FFFFFFFF", "tray_info_idx": "GFG99"}]}
    ]
    scheduler = _scheduler()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _status(white_only, EMPTY_EXTERNAL)
        conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 12, _item(), [254])
    assert conflict is None


@pytest.mark.asyncio
async def test_the_conflict_names_the_empty_external_spool():
    scheduler = _scheduler()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _status(A1_002_AMS, EMPTY_EXTERNAL)
        conflict = await scheduler._stored_mapping_conflict(AsyncMock(), 12, _item(), [254])
    assert conflict is not None
    assert "external spool" in conflict
    assert "tray 0" in conflict


@pytest.mark.asyncio
async def test_print_anyway_still_keeps_the_users_mapping():
    scheduler = _scheduler()
    item = _item()
    item.skip_filament_check = True
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _status(A1_002_AMS, EMPTY_EXTERNAL)
        await scheduler._ensure_ams_mapping(AsyncMock(), 12, item)
    assert json.loads(item.ams_mapping) == [254]
