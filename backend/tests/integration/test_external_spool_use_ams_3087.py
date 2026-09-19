"""A plate printed entirely from the external spool must dispatch use_ams=False (#3087).

The reporter's P1S sat at "Heatbed preheating" for ten and a half minutes and
then paused with 07FF_8012, "Failed to get AMS mapping table". The plate was one
filament, mapped by hand to the external spool, out of a seven-filament
MakerWorld project -- so the mapping was ``[-1, -1, -1, -1, -1, -1, 254]`` and
the command went out as ``use_ams: true`` with a flat mapping of nothing but
-1 (254 is deliberately not sent raw: the firmware reads it as AMS tray 0).

The decision belongs here rather than in the MQTT command builder. Down there a
-1 is either padding for a filament this plate does not print -- BambuStudio's
own convention, and what the other six entries are -- or a slot that never
resolved, which must never be redirected to the spool holder (#2589). The two
are the same byte. Only the plate's own filament list tells them apart, and
``extract_filament_requirements`` already drops anything with ``used_g <= 0``,
so it names exactly the slots that are printed.
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

# The reporter's plate: filament 7 of a seven-filament project, and it is the
# only one this plate consumes. slice_info.config lists a plate's filaments by
# their project-wide id, which is why the mapping is seven long.
_PLATE_4_ONE_FILAMENT = '<filament id="7" used_g="12.4" type="PLA" color="#F98C36"/>'


def _write_3mf(path: Path, plate_index: int = 4, filaments: str = _PLATE_4_ONE_FILAMENT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            f'<config><plate><metadata key="index" value="{plate_index}"/>{filaments}</plate></config>',
        )


def _write_3mf_without_slice_info(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")


@pytest.fixture
async def dispatch_case(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    base_dir = tmp_path / "external-spool"

    async def _build(
        mapping, *, use_ams=True, plate_id=4, filaments=_PLATE_4_ONE_FILAMENT, slice_info=True, model="P1S"
    ):
        archive_rel = Path("archives") / f"plate-{plate_id}-{abs(hash(str(mapping))) % 10**6}.gcode.3mf"
        if slice_info:
            _write_3mf(base_dir / archive_rel, plate_index=plate_id, filaments=filaments)
        else:
            _write_3mf_without_slice_info(base_dir / archive_rel)

        async with session_maker() as db:
            printer = Printer(
                name="P1S",
                serial_number=f"01P{abs(hash(str(mapping))) % 10**9}",
                ip_address="127.0.0.1",
                access_code="access-code",
                model=model,
            )
            db.add(printer)
            await db.flush()
            archive = PrintArchive(
                printer_id=printer.id,
                filename=archive_rel.name,
                file_path=str(archive_rel),
                file_size=(base_dir / archive_rel).stat().st_size,
                status="completed",
            )
            db.add(archive)
            await db.flush()
            item = PrintQueueItem(
                printer_id=printer.id,
                archive_id=archive.id,
                plate_id=plate_id,
                status="pending",
                use_ams=use_ams,
                ams_mapping=json.dumps(mapping) if mapping is not None else None,
            )
            db.add(item)
            await db.commit()
            return SimpleNamespace(item_id=item.id, printer_id=printer.id)

    try:
        yield SimpleNamespace(session_maker=session_maker, base_dir=base_dir, build=_build)
    finally:
        await engine.dispose()


async def _dispatch(ctx, ids, status=None):
    scheduler = PrintScheduler()
    start_print = MagicMock(return_value=True)
    status = status or SimpleNamespace(state="IDLE", nozzle_rack=None, raw_data={}, nozzles=[])

    with ExitStack() as stack:
        for patcher in (
            patch.object(scheduler_module, "async_session", ctx.session_maker),
            patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=status)),
            patch("backend.app.services.print_scheduler.printer_manager.start_print", start_print),
            patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
            patch("backend.app.services.print_scheduler.delete_file_async", AsyncMock(return_value=True)),
            patch("backend.app.services.print_scheduler.upload_file_async", AsyncMock(return_value=True)),
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

    assert start_print.call_count == 1, "the print command was never sent"
    return start_print.call_args


class TestThePlateThatOnlyPrintsFromTheSpoolHolder:
    async def test_the_reporters_mapping_dispatches_without_the_ams(self, dispatch_case):
        """[-1]*6 + [254] on a plate whose only printed filament is #7."""
        ids = await dispatch_case.build([-1, -1, -1, -1, -1, -1, 254])
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is False
        # The mapping itself still goes out untouched — the builder is what
        # turns 254 into -1 plus ams_mapping2, and none of that changes.
        assert call.kwargs["ams_mapping"] == [-1, -1, -1, -1, -1, -1, 254]

    async def test_the_main_nozzle_sentinel_counts_too(self, dispatch_case):
        ids = await dispatch_case.build([-1, -1, -1, -1, -1, -1, 255])
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is False

    async def test_an_unpadded_single_filament_plate_is_unaffected(self, dispatch_case):
        """[254] already worked: the MQTT command builder downgrades an
        all-external mapping by itself. The scheduler now reaches the same
        answer one layer earlier, so the two agree rather than one undoing the
        other — this pins that they do."""
        ids = await dispatch_case.build([254], filaments='<filament id="1" used_g="9.0" type="PLA"/>', plate_id=1)
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is False


