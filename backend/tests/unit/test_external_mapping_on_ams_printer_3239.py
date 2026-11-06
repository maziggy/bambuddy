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


# A job made for A1-003, where black PETG sits in AMS tray 3, moved to the
# location and picked by A1-002, whose tray 3 holds blue PETG. Tray 3 exists
# and holds PETG, so the stored [3] fits by type and would print in blue.
A1_002_BLUE_IN_TRAY_3 = [
    {
        "id": "0",
        "tray": [
            {"id": "0", "tray_type": "PETG", "tray_color": "000000FF", "tray_info_idx": "GFG99"},
            {"id": "3", "tray_type": "PETG", "tray_color": "0085D5FF", "tray_info_idx": "GFG99"},
        ],
    }
]


@pytest.fixture
async def session_maker():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import backend.app.models  # noqa: F401 - populate Base.metadata
    from backend.app.core.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _location_job(session_maker, ams_mapping):
    from backend.app.models.library import LibraryFile
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.models.printer import Printer

    async with session_maker() as db:
        db.add(
            Printer(
                id=2,
                name="A1-002",
                serial_number="A1002",
                ip_address="10.0.0.2",
                access_code="x",
                model="A1",
                location="Farm",
                is_active=True,
            )
        )
        lib = LibraryFile(
            filename="job.gcode.3mf",
            file_path="/library/job.gcode.3mf",
            file_size=10,
            file_type="gcode.3mf",
            file_metadata={"sliced_for_model": "A1"},
        )
        db.add(lib)
        await db.flush()
        item = PrintQueueItem(
            status="pending",
            position=1,
            target_model="A1",
            target_location="Farm",
            library_file_id=lib.id,
            ams_mapping=ams_mapping,
        )
        db.add(item)
        await db.commit()
        return item.id


async def _mapping_after_a_pass(session_maker, item_id):
    from backend.app.models.print_queue import PrintQueueItem

    scheduler = _scheduler()
    scheduler._get_job_name = AsyncMock(return_value="job")
    status = _status(A1_002_BLUE_IN_TRAY_3, EMPTY_EXTERNAL)
    with (
        patch("backend.app.services.print_scheduler.async_session", session_maker),
        patch("backend.app.core.database.async_session", session_maker),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=status)),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_assigned", AsyncMock()),
        patch.object(scheduler, "_find_idle_printer_for_model", AsyncMock(return_value=(2, None))),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
        patch.object(scheduler, "_launch_uploads", MagicMock()),
    ):
        await scheduler.check_queue()
    async with session_maker() as db:
        item = await db.get(PrintQueueItem, item_id)
        return item.printer_id, json.loads(item.ams_mapping) if item.ams_mapping else None


@pytest.mark.asyncio
async def test_a_job_moved_to_a_location_is_mapped_for_the_printer_it_gets(session_maker):
    item_id = await _location_job(session_maker, json.dumps([3]))

    assert await _mapping_after_a_pass(session_maker, item_id) == (2, [0])


@pytest.mark.asyncio
async def test_a_location_job_without_a_mapping_is_matched_the_same(session_maker):
    item_id = await _location_job(session_maker, None)

    assert await _mapping_after_a_pass(session_maker, item_id) == (2, [0])


async def _ensure_unmapped(status, model="A1", required=None):
    """A job with no stored mapping, as a location job now always arrives."""
    scheduler = _scheduler()
    if required is not None:
        scheduler._get_filament_requirements = AsyncMock(return_value=required)
    scheduler._get_printer = AsyncMock(return_value=SimpleNamespace(model=model))
    item = _item()
    item.ams_mapping = None
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = status
        message = await scheduler._ensure_ams_mapping(AsyncMock(), 12, item)
    return (json.loads(item.ams_mapping) if item.ams_mapping else None), message


@pytest.mark.asyncio
async def test_a_printer_without_ams_prints_from_its_unset_external_spool():
    """A1-018 picks the job from the location: no AMS, and its external spool
    holds the black PETG without a filament set. Without a mapping the print
    would go out with the AMS on and be rejected with 0700_8012."""
    assert await _ensure_unmapped(_status([], EMPTY_EXTERNAL)) == ([254], None)


@pytest.mark.asyncio
async def test_every_printed_filament_goes_to_the_external_spool():
    required = [
        {"slot_id": 1, "type": "PETG", "color": "#000000"},
        {"slot_id": 3, "type": "PETG", "color": "#FFFFFF"},
    ]
    assert await _ensure_unmapped(_status([], EMPTY_EXTERNAL), required=required) == ([254, -1, 254], None)


@pytest.mark.asyncio
async def test_a_set_external_spool_is_matched_as_before():
    assert await _ensure_unmapped(_status([], LOADED_EXTERNAL)) == ([254], None)


@pytest.mark.asyncio
async def test_an_external_spool_set_to_another_material_still_fails_the_job():
    """The matcher answered: the only spool holds PLA. That stays a failure
    with a message (#2771), not a print in the wrong material."""
    pla = {**LOADED_EXTERNAL, "tray_type": "PLA", "tray_info_idx": "GFL99"}
    mapping, message = await _ensure_unmapped(_status([], pla))
    assert mapping is None
    assert message


@pytest.mark.asyncio
async def test_a_printer_with_an_ams_is_not_sent_to_the_external_spool():
    empty_ams = [{"id": "0", "tray": [{"id": "0", "tray_type": ""}]}]
    assert await _ensure_unmapped(_status(empty_ams, EMPTY_EXTERNAL)) == (None, None)


@pytest.mark.asyncio
async def test_a_printer_that_has_not_reported_its_ams_is_left_alone():
    status = SimpleNamespace(raw_data={"vt_tray": [EMPTY_EXTERNAL]}, nozzles=[], fila_switch=None)
    assert await _ensure_unmapped(status) == (None, None)


@pytest.mark.asyncio
async def test_a_dual_nozzle_printer_is_left_alone():
    """Its two external feeds steer the nozzles; which one is not ours to pick."""
    status = _status([], EMPTY_EXTERNAL)
    status.raw_data["vt_tray"] = [EMPTY_EXTERNAL, {**EMPTY_EXTERNAL, "id": "255"}]
    assert await _ensure_unmapped(status, model="H2D") == (None, None)


@pytest.mark.asyncio
async def test_a_job_that_wants_its_colours_matched_strictly_is_left_alone():
    """Force colour match: a spool without a filament set has no colour to check."""
    scheduler = _scheduler()
    scheduler._get_printer = AsyncMock(return_value=SimpleNamespace(model="A1"))
    item = _item()
    item.ams_mapping = None
    item.filament_overrides = json.dumps(
        [{"slot_id": 1, "type": "PETG", "color": "#000000", "force_color_match": True}]
    )
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _status([], EMPTY_EXTERNAL)
        assert await scheduler._external_spool_only_mapping(AsyncMock(), 12, item) is None
