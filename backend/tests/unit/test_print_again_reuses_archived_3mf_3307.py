"""A "Print again" from the printer's screen is archived with its 3MF (#3307).

On a touchscreen reprint the printer never names the file on the card. It
reports its own history copy under a generic name
(``file:///userdata/project_file.gcode.3mf``, which port 990 cannot serve), the
3MF's internal title as ``subtask_name`` -- "IKEA Skadis Support Feet", while
the card file is "Support Feet 20mm Standoff_Build Sandbox.gcode.3mf" -- and
``/data/Metadata/plate_3.gcode``. Every name built from those misses, so the
second run of a job archived fine the first time got a no-3MF fallback.

The first run's archive holds that 3MF, and its ``_print_data`` records the
same subtask name and plate. When the lookup comes back empty, the newest such
archive on the same printer lends its 3MF to the new run. A layer count the
printer has already reported must agree, so a re-slice under the same title is
not mistaken for the same job. Not on a cool-off or a failed transfer: there
the file was reachable and its own retry fetches the real one.
"""

import shutil
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core.config import settings as app_settings
from backend.app.main import (
    _active_prints,
    _archived_3mf_for_repeat,
    _expected_print_creators,
    _expected_print_registered_at,
    _expected_prints,
    _print_ams_mappings,
    _timelapse_baselines,
)
from backend.app.models.archive import PrintArchive
from backend.app.models.printer import Printer
from backend.app.services.bambu_ftp import FileNotOnPrinterError

pytestmark = pytest.mark.unit

TITLE = "IKEA Skadis Support Feet"
PLATE_3 = "/data/Metadata/plate_3.gcode"
CARD_FILE = "Support Feet 20mm Standoff_Build Sandbox.gcode.3mf"


@pytest.fixture
def base_dir(tmp_path):
    # archive_dir too: it is its own setting, and the copy lands in its temp/.
    with (
        patch.object(app_settings, "base_dir", tmp_path),
        patch.object(app_settings, "archive_dir", tmp_path / "archive"),
    ):
        yield tmp_path


def _stored_3mf(base_dir, folder: str, content: bytes = b"PK\x03\x04 3mf") -> str:
    path = base_dir / "archive" / folder / CARD_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path.relative_to(base_dir))