class TestWhatMustNotChange:
    async def test_a_consumed_slot_that_never_resolved_still_goes_out_with_the_ams(self, dispatch_case):
        """The #2589 contract, and the reason this lives in the scheduler.

        Filaments 1 and 7 are both printed; 7 is on the spool holder and 1
        resolved to nothing. Redirecting the plate to the external spool would
        print filament 1 in the wrong material without saying so. use_ams stays
        true and the firmware rejects the print, exactly as before.
        """
        ids = await dispatch_case.build(
            [-1, -1, -1, -1, -1, -1, 254],
            filaments='<filament id="1" used_g="8.0" type="PETG"/>' + _PLATE_4_ONE_FILAMENT,
        )
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is True

    async def test_a_plate_mixing_an_ams_tray_with_the_spool_holder_keeps_the_ams(self, dispatch_case):
        ids = await dispatch_case.build(
            [5, -1, -1, -1, -1, -1, 254],
            filaments='<filament id="1" used_g="8.0" type="PETG"/>' + _PLATE_4_ONE_FILAMENT,
        )
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is True

    async def test_a_plate_printed_from_ams_trays_is_untouched(self, dispatch_case):
        ids = await dispatch_case.build([-1, -1, -1, -1, -1, -1, 5])
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is True

    async def test_use_ams_false_is_never_promoted_here(self, dispatch_case):
        """Promotion is the builder's job (#2595) and stays there."""
        ids = await dispatch_case.build([-1, -1, -1, -1, -1, -1, 5], use_ams=False)
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is False

    async def test_a_3mf_with_no_filament_list_falls_back_to_the_stored_flag(self, dispatch_case):
        """No evidence, no decision — the same convention as #2771."""
        ids = await dispatch_case.build([-1, -1, -1, -1, -1, -1, 254], slice_info=False)
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is True

    async def test_a_plate_the_file_does_not_describe_falls_back(self, dispatch_case):
        """The item says plate 4; the file only describes plate 1."""
        ids = await dispatch_case.build([-1, -1, -1, -1, -1, -1, 254], plate_id=4)
        # Rewrite the archive's 3MF so its only plate is index 1.
        async with dispatch_case.session_maker() as db:
            archive = (await db.get(PrintQueueItem, ids.item_id)).archive_id
            path = dispatch_case.base_dir / (await db.get(PrintArchive, archive)).file_path
        _write_3mf(path, plate_index=1)

        call = await _dispatch(dispatch_case, ids)
        assert call.kwargs["use_ams"] is True

    async def test_an_item_with_no_mapping_at_all_is_untouched(self, dispatch_case):
        ids = await dispatch_case.build(None)
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is True


class TestDualNozzleIsNotOursToRewrite:
    """On a two-extruder printer use_ams is which nozzle to feed, not whether to
    use the AMS — H2D Pro firmware reads it as an extruder index. The MQTT
    command builder skips its own reconcile for exactly that reason, and this
    must skip it too, or a perfectly normal dual external-spool print gets its
    routing rewritten."""

    async def test_a_dual_nozzle_model_keeps_its_flag(self, dispatch_case):
        ids = await dispatch_case.build([-1, -1, -1, -1, -1, -1, 254], model="H2D")
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is True

    async def test_both_external_feeds_on_a_dual_nozzle_are_left_alone(self, dispatch_case):
        """254 is the deputy feed and 255 the main one — an ordinary H2D print
        with a spool on each side, and the one this would have broken."""
        ids = await dispatch_case.build(
            [254, -1, -1, -1, -1, -1, 255],
            filaments='<filament id="1" used_g="8.0" type="PLA"/>' + _PLATE_4_ONE_FILAMENT,
            model="H2D",
        )
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is True

    async def test_live_telemetry_can_veto_a_single_nozzle_model_name(self, dispatch_case):
        """A model string we do not recognise as dual is not the last word: two
        external feeds is something only a two-extruder printer reports."""
        ids = await dispatch_case.build([-1, -1, -1, -1, -1, -1, 254], model="Something New")
        status = SimpleNamespace(
            state="IDLE",
            nozzle_rack=None,
            nozzles=[],
            raw_data={"vt_tray": [{"id": "254"}, {"id": "255"}]},
        )
        call = await _dispatch(dispatch_case, ids, status=status)

        assert call.kwargs["use_ams"] is True

    async def test_a_second_nozzle_reporting_a_diameter_vetoes_it_too(self, dispatch_case):
        ids = await dispatch_case.build([-1, -1, -1, -1, -1, -1, 254], model="Something New")
        status = SimpleNamespace(
            state="IDLE",
            nozzle_rack=None,
            nozzles=[SimpleNamespace(nozzle_diameter="0.4"), SimpleNamespace(nozzle_diameter="0.4")],
            raw_data={},
        )
        call = await _dispatch(dispatch_case, ids, status=status)

        assert call.kwargs["use_ams"] is True

    async def test_h2s_is_single_nozzle_and_still_gets_the_fix(self, dispatch_case):
        """H2S shares the H2 serial prefix and firmware quirks but has one
        extruder — the #1386 distinction, which must survive here."""
        ids = await dispatch_case.build([-1, -1, -1, -1, -1, -1, 254], model="H2S")
        call = await _dispatch(dispatch_case, ids)

        assert call.kwargs["use_ams"] is False
