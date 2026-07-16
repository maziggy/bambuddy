"""Tests for the nozzle-diameter mismatch guard (#1899).

A file sliced for one nozzle size dispatched to a printer with a different
nozzle installed is rejected by the firmware with a cryptic HMS ("Failed to get
AMS mapping table" 0700_8012). The scheduler catches this before upload and
fails the queue item with an actionable message instead.

These cover the two pure helpers that make the decision. The guard is fail-safe
by construction: it only blocks on a POSITIVE mismatch, never on missing data.
"""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.services.print_scheduler as scheduler_module
from backend.app.core.database import Base
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import (
    PrintScheduler,
    _installed_nozzle_diameters,
    _nozzle_mismatch_message,
)


def _state(*diameters: str):
    """PrinterState-shaped namespace with the given nozzle diameter strings."""
    return SimpleNamespace(nozzles=[SimpleNamespace(nozzle_diameter=d) for d in diameters])


# ---------------------------------------------------------------------------
# _installed_nozzle_diameters
# ---------------------------------------------------------------------------


def test_installed_parses_single_nozzle():
    assert _installed_nozzle_diameters(_state("0.6")) == [0.6]


def test_installed_parses_dual_nozzle():
    assert _installed_nozzle_diameters(_state("0.4", "0.6")) == [0.4, 0.6]


def test_installed_skips_empty_default_stub():
    # Single-nozzle printers still emit a 2-entry array; the second is an
    # empty-string default until MQTT fills it in.
    assert _installed_nozzle_diameters(_state("0.4", "")) == [0.4]


def test_installed_skips_unparseable_and_zero():
    assert _installed_nozzle_diameters(_state("", "abc", "0", "0.4")) == [0.4]


def test_installed_handles_no_status_or_no_nozzles():
    assert _installed_nozzle_diameters(None) == []
    assert _installed_nozzle_diameters(SimpleNamespace()) == []
    assert _installed_nozzle_diameters(SimpleNamespace(nozzles=[])) == []


# ---------------------------------------------------------------------------
# _nozzle_mismatch_message
# ---------------------------------------------------------------------------


def test_mismatch_blocks_single_nozzle():
    msg = _nozzle_mismatch_message(0.6, [0.4])
    assert msg is not None
    assert "0.6mm" in msg
    assert "0.4mm" in msg


def test_match_single_nozzle_passes():
    assert _nozzle_mismatch_message(0.4, [0.4]) is None


def test_match_within_float_tolerance_passes():
    # 0.4 slice vs a 0.40000001 reported diameter must not trip.
    assert _nozzle_mismatch_message(0.4, [0.40000001]) is None


def test_dual_nozzle_match_on_either_passes():
    # 0.6 slice on a printer with a 0.4 and a 0.6 hotend is fine.
    assert _nozzle_mismatch_message(0.6, [0.4, 0.6]) is None


def test_dual_nozzle_mismatch_on_both_blocks():
    msg = _nozzle_mismatch_message(0.8, [0.4, 0.6])
    assert msg is not None
    assert "0.4mm / 0.6mm" in msg


def test_no_sliced_diameter_is_failsafe_none():
    # Slice didn't declare a nozzle diameter → never block.
    assert _nozzle_mismatch_message(None, [0.4]) is None
    assert _nozzle_mismatch_message(0.0, [0.4]) is None


def test_no_installed_nozzles_is_failsafe_none():
    # Printer hasn't reported nozzles → unknown, never block.
    assert _nozzle_mismatch_message(0.6, []) is None


def test_adjacent_sizes_are_distinguished():
    # 0.2 gap between adjacent sizes stays well outside the 0.05 tolerance.
    assert _nozzle_mismatch_message(0.4, [0.6]) is not None
    assert _nozzle_mismatch_message(0.6, [0.8]) is not None


# ---------------------------------------------------------------------------
# End-to-end: the guard fires inside _start_print BEFORE upload
# ---------------------------------------------------------------------------


