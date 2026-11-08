"""A specific-printer job's "Force color match" at dispatch (#3445).

The reporter sliced a part in white, queued it for one P1S, picked the black
tray for the slot and ticked "Force color match". The dialog dropped the tick:
the job was saved with the tray only. By the next morning that tray no longer
fit, the scheduler matched the trays again against the sliced white, found
none, and with "prefer lowest" on took the green spool on type alone.

The dialog now saves the ticked slot with the filament of the tray picked for
it. These pin what the scheduler does with such an entry when it has to match
again: the black spool, or nothing at all, never another colour.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler

REQUIREMENTS = [{"slot_id": 1, "type": "PLA", "color": "#CBC6B8", "tray_info_idx": "GFA01"}]

# What the dialog saves for the ticked slot mapped to the black tray.
BLACK_FORCED = [{"slot_id": 1, "type": "PLA", "color": "#000000", "tray_info_idx": "GFA01", "force_color_match": True}]

# The reporter's printer 2 at dispatch: tray 1 empty now, green lower than black.
TRAYS = [
    {"global_tray_id": 2, "type": "PLA", "color": "000000FF", "tray_info_idx": "GFA01", "remain": 95},
    {"global_tray_id": 3, "type": "PLA", "color": "68724DFF", "tray_info_idx": "GFA01", "remain": 73},
]


def _item(overrides):
    item = MagicMock()
    item.id = 74
    item.printer_id = 2
    item.ams_mapping = json.dumps([1])
    item.skip_filament_check = False
    item.filament_overrides = json.dumps(overrides) if overrides is not None else None
    item.required_filament_types = None
    item.manual_start = False
    item.filament_short = False
    item.waiting_reason = None
    return item


def _scheduler(trays):
    scheduler = PrintScheduler()
    scheduler._build_loaded_filaments = MagicMock(return_value=[dict(t) for t in trays])
    scheduler._get_filament_requirements = AsyncMock(return_value=[dict(r) for r in REQUIREMENTS])
    scheduler._get_bool_setting = AsyncMock(return_value=True)  # prefer lowest
    scheduler._build_inventory_remain_overrides = AsyncMock(return_value=None)
    scheduler._get_job_name = AsyncMock(return_value="part")
    scheduler._get_printer = AsyncMock(return_value=MagicMock(model="P1S"))
    return scheduler


async def _dispatch_mapping(scheduler, item):
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = MagicMock(raw_data={"ams": [{"id": 0}]}, ams_filament_backup=None)
        await scheduler._ensure_ams_mapping(AsyncMock(), 2, item)
        blocked = await scheduler._block_on_unmatched_filament(AsyncMock(), item)
    return (json.loads(item.ams_mapping) if item.ams_mapping else None), blocked


@pytest.mark.asyncio
async def test_without_the_saved_tick_the_lowest_spool_of_the_type_is_taken():
    """The report as it happened: the green spool."""
    mapping, _ = await _dispatch_mapping(_scheduler(TRAYS), _item(None))
    assert mapping == [3]


@pytest.mark.asyncio
async def test_the_saved_tick_takes_the_black_spool():
    mapping, blocked = await _dispatch_mapping(_scheduler(TRAYS), _item(BLACK_FORCED))
    assert mapping == [2]
    assert blocked is False


@pytest.mark.asyncio
async def test_with_no_black_loaded_the_job_waits():
    mapping, blocked = await _dispatch_mapping(_scheduler(TRAYS[1:]), _item(BLACK_FORCED))
    assert mapping != [3]
    assert blocked is True