class TestFindingTheEarlierRun:
    @pytest.fixture
    async def printers(self, db_session):
        for n in (1, 2):
            db_session.add(
                Printer(id=n, name=f"H2C-{n}", serial_number=f"SN{n}", ip_address="192.0.2.1", access_code="1")
            )
        await db_session.commit()

    async def _archive(self, db, base_dir, folder, *, printer_id=1, subtask=TITLE, gcode=PLATE_3, age_min=60, **fields):
        archive = PrintArchive(
            printer_id=printer_id,
            filename=CARD_FILE,
            file_path=_stored_3mf(base_dir, folder),
            file_size=10,
            print_name=f"{TITLE} - Plate 3",
            status="completed",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=age_min),
            extra_data={"_print_data": {"filename": gcode, "subtask_name": subtask}},
            **fields,
        )
        db.add(archive)
        await db.commit()
        return archive

    async def _find(self, db, *, printer_id=1, subtask=TITLE, gcode=PLATE_3, layers=None):
        return await _archived_3mf_for_repeat(db, printer_id, subtask, gcode, layers)

    @pytest.mark.asyncio
    async def test_the_same_job_on_the_same_printer_is_found(self, db_session, printers, base_dir):
        first = await self._archive(db_session, base_dir, "14")
        assert (await self._find(db_session)).id == first.id

    @pytest.mark.asyncio
    async def test_the_newest_run_wins(self, db_session, printers, base_dir):
        await self._archive(db_session, base_dir, "14", age_min=120)
        newer = await self._archive(db_session, base_dir, "15", age_min=10)
        assert (await self._find(db_session)).id == newer.id

    @pytest.mark.asyncio
    async def test_another_plate_is_not(self, db_session, printers, base_dir):
        await self._archive(db_session, base_dir, "14", gcode="/data/Metadata/plate_2.gcode")
        assert await self._find(db_session) is None

    @pytest.mark.asyncio
    async def test_another_title_is_not(self, db_session, printers, base_dir):
        await self._archive(db_session, base_dir, "14", subtask="IKEA Skadis Hook")
        assert await self._find(db_session) is None

    @pytest.mark.asyncio
    async def test_another_printer_is_not(self, db_session, printers, base_dir):
        await self._archive(db_session, base_dir, "14", printer_id=2)
        assert await self._find(db_session) is None

    @pytest.mark.asyncio
    async def test_a_deleted_archive_is_not(self, db_session, printers, base_dir):
        await self._archive(db_session, base_dir, "14", deleted_at=datetime.now(timezone.utc))
        assert await self._find(db_session) is None

    @pytest.mark.asyncio
    async def test_a_fallback_archive_without_a_3mf_is_not(self, db_session, printers, base_dir):
        archive = await self._archive(db_session, base_dir, "14")
        archive.file_path = ""
        await db_session.commit()
        assert await self._find(db_session) is None

    @pytest.mark.asyncio
    async def test_an_archive_whose_file_is_gone_is_skipped_for_an_older_one(self, db_session, printers, base_dir):
        older = await self._archive(db_session, base_dir, "14", age_min=120)
        await self._archive(db_session, base_dir, "15", age_min=10)
        shutil.rmtree(base_dir / "archive" / "15")
        assert (await self._find(db_session)).id == older.id

    @pytest.mark.asyncio
    async def test_a_different_layer_count_is_another_slice(self, db_session, printers, base_dir):
        await self._archive(db_session, base_dir, "14", total_layers=120)
        assert await self._find(db_session, layers=96) is None
        assert await self._find(db_session, layers=120) is not None

    @pytest.mark.asyncio
    async def test_an_unknown_layer_count_on_either_side_does_not_decide(self, db_session, printers, base_dir):
        await self._archive(db_session, base_dir, "14")
        assert await self._find(db_session, layers=96) is not None
        assert await self._find(db_session, layers=None) is not None

    @pytest.mark.asyncio
    async def test_nothing_to_match_on_finds_nothing(self, db_session, printers, base_dir):
        # A run that recorded no plate is no evidence for a print reporting none.
        await self._archive(db_session, base_dir, "14", gcode="")
        assert await self._find(db_session, gcode="") is None
        await self._archive(db_session, base_dir, "15", subtask="")
        assert await self._find(db_session, subtask="") is None

    @pytest.mark.asyncio
    async def test_fallback_archives_do_not_use_up_the_lookback(self, db_session, printers, base_dir):
        """Every failed run leaves a no-3MF row; they must not push the run
        that has the file out of the rows compared."""
        good = await self._archive(db_session, base_dir, "14", age_min=120)
        empty = await self._archive(db_session, base_dir, "17", age_min=10)
        empty.file_path = ""
        await db_session.commit()
        with patch("backend.app.main._REPEAT_LOOKBACK", 1):
            assert (await self._find(db_session)).id == good.id

    @pytest.mark.asyncio
    async def test_unreadable_extra_data_is_skipped(self, db_session, printers, base_dir):
        archive = await self._archive(db_session, base_dir, "14")
        archive.extra_data = {"_print_data": "not a dict"}
        await db_session.commit()
        assert await self._find(db_session) is None


# --- on_print_start wiring ---------------------------------------------------


@pytest.fixture
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
    printer.plate_detection_enabled = False
    printer.name = "H2C"
    printer.model = "H2C"
    printer.ip_address = "192.0.2.10"
    printer.access_code = "12345678"
    return printer


