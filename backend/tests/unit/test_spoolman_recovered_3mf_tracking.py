"""A recovered 3MF reaches Spoolman's usage tracking (late-3MF item).

A print that started without its 3MF gets a fallback archive, and Spoolman's
print-start tracking stores no per-filament usage for it: completion charges
the AMS remain% change instead, in 10 g steps on a 1 kg spool, nothing for a
slot without a remain%, and nothing at all when no slot had one. When the 3MF
turned up later, the recovery filled the archive in but left Spoolman's
tracking as it was; the built-in inventory reads the archive at completion and
was not affected. The recovered 3MF's usage is now added to it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.active_print_spoolman import ActivePrintSpoolman
from backend.app.models.settings import Settings
from backend.tests.unit.test_fallback_archive_recovery_2957 import DISPATCH_FILENAME, _seed, _write_3mf

pytestmark = pytest.mark.asyncio

USED = [{"slot_id": 1, "used_g": 42.5, "type": "PLA", "color": "#00AE42"}]
REMAIN_AT_START = {"0:0": {"remain": 80, "tray_uuid": "A" * 32}}


async def _with(maker, *, spoolman: bool = True, row: dict | None = None, printer_id: int, archive_id: int) -> None:
    async with maker() as db:
        if spoolman:
            db.add(Settings(key="spoolman_enabled", value="true"))
        if row is not None:
            db.add(ActivePrintSpoolman(printer_id=printer_id, archive_id=archive_id, ams_trays={}, **row))
        await db.commit()


async def _recover(maker, tmp_path, printer_id: int, archive_id: int, *, price: bool = True) -> None:
    from backend.app import main as main_module

    source = _write_3mf(tmp_path / "temp" / DISPATCH_FILENAME)
    printer_manager = MagicMock()
    printer_manager.get_status.return_value = SimpleNamespace(
        raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 71, "tray_type": "PLA"}]}]}, tray_now=0
    )
    with (
        patch.object(main_module, "async_session", maker),
        patch.object(main_module, "printer_manager", printer_manager),
    ):
        await main_module._recover_fallback_archive_locked(archive_id, source, printer_id, price=price)


async def _row(maker, archive_id: int) -> ActivePrintSpoolman | None:
    async with maker() as db:
        return (
            await db.execute(select(ActivePrintSpoolman).where(ActivePrintSpoolman.archive_id == archive_id))
        ).scalar_one_or_none()


@pytest.fixture
async def seeded(test_engine, tmp_path, monkeypatch):
    from backend.app import main as main_module
    from backend.app.core.config import settings as app_config

    # Archived under this test's own directory: in the shared archive/, a test
    # on another worker recovering the same archive id in the same second
    # writes the same 3MF path and this one reads it half-written.
    monkeypatch.setattr(app_config, "base_dir", tmp_path)
    monkeypatch.setattr(app_config, "archive_dir", tmp_path / "archive")
    maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
    # Empty, not whatever a test before on this worker left: the recovery reads
    # the printed plate from it, and a plate the 3MF has no usage for tracks
    # nothing.
    with (
        patch.dict(main_module._active_prints, {(printer_id, DISPATCH_FILENAME): archive_id}, clear=True),
        patch.dict(main_module._print_plate_ids, {}, clear=True),
    ):
        yield maker, printer_id, archive_id


async def test_the_recovered_usage_is_added_and_the_start_snapshot_kept(seeded, tmp_path):
    maker, printer_id, archive_id = seeded
    await _with(
        maker,
        printer_id=printer_id,
        archive_id=archive_id,
        row={"tray_remain_start": REMAIN_AT_START, "tray_now_at_start": 0, "slot_to_tray": [0]},
    )

    await _recover(maker, tmp_path, printer_id, archive_id)

    row = await _row(maker, archive_id)
    assert row.filament_usage == USED
    # Taken at print start; the recovery is mid-print, so it must not replace them.
    assert (row.tray_remain_start, row.tray_now_at_start, row.slot_to_tray, row.ams_trays) == (
        REMAIN_AT_START,
        0,
        [0],
        {},
    )


async def test_so_does_the_recovery_at_the_print_s_end(seeded, tmp_path):
    """It runs just before Spoolman is charged, so the charge reads it."""
    maker, printer_id, archive_id = seeded
    await _with(maker, printer_id=printer_id, archive_id=archive_id, row={"tray_remain_start": REMAIN_AT_START})

    await _recover(maker, tmp_path, printer_id, archive_id, price=False)

    assert (await _row(maker, archive_id)).filament_usage == USED


async def test_usage_already_tracked_is_left_alone(seeded, tmp_path):
    maker, printer_id, archive_id = seeded
    own = [{"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": "#FFFFFF"}]
    await _with(maker, printer_id=printer_id, archive_id=archive_id, row={"filament_usage": own})

    await _recover(maker, tmp_path, printer_id, archive_id)

    assert (await _row(maker, archive_id)).filament_usage == own


async def test_a_print_tracked_with_nothing_at_its_start_is_tracked_now(seeded, tmp_path):
    """No slot had a remain% at start, so nothing was stored and the print would
    be charged nothing. The 3MF is all there is; no start snapshot is invented."""
    maker, printer_id, archive_id = seeded
    await _with(maker, printer_id=printer_id, archive_id=archive_id)

    await _recover(maker, tmp_path, printer_id, archive_id)

    row = await _row(maker, archive_id)
    assert row.filament_usage == USED
    assert row.tray_remain_start is None
    assert row.tray_now_at_start is None


async def test_the_recovery_at_the_print_s_end_tracks_it_too(seeded, tmp_path):
    """It runs inside the close-out (marked as such), before the charge, so the
    charge reads it."""
    from backend.app.services import usage_tracker

    maker, printer_id, archive_id = seeded
    await _with(maker, printer_id=printer_id, archive_id=archive_id)
    usage_tracker.mark_completing(printer_id)
    try:
        await _recover(maker, tmp_path, printer_id, archive_id, price=False)
    finally:
        usage_tracker.done_completing(printer_id)

    assert (await _row(maker, archive_id)).filament_usage == USED


async def test_no_other_recovery_starts_tracking_while_the_print_is_closed(seeded, tmp_path):
    """The cover endpoint's download can land while the print is being closed:
    tracking made then could follow the charge that already deleted it, and be
    left behind, making the printer look mid-print to later spool archiving."""
    from backend.app.services import usage_tracker

    maker, printer_id, archive_id = seeded
    await _with(maker, printer_id=printer_id, archive_id=archive_id)
    usage_tracker.mark_completing(printer_id)
    try:
        await _recover(maker, tmp_path, printer_id, archive_id)
    finally:
        usage_tracker.done_completing(printer_id)

    assert await _row(maker, archive_id) is None


async def test_nothing_without_spoolman(seeded, tmp_path):
    maker, printer_id, archive_id = seeded
    await _with(maker, spoolman=False, printer_id=printer_id, archive_id=archive_id)

    await _recover(maker, tmp_path, printer_id, archive_id)

    assert await _row(maker, archive_id) is None
