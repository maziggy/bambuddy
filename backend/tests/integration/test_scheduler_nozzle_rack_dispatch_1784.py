"""What the dispatcher does with a rack-position pick (#1784).

The resolution itself is covered in
``backend/tests/unit/test_nozzle_rack_positions_1784.py``. This covers the glue
around it, where the two failure modes deliberately differ:

- an **explicit** pick that no longer fits the rack stops the print, because the
  operator named a hotend and printing from a different one is how a plate gets
  levelled on one nozzle and drawn with another, millimetres above the bed;
- an **assignment** that cannot be made falls through to the pre-existing #2800
  path, which is strictly not worse than the behaviour before any of this.

And a non-rack printer must be untouched by all of it.
"""

from __future__ import annotations

import json
import zipfile
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
from backend.app.models.settings import Settings  # noqa: F401 - registers the table
from backend.app.services.print_scheduler import PrintScheduler
from backend.tests._fixtures.background_tasks import discarding_spawn_patch

pytestmark = pytest.mark.integration

# Three filaments in groups 2/0/1, groups 1 and 2 both on the rack carriage --
# the maintainer's own plate, the one that printed in mid-air.
_FILAMENTS = (
    '<filament id="1" group_id="2" color="#DE4343" nozzle_diameter="0.40" volume_type="High Flow"/>'
    '<filament id="2" group_id="0" color="#F4EE2A" nozzle_diameter="0.40" volume_type="High Flow"/>'
    '<filament id="3" group_id="1" color="#0078BF" nozzle_diameter="0.40" volume_type="High Flow"/>'
)
_NOZZLES = '<nozzle id="0" extruder_id="1"/><nozzle id="1" extruder_id="2"/><nozzle id="2" extruder_id="2"/>'


def _write_3mf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps(
                {
                    "physical_extruder_map": ["1", "0"],
                    "extruder_max_nozzle_count": ["1", "6"],
                    "extruder_nozzle_stats": ["High Flow#1", "High Flow#6"],
                }
            ),
        )
        zf.writestr(
            "Metadata/slice_info.config",
            f'<config><plate><metadata key="index" value="1"/>{_FILAMENTS}{_NOZZLES}</plate></config>',
        )


def _rack(present=(1, 2, 3, 4, 5, 6)):
    """Live rack telemetry, plus the always-reported fixed carriage."""
    return [{"id": 15 + p, "diameter": "0.4", "type": "HH01", "filament_color": ""} for p in present] + [
        {"id": 1, "diameter": "0.4", "type": "HH01", "filament_color": ""}
    ]


@pytest.fixture
async def rack_case(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    base_dir = tmp_path / "rack-dispatch"
    archive_rel = Path("archives") / "benchy.gcode.3mf"
    _write_3mf(base_dir / archive_rel)

    async def _build(model: str, choice: dict | None):
        async with session_maker() as db:
            printer = Printer(
                name="H2C-1",
                serial_number="RACK-SERIAL",
                ip_address="127.0.0.1",
                access_code="access-code",
                model=model,
            )
            db.add(printer)
            await db.flush()
            archive = PrintArchive(
                printer_id=printer.id,
                filename="benchy.gcode.3mf",
                file_path=str(archive_rel),
                file_size=(base_dir / archive_rel).stat().st_size,
                status="completed",
            )
            db.add(archive)
            await db.flush()
            item = PrintQueueItem(
                printer_id=printer.id,
                archive_id=archive.id,
                plate_id=1,
                status="pending",
                nozzle_rack_choice=json.dumps(choice) if choice else None,
            )
            db.add(item)
            await db.commit()
            return SimpleNamespace(item_id=item.id, printer_id=printer.id)

    try:
        yield SimpleNamespace(session_maker=session_maker, base_dir=base_dir, build=_build)
    finally:
        await engine.dispose()


async def _dispatch(ctx, ids, rack_slots):
    """Run one dispatch, returning the mocked ``start_print`` and the delete."""
    scheduler = PrintScheduler()
    start_print = MagicMock(return_value=True)
    delete_file = AsyncMock(return_value=True)
    status = SimpleNamespace(state="IDLE", nozzle_rack=rack_slots)

    with ExitStack() as stack:
        for patcher in (
            patch.object(scheduler_module, "async_session", ctx.session_maker),
            patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=status)),
            patch("backend.app.services.print_scheduler.printer_manager.start_print", start_print),
            patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
            patch("backend.app.services.print_scheduler.delete_file_async", delete_file),
            patch("backend.app.services.print_scheduler.upload_file_async", AsyncMock(return_value=True)),
            # Reads settings through its own session on the real app database,
            # which the in-memory engine here does not have.
            patch(
                "backend.app.services.print_scheduler.get_ftp_retry_settings",
                AsyncMock(return_value=(False, 3, 2.0, 30.0)),
            ),
            patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
            discarding_spawn_patch(),
            patch("backend.app.services.notification_service.notification_service.on_queue_job_started", AsyncMock()),
            patch("backend.app.services.notification_service.notification_service.on_queue_job_failed", AsyncMock()),
            patch("backend.app.services.mqtt_relay.mqtt_relay.on_queue_job_started", AsyncMock()),
            patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
            patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
            patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
        ):
            stack.enter_context(patcher)
        await scheduler._dispatch_one(ids.item_id)

    async with ctx.session_maker() as db:
        item = await db.get(PrintQueueItem, ids.item_id)
    return start_print, delete_file, item