async def _print_again(
    base_dir,
    *,
    earlier,
    project_url="file:///userdata/project_file.gcode.3mf",
    download=None,
    listing=(),
    blocked=False,
):
    """Run on_print_start for the reporter's reprint; every lookup misses."""
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

    state = MagicMock(current_project_url=project_url, sdcard=True, sdcard_reported=True)
    archived = MagicMock(id=99, printer_id=1, status="printing", filename=CARD_FILE)
    service = MagicMock()
    finder = AsyncMock(return_value=earlier)
    copied: dict = {}

    async def archive_print(**kwargs):
        copied["bytes"] = kwargs["source_file"].read_bytes()
        return archived

    service.archive_print = AsyncMock(side_effect=archive_print)

    with ExitStack() as stack:
        session_maker = stack.enter_context(patch("backend.app.main.async_session"))
        notif = stack.enter_context(patch("backend.app.main.notification_service"))
        plug = stack.enter_context(patch("backend.app.main.smart_plug_manager"))
        ws = stack.enter_context(patch("backend.app.main.ws_manager"))
        relay = stack.enter_context(patch("backend.app.main.mqtt_relay"))
        pm = stack.enter_context(patch("backend.app.main.printer_manager"))
        stack.enter_context(
            patch(
                "backend.app.main.download_file_async",
                new=download or AsyncMock(side_effect=FileNotOnPrinterError("550")),
            )
        )
        stack.enter_context(patch("backend.app.main.download_file_try_paths_async", new=AsyncMock(return_value=None)))
        stack.enter_context(patch("backend.app.main.get_cached_3mf", return_value=None))
        stack.enter_context(patch("backend.app.main.cache_3mf_download"))
        stack.enter_context(patch("backend.app.main.peek_plate_index_in_3mf", return_value=3))
        stack.enter_context(
            patch("backend.app.services.bambu_ftp.list_files_async", new=AsyncMock(return_value=list(listing)))
        )
        stack.enter_context(patch("backend.app.main.ftps_handshake_blocked", return_value=blocked))
        stack.enter_context(
            patch("backend.app.main.get_ftp_retry_settings", new=AsyncMock(return_value=(False, 3, 2.0, 30)))
        )
        stack.enter_context(patch("backend.app.main._record_energy_start", new_callable=AsyncMock))
        stack.enter_context(patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock))
        stack.enter_context(patch("backend.app.main._maybe_start_layer_timelapse"))
        stack.enter_context(patch("backend.app.main._capture_timelapse_baseline_at_start", new_callable=AsyncMock))
        stack.enter_context(patch("backend.app.main._schedule_fallback_3mf_retry", new=MagicMock()))
        stack.enter_context(
            patch("backend.app.main._ask_outcome_for_external_print", new=AsyncMock(return_value=False))
        )
        stack.enter_context(
            patch("backend.app.main._drop_archive_of_deleted_printer", new=AsyncMock(return_value=False))
        )
        stack.enter_context(patch("backend.app.main._printer_gone", new=AsyncMock(return_value=False)))
        stack.enter_context(patch("backend.app.main.ArchiveService", return_value=service))
        stack.enter_context(patch("backend.app.main._archived_3mf_for_repeat", new=finder))
        session_maker.return_value = session
        notif.on_print_start = AsyncMock()
        plug.on_print_start = AsyncMock()
        ws.send_print_start = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        ws.send_archive_created = AsyncMock()
        relay.on_print_start = AsyncMock()
        pm.get_status = MagicMock(return_value=state)
        pm.get_printer = MagicMock(return_value=MagicMock(serial_number="TEST3307"))

        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": PLATE_3, "subtask_name": TITLE, "raw_data": {"total_layer_num": 120}})

    fallback = next(
        (r for r in added if isinstance(getattr(r, "extra_data", None), dict) and r.extra_data.get("no_3mf_available")),
        None,
    )
    return SimpleNamespace(service=service, finder=finder, fallback=fallback, copied=copied)


def _earlier(base_dir, content=b"PK\x03\x04 the first run's 3MF"):
    return SimpleNamespace(id=14, filename=CARD_FILE, file_path=_stored_3mf(base_dir, "14", content))


