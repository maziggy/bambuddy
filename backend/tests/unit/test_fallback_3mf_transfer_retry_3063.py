"""A transfer that ran out of time is a temporary give-up too (#3063).

The reporter's P1S was sent a 19 MB 3MF, the card had it, and FTPS served it --
just not inside the 30s budget plus its 30s grace, four times over, while the
printer was also running MQTT, the camera and the job upload at print start.
Bambuddy wrote an empty fallback archive at 03:14:23. The same file then
downloaded successfully at 03:15:11, 03:16:09 and 03:16:36, and every one of
those copies was thrown away, because the only code that would have attached one
had already given up.

#2957 built the machinery to fill a fallback archive in after the fact, but
armed it for exactly one give-up: the FTPS cool-off. Everything else was treated
as settled, which is right for the three storage verdicts -- a job on internal
eMMC never appears at any FTPS path -- and wrong here, where the file is on the
card and the only thing that failed was the transfer.

The discrimination these tests pin is the one the sweep already has and never
used: a file that is genuinely not there answers 550, which surfaces as
FileNotOnPrinterError and is caught by name. A timeout returns falsy instead --
``with_ftp_retry`` hands back None once its budget is spent -- so "we never got a
straight answer" and "the printer says no such file" are distinguishable without
guessing.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.main import (
    _active_prints,
    _expected_print_creators,
    _expected_print_registered_at,
    _expected_prints,
    _print_ams_mappings,
    _timelapse_baselines,
)
from backend.app.services.print_storage import REASON_FTP_TRANSFER_FAILED, REASON_FTPS_COOLOFF

pytestmark = pytest.mark.unit

DISPATCH = "/data/Metadata/plate_1.gcode"
SUBTASK = "Fan_Shroud"


@pytest.fixture(autouse=True)
def _clear_dicts():
    dicts = (
        _expected_prints,
        _expected_print_registered_at,
        _expected_print_creators,
        _print_ams_mappings,
        _active_prints,
        _timelapse_baselines,
    )
    for d in dicts:
        d.clear()
    yield
    for d in dicts:
        d.clear()


def _printer():
    printer = MagicMock()
    printer.id = 1
    printer.auto_archive = True
    printer.external_camera_enabled = False
    printer.external_camera_url = None
    # Every unset MagicMock attribute is truthy, and leaving this one implicit
    # runs the plate-detection camera grab against a printer that is not there.
    printer.plate_detection_enabled = False
    printer.name = "P1S"
    printer.model = "P1S"
    printer.ip_address = "172.25.12.149"
    printer.access_code = "12345678"
    return printer


async def _run_print_start(download, peek_plate=None):
    """Drive on_print_start's fallback path for a print on external storage.

    Returns ``(added_rows, schedule_mock)``. The card is present and the
    dispatch says ``ftp://``, so the storage verdict is reachable and the sweep
    runs -- what differs between tests is only how ``download`` fails.
    """
    printer = _printer()

    def execute_router(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from printers" in sql or "from printer " in sql:
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=printer),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[printer]))),
            )
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    added: list = []
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_router)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock(side_effect=added.append)

    schedule = MagicMock()
    state = MagicMock(
        current_project_url=f"ftp://{SUBTASK}.gcode.3mf",
        sdcard=True,
        sdcard_reported=True,
    )

    with (
        patch("backend.app.main.async_session") as session_maker,
        patch("backend.app.main.notification_service") as notif,
        patch("backend.app.main.smart_plug_manager") as plug,
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main.download_file_async", new=download),
        patch("backend.app.main.download_file_try_paths_async", new=AsyncMock(return_value=None)),
        patch("backend.app.main.get_cached_3mf", return_value=None),
        patch("backend.app.main.cache_3mf_download"),
        patch("backend.app.main.peek_plate_index_in_3mf", return_value=peek_plate),
        # Imported inside the function, so patching it anywhere else lets the
        # directory walk open real sockets and the test hangs on connect.
        patch("backend.app.services.bambu_ftp.list_files_async", new=AsyncMock(return_value=[])),
        patch("backend.app.main.ftps_handshake_blocked", return_value=False),
        # Retry off, so `download` is called directly and its failure mode is
        # the one under test rather than with_ftp_retry's summary of it.
        patch("backend.app.main.get_ftp_retry_settings", new=AsyncMock(return_value=(False, 3, 2.0, 30))),
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
        patch("backend.app.main._maybe_start_layer_timelapse"),
        patch("backend.app.main._capture_timelapse_baseline_at_start", new_callable=AsyncMock),
        # Real, it would spawn a task that outlives the test by a minute.
        patch("backend.app.main._schedule_fallback_3mf_retry", new=schedule),
    ):
        session_maker.return_value = session
        notif.on_print_start = AsyncMock()
        plug.on_print_start = AsyncMock()
        ws.send_print_start = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        # Awaited between creating the fallback row and scheduling its retry: a
        # plain MagicMock here raises, the handler swallows it, and every
        # assertion about the retry passes vacuously.
        ws.send_archive_created = AsyncMock()
        relay.on_print_start = AsyncMock()
        pm.get_status = MagicMock(return_value=state)
        pm.get_printer = MagicMock(return_value=MagicMock(serial_number="TEST3063"))

        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": DISPATCH, "subtask_name": SUBTASK})

    return added, schedule


def _fallback(added):
    for row in added:
        extra = getattr(row, "extra_data", None)
        if isinstance(extra, dict) and extra.get("no_3mf_available"):
            return row
    return None


class TestATimedOutTransferIsWorthComingBackFor:
    @pytest.mark.asyncio
    async def test_the_archive_records_the_transfer_as_the_cause(self):
        """Not `None`, which is the slug for "the slicer left nothing on the
        card" and sends this reporter to a setting that was already on."""
        added, _schedule = await _run_print_start(AsyncMock(return_value=False))

        assert _fallback(added).extra_data["no_3mf_reason"] == REASON_FTP_TRANSFER_FAILED

    @pytest.mark.asyncio
    async def test_a_retry_is_scheduled_with_the_names_the_sweep_just_tried(self):
        added, schedule = await _run_print_start(AsyncMock(return_value=False))

        schedule.assert_called_once()
        kwargs = schedule.call_args.kwargs
        assert kwargs["reason"] == REASON_FTP_TRANSFER_FAILED
        assert f"{SUBTASK}.gcode.3mf" in kwargs["filenames"]
        assert _fallback(added) is not None

    @pytest.mark.asyncio
    async def test_a_connection_error_counts_as_a_failed_transfer_too(self):
        """A refused or dropped connection is not the printer saying the file
        is absent, and it does not last any longer than a timeout does."""
        added, schedule = await _run_print_start(AsyncMock(side_effect=OSError("connection reset")))

        assert _fallback(added).extra_data["no_3mf_reason"] == REASON_FTP_TRANSFER_FAILED
        schedule.assert_called_once()