def _sent_mapping(start_print):
    assert start_print.call_count == 1, "the print command was never sent"
    return json.loads(start_print.call_args.kwargs["nozzle_mapping"])


class TestAPickThatStillFits:
    async def test_the_chosen_positions_reach_the_printer(self, rack_case):
        """Picking R1 for group 2 and R2 for group 1 is BambuStudio's own
        dispatch of this plate on 2026-08-14: nozzle_mapping [16, 1, 17].
        """
        ids = await rack_case.build("H2C", {"2": 1, "1": 2})
        start_print, _, item = await _dispatch(rack_case, ids, _rack())

        assert _sent_mapping(start_print)[:3] == [16, 1, 17]
        assert item.status == "printing"

    async def test_a_different_pick_of_the_same_plate_sends_a_different_mapping(self, rack_case):
        """The 2026-08-13 dispatch of the identical file: [16, 1, 18]."""
        ids = await rack_case.build("H2C", {"2": 1, "1": 3})
        start_print, _, _ = await _dispatch(rack_case, ids, _rack())

        assert _sent_mapping(start_print)[:3] == [16, 1, 18]


class TestNoPickAtAll:
    async def test_positions_are_assigned_rather_than_left_to_the_firmware(self, rack_case):
        """The plate that used to dispatch with no mapping now gets one."""
        ids = await rack_case.build("H2C", None)
        start_print, _, item = await _dispatch(rack_case, ids, _rack())

        assert _sent_mapping(start_print)[:3] == [17, 1, 16]
        assert item.status == "printing"

    async def test_an_unassignable_plate_falls_back_instead_of_failing(self, rack_case):
        """Nothing was promised, so nothing is broken by letting the firmware
        pick -- exactly what happened before this feature existed.
        """
        ids = await rack_case.build("H2C", None)
        start_print, _, item = await _dispatch(rack_case, ids, _rack(present=()))

        assert start_print.call_count == 1
        assert start_print.call_args.kwargs["nozzle_mapping"] is None
        assert item.status == "printing"


class TestAPickThatNoLongerFits:
    """Someone re-loaded the rack between queueing and dispatch."""

    async def test_the_print_is_refused_rather_than_sent_to_another_hotend(self, rack_case):
        ids = await rack_case.build("H2C", {"2": 1, "1": 3})
        start_print, _, item = await _dispatch(rack_case, ids, _rack(present=(1, 2)))

        start_print.assert_not_called()
        assert item.status == "failed"

    async def test_the_error_names_the_position_and_says_how_to_fix_it(self, rack_case):
        ids = await rack_case.build("H2C", {"2": 1, "1": 3})
        _, _, item = await _dispatch(rack_case, ids, _rack(present=(1, 2)))

        assert "rack position 3" in item.error_message
        assert "Edit the item" in item.error_message

    async def test_the_uploaded_file_is_removed_from_the_sd_card(self, rack_case):
        """It is already uploaded by this point, and a 3MF left there is a
        phantom print waiting to be started from the touchscreen.
        """
        ids = await rack_case.build("H2C", {"2": 1, "1": 3})
        _, delete_file, _ = await _dispatch(rack_case, ids, _rack(present=(1, 2)))

        delete_file.assert_awaited()


class TestOtherModels:
    async def test_a_non_rack_printer_is_left_entirely_alone(self, rack_case):
        """No rack means no resolution, no refusal, and no mapping invented."""
        ids = await rack_case.build("X1C", None)
        start_print, _, item = await _dispatch(rack_case, ids, [])

        assert start_print.call_count == 1
        assert start_print.call_args.kwargs["nozzle_mapping"] is None
        assert item.status == "printing"

    async def test_a_stale_pick_on_a_non_rack_printer_does_not_stop_the_print(self, rack_case):
        """The column can survive a reassignment to another model; it must not
        then block a printer the pick never applied to.
        """
        ids = await rack_case.build("X1C", {"2": 1, "1": 3})
        start_print, _, item = await _dispatch(rack_case, ids, [])

        assert start_print.call_count == 1
        assert item.status == "printing"