@pytest.mark.usefixtures("_clear_dicts")
class TestPrintAgain:
    @pytest.mark.asyncio
    async def test_the_reported_case_is_archived_with_the_earlier_runs_3mf(self, base_dir):
        run = await _print_again(base_dir, earlier=_earlier(base_dir))

        assert run.fallback is None
        assert run.copied["bytes"] == b"PK\x03\x04 the first run's 3MF"
        kwargs = run.service.archive_print.await_args.kwargs
        # Said where it came from, for whoever reads the archive later.
        assert kwargs["print_data"]["_3mf_from_archive"] == 14
        # Named after the card file, not after the temp copy.
        assert kwargs["original_filename"] == CARD_FILE

    @pytest.mark.asyncio
    async def test_the_copy_is_per_printer(self, base_dir):
        """Two printers reprinting the same job at once must not share one
        temp file: one copy would truncate the other mid-archive."""
        run = await _print_again(base_dir, earlier=_earlier(base_dir))
        assert run.service.archive_print.await_args.kwargs["source_file"].name == f"reprint_1_{CARD_FILE}"

    @pytest.mark.asyncio
    async def test_a_copy_that_fails_leaves_the_fallback_archive(self, base_dir):
        earlier = SimpleNamespace(id=14, filename=CARD_FILE, file_path="archive/gone/" + CARD_FILE)
        run = await _print_again(base_dir, earlier=earlier)
        assert run.fallback is not None
        run.service.archive_print.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_looked_up_by_title_plate_and_layer_count(self, base_dir):
        run = await _print_again(base_dir, earlier=_earlier(base_dir))
        assert run.finder.await_args.args[1:] == (1, TITLE, PLATE_3, 120)

    @pytest.mark.asyncio
    async def test_the_earlier_archive_keeps_its_own_file(self, base_dir):
        earlier = _earlier(base_dir)
        await _print_again(base_dir, earlier=earlier)
        assert (base_dir / earlier.file_path).read_bytes() == b"PK\x03\x04 the first run's 3MF"

    @pytest.mark.asyncio
    async def test_a_name_sweep_that_missed_too(self, base_dir):
        """The reporter's first failure (archive 17): no internal-storage URL,
        a full sweep and directory walk, all missing."""
        run = await _print_again(base_dir, earlier=_earlier(base_dir), project_url=None)
        assert run.fallback is None
        assert run.service.archive_print.await_count == 1

    @pytest.mark.asyncio
    async def test_no_earlier_run_is_a_fallback_archive_as_before(self, base_dir):
        run = await _print_again(base_dir, earlier=None)
        assert run.fallback is not None
        run.service.archive_print.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_when_the_card_listed_a_file_for_it(self, base_dir):
        """The directory walk found a file named for the print and only its
        transfer failed: that file is on the card, and the retry fetches it --
        possibly a newer slice than the earlier run."""
        run = await _print_again(
            base_dir,
            earlier=_earlier(base_dir),
            project_url=None,
            download=AsyncMock(return_value=False),
            listing=[{"name": f"{TITLE}.gcode.3mf", "is_directory": False}],
        )
        run.finder.assert_not_awaited()
        assert run.fallback is not None

    @pytest.mark.asyncio
    async def test_a_sweep_path_that_timed_out_is_no_such_evidence(self, base_dir):
        """The retry would try the same names, which this print's file never
        had, so leaving it to the retry left the archive empty for good."""
        run = await _print_again(
            base_dir, earlier=_earlier(base_dir), project_url=None, download=AsyncMock(return_value=False)
        )
        assert run.fallback is None
        assert run.service.archive_print.await_count == 1

    @pytest.mark.asyncio
    async def test_nor_is_a_cool_off(self, base_dir):
        run = await _print_again(base_dir, earlier=_earlier(base_dir), project_url=None, blocked=True)
        assert run.fallback is None
        assert run.service.archive_print.await_count == 1