class TestAFileThatIsNotThereIsStillSettled:
    @pytest.mark.asyncio
    async def test_a_550_from_every_path_schedules_nothing(self):
        """The regression guard on the whole change. 550 is the printer
        answering the question, and no amount of waiting changes the answer --
        retrying it is the sweep #2780 removed for costing an install 1813
        failed connections in a day."""
        from backend.app.services.bambu_ftp import FileNotOnPrinterError

        added, schedule = await _run_print_start(AsyncMock(side_effect=FileNotOnPrinterError("550")))

        assert _fallback(added).extra_data["no_3mf_reason"] is None
        schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_download_that_produced_the_wrong_plate_schedules_nothing(self):
        """A 3MF arrived, so the transport is not what failed here -- it was the
        wrong plate, and #2957 discards it rather than archive another plate's
        filament and cost against this print.

        The names the sweep would retry with are the same stale ones that
        fetched the contradicted file, and `_recover_fallback_archive` checks
        that a candidate is a readable 3MF but not which plate it holds. So a
        retry here would put back exactly what was just thrown away.
        """
        # First path times out, the second serves a file -- for plate 2, while
        # the dispatch says plate 1. Without the reset, that first timeout would
        # be enough to arm a retry.
        download = AsyncMock(side_effect=[False, True, True, True, True, True])

        added, schedule = await _run_print_start(download, peek_plate=2)

        assert _fallback(added) is not None
        schedule.assert_not_called()