@pytest.fixture
async def archive_case(tmp_path):
    """Build an archive-based queue item on a real in-memory DB + on-disk 3MF."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def make_case(*, sliced_nozzle: float | None):
        base_dir = tmp_path / "case"
        base_dir.mkdir(exist_ok=True)
        archive_rel = Path("archives") / "job.3mf"
        archive_abs = base_dir / archive_rel
        archive_abs.parent.mkdir(parents=True, exist_ok=True)
        archive_abs.write_bytes(b"sliced 3mf")

        async with session_maker() as db:
            printer = Printer(
                name="H2S",
                serial_number="SN-H2S",
                ip_address="127.0.0.1",
                access_code="ac",
                model="H2S",
            )
            db.add(printer)
            await db.flush()
            archive = PrintArchive(
                printer_id=printer.id,
                filename="job.3mf",
                file_path=str(archive_rel),
                file_size=archive_abs.stat().st_size,
                nozzle_diameter=sliced_nozzle,
                status="completed",
            )
            db.add(archive)
            await db.flush()
            item = PrintQueueItem(
                printer_id=printer.id,
                archive_id=archive.id,
                status="pending",
                bed_levelling=True,
                flow_cali=False,
                vibration_cali=True,
                layer_inspect=False,
                timelapse=False,
                use_ams=True,
                nozzle_offset_cali=True,
            )
            db.add(item)
            await db.commit()
            return SimpleNamespace(
                session_maker=session_maker,
                base_dir=base_dir,
                archive_abs=archive_abs,
                printer_id=printer.id,
                queue_item_id=item.id,
                start_print=MagicMock(return_value=True),
                upload=AsyncMock(return_value=True),
            )

    try:
        yield make_case
    finally:
        await engine.dispose()


async def _run_start_print(ctx, *, installed_nozzles):
    scheduler = PrintScheduler()
    status = SimpleNamespace(nozzles=[SimpleNamespace(nozzle_diameter=d) for d in installed_nozzles])
    # The mismatch case returns before the upload path; the match case drives it
    # to start_print, so mirror the post-guard dependency patches the
    # cleanup-library harness uses (get_ftp_retry_settings et al. open their own
    # DB session, not our in-memory one, so they must be stubbed).
    patches = [
        patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=status)),
        patch("backend.app.services.print_scheduler.printer_manager.start_print", ctx.start_print),
        patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
        patch("backend.app.services.print_scheduler.upload_file_async", ctx.upload),
        patch("backend.app.services.print_scheduler.delete_file_async", AsyncMock(return_value=True)),
        patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
        patch("backend.app.services.print_scheduler.spawn_background_task", MagicMock()),
        patch(
            "backend.app.services.print_scheduler.get_ftp_retry_settings", AsyncMock(return_value=(False, 0, 0, 1.0))
        ),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_started", AsyncMock()),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_failed", AsyncMock()),
        patch("backend.app.services.mqtt_relay.mqtt_relay.on_queue_job_started", AsyncMock()),
        patch("backend.app.services.print_scheduler.ws_manager.send_queue_item_failed", AsyncMock()),
        patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
        patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.queue_item_id)
            await scheduler._start_print(db, item)


@pytest.mark.asyncio
async def test_start_print_blocks_on_nozzle_mismatch_before_upload(archive_case):
    """0.6 slice on a 0.4-only printer: item fails with an actionable message,
    and neither upload nor start_print is reached."""
    ctx = await archive_case(sliced_nozzle=0.6)
    await _run_start_print(ctx, installed_nozzles=["0.4"])

    async with ctx.session_maker() as db:
        item = await db.get(PrintQueueItem, ctx.queue_item_id)
    assert item.status == "failed"
    assert "0.6mm" in item.error_message and "0.4mm" in item.error_message
    ctx.upload.assert_not_called()
    ctx.start_print.assert_not_called()


@pytest.mark.asyncio
async def test_start_print_proceeds_when_nozzle_matches(archive_case):
    """0.6 slice on a 0.6 printer: the guard is a no-op and dispatch proceeds
    (item leaves 'pending', start_print is reached)."""
    ctx = await archive_case(sliced_nozzle=0.6)
    await _run_start_print(ctx, installed_nozzles=["0.6"])

    async with ctx.session_maker() as db:
        item = await db.get(PrintQueueItem, ctx.queue_item_id)
    assert item.status != "failed"
    ctx.start_print.assert_called_once()