class TestTheRetryActuallyFillsTheArchiveIn:
    """The ladder is only half of it -- the pass it schedules has to land."""

    @pytest.mark.asyncio
    async def test_the_reporters_sequence_end_to_end(self, test_engine, tmp_path, monkeypatch):
        """Give up on the transfer, then let the same file turn up a minute
        later exactly as it did for the reporter, and the empty row is filled
        in rather than left for good.

        Driven through the real ``_schedule_fallback_3mf_retry`` rather than a
        mock of it, because the thing #3063 reports is not that nothing was
        scheduled -- it is that nothing ever attached the file.
        """
        import asyncio
        import zipfile

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from backend.app import main as main_module
        from backend.app.models.archive import PrintArchive
        from backend.app.models.printer import Printer
        from backend.app.services import bambu_ftp

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            printer = Printer(
                name="P1S",
                serial_number="01P00A3B1200579",
                ip_address="172.25.12.149",
                access_code="12345678",
                model="P1S",
            )
            db.add(printer)
            await db.commit()
            await db.refresh(printer)
            archive = PrintArchive(
                printer_id=printer.id,
                filename=f"{SUBTASK}.gcode.3mf",
                file_path="",
                file_size=0,
                print_name=SUBTASK,
                status="printing",
                extra_data={
                    "no_3mf_available": True,
                    "no_3mf_reason": REASON_FTP_TRANSFER_FAILED,
                    "_print_data": {"filename": f"{SUBTASK}.gcode.3mf"},
                },
            )
            db.add(archive)
            await db.commit()
            await db.refresh(archive)
            printer_id, archive_id = printer.id, archive.id

        source = tmp_path / "temp" / f"{SUBTASK}.gcode.3mf"
        source.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "Metadata/slice_info.config",
                "<?xml version='1.0' encoding='UTF-8'?>"
                "<config><plate>"
                "<metadata key='index' value='1'/>"
                "<metadata key='prediction' value='3600'/>"
                "<metadata key='weight' value='42.5'/>"
                "<filament id='1' type='PLA' color='#00AE42' used_g='42.5' used_m='14.2'/>"
                "</plate></config>",
            )
            zf.writestr("3D/3dmodel.model", "<model/>")

        # The file turns up between the give-up and the first retry -- the
        # cover endpoint pulling it for a thumbnail, as it did at 03:15:11.
        bambu_ftp.cache_3mf_download(printer_id, f"{SUBTASK}.gcode.3mf", source)
        monkeypatch.setattr(main_module, "_FALLBACK_3MF_TRANSFER_RETRY_DELAYS_SECONDS", (0.01,))

        try:
            with patch.object(main_module, "async_session", maker):
                main_module._schedule_fallback_3mf_retry(
                    printer_id=printer_id,
                    archive_id=archive_id,
                    filenames=[f"{SUBTASK}.gcode.3mf"],
                    reason=REASON_FTP_TRANSFER_FAILED,
                )
                await asyncio.wait_for(main_module._fallback_3mf_retry_tasks[printer_id], timeout=5)
        finally:
            bambu_ftp.clear_3mf_cache(printer_id, delete_files=False)

        async with maker() as db:
            recovered = await db.get(PrintArchive, archive_id)
            assert recovered.file_path, "the row still has no 3MF"
            assert recovered.file_size > 0
            # The markers are what the archives banner counts, so they have to
            # go or the install keeps being told about a print that is fine.
            assert not recovered.extra_data.get("no_3mf_available")
            assert not recovered.extra_data.get("no_3mf_reason")


class TestTheLadderSuitsTheCause:
    def test_the_transfer_ladder_starts_well_before_the_cooloff_one(self):
        """A cool-off has to expire first -- 300s of it -- so #2957 places its
        first attempt past that. Nothing has to expire here: #3063's file
        completed 48 seconds after the budget ran out, and waiting five minutes
        to ask would mean the cover endpoint is the only thing that ever
        recovers these.
        """
        from backend.app.main import (
            _FALLBACK_3MF_RETRY_DELAYS_SECONDS,
            _FALLBACK_3MF_TRANSFER_RETRY_DELAYS_SECONDS,
        )

        assert _FALLBACK_3MF_TRANSFER_RETRY_DELAYS_SECONDS[0] < _FALLBACK_3MF_RETRY_DELAYS_SECONDS[0]
        assert _FALLBACK_3MF_TRANSFER_RETRY_DELAYS_SECONDS[0] <= 60.0

    @pytest.mark.asyncio
    async def test_each_cause_gets_its_own_ladder(self, monkeypatch):
        """One scheduler, two callers. Passing the wrong reason would make a
        cool-off retry fire while the cool-off is still running, which the task
        can only answer by deferring."""
        import asyncio

        from backend.app import main as main_module

        slept: list[float] = []

        async def _record(delay):
            slept.append(delay)
            raise asyncio.CancelledError

        monkeypatch.setattr(main_module.asyncio, "sleep", _record)

        for reason, expected in (
            (REASON_FTPS_COOLOFF, main_module._FALLBACK_3MF_RETRY_DELAYS_SECONDS[0]),
            (REASON_FTP_TRANSFER_FAILED, main_module._FALLBACK_3MF_TRANSFER_RETRY_DELAYS_SECONDS[0]),
        ):
            slept.clear()
            main_module._schedule_fallback_3mf_retry(printer_id=1, archive_id=1, filenames=["x.3mf"], reason=reason)
            task = main_module._fallback_3mf_retry_tasks[1]
            with pytest.raises(asyncio.CancelledError):
                await task
            assert slept == [expected]
