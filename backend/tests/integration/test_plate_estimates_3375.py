"""Each print against the estimate of the plate it printed (#3375).

A multi-plate 3MF's archive adds its plates up (#1593): one plate printed
read as +200 % on the card and skewed the statistics. The archive keeps each
plate's estimate, read from its 3MF, and a print is measured against its
plate's.
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.config import settings as app_settings
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry
from backend.app.services.print_cost_estimate import (
    fill_plate_estimates,
    refresh_plate_estimates,
    run_estimate,
)
from backend.app.utils.threemf_tools import extract_plate_estimates_from_3mf

T1 = datetime(2026, 10, 30, 9)
DIR = "archives/plates"

SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="prediction" value="3600"/>
    <filament id="1" type="PLA" color="#FFFFFF" used_m="10" used_g="30.5"/>
    <filament id="2" type="PLA" color="#000000" used_m="3" used_g="9.5"/>
  </plate>
  <plate>
    <metadata key="index" value="2"/>
    <metadata key="prediction" value="1800"/>
    <filament id="1" type="PLA" color="#FFFFFF" used_m="6" used_g="20"/>
  </plate>
</config>
"""


def _three_mf(path, slice_info: str | None = SLICE_INFO):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        if slice_info is not None:
            zf.writestr("Metadata/slice_info.config", slice_info)
    return path


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    _three_mf(tmp_path / DIR / "two.gcode.3mf")
    return tmp_path


def _file(**overrides):
    values = {"plate_estimates": None, "print_time_seconds": 5400, "filament_used_grams": 60.0}
    return SimpleNamespace(**{**values, **overrides})


PLATES = {"1": {"seconds": 3600, "grams": 40.0}, "2": {"seconds": 1800, "grams": 20.0}}


class TestReadingPlates:
    def test_each_plate_has_its_time_and_grams(self, tmp_path):
        assert extract_plate_estimates_from_3mf(_three_mf(tmp_path / "f.3mf")) == PLATES

    def test_a_file_not_sliced_has_none(self, tmp_path):
        assert extract_plate_estimates_from_3mf(_three_mf(tmp_path / "f.3mf", slice_info=None)) == {}

    def test_an_unreadable_file_is_left_to_read_again(self, tmp_path):
        bad = tmp_path / "f.3mf"
        bad.write_bytes(b"not a zip")

        assert extract_plate_estimates_from_3mf(bad) is None


class TestRunEstimate:
    def test_a_print_of_a_plate_is_that_plates(self):
        estimate = run_estimate(_file(plate_estimates=PLATES), 2)

        assert (estimate.seconds, estimate.grams, estimate.per_plate) == (1800, 20.0, True)

    def test_a_print_of_a_single_plate_file_is_its_plate_whatever_its_number(self):
        estimate = run_estimate(_file(plate_estimates={"2": PLATES["2"]}), 1)

        assert (estimate.seconds, estimate.per_plate) == (1800, True)

    def test_a_print_of_an_unknown_plate_of_several_has_none(self):
        assert run_estimate(_file(plate_estimates=PLATES), None) is None

    def test_plates_not_known_give_the_files_figures(self):
        for plates in (None, {}):
            estimate = run_estimate(_file(plate_estimates=plates), 2)

            assert (estimate.seconds, estimate.grams, estimate.per_plate) == (5400, 60.0, False)


class TestKeepingPlates:
    def test_an_archive_reads_its_files_plates(self, files):
        archive = PrintArchive(filename="two.gcode.3mf", file_path=f"{DIR}/two.gcode.3mf", file_size=1)

        refresh_plate_estimates(archive)

        assert archive.plate_estimates == PLATES

    def test_a_gcode_file_has_no_plates_to_read(self, files):
        (files / DIR / "cube.gcode").write_text("; gcode\n")
        archive = PrintArchive(filename="cube.gcode", file_path=f"{DIR}/cube.gcode", file_size=1)

        refresh_plate_estimates(archive)

        assert archive.plate_estimates == {}

    def test_an_archive_without_its_file_stays_unread(self, files):
        archive = PrintArchive(filename="gone.3mf", file_path=f"{DIR}/gone.3mf", file_size=1)

        refresh_plate_estimates(archive)

        assert archive.plate_estimates is None

    async def test_an_archived_print_keeps_its_files_plates(self, files, db_session, printer_factory):
        from backend.app.services.archive import ArchiveService

        printer = await printer_factory()
        source = _three_mf(files / "upload" / "two.gcode.3mf")

        archive = await ArchiveService(db_session).archive_print(printer.id, source)

        assert archive.plate_estimates == PLATES

    async def test_a_print_given_its_3mf_later_reads_its_plates(self, files, db_session, printer_factory):
        from backend.app.services.archive import ArchiveService

        printer = await printer_factory()
        fallback = PrintArchive(printer_id=printer.id, filename="two.3mf", file_path="", file_size=0, status="printing")
        db_session.add(fallback)
        await db_session.commit()
        source = _three_mf(files / "upload" / "two.gcode.3mf")

        archive = await ArchiveService(db_session).archive_print(printer.id, source, update_archive_id=fallback.id)

        assert (archive.id, archive.plate_estimates) == (fallback.id, PLATES)

    async def test_archives_made_before_get_theirs_once(self, files, db_session, test_engine):
        older = PrintArchive(filename="two.gcode.3mf", file_path=f"{DIR}/two.gcode.3mf", file_size=1)
        gone = PrintArchive(filename="gone.3mf", file_path=f"{DIR}/gone.3mf", file_size=1)
        read = PrintArchive(filename="r.3mf", file_path=f"{DIR}/two.gcode.3mf", file_size=1, plate_estimates={})
        db_session.add_all([older, gone, read])
        await db_session.commit()

        sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        assert await fill_plate_estimates(sessions, batch=1) == 2
        assert await fill_plate_estimates(sessions) == 0

        for row in (older, gone, read):
            await db_session.refresh(row)
        assert (older.plate_estimates, gone.plate_estimates, read.plate_estimates) == (PLATES, {}, {})

    async def test_an_archive_read_meanwhile_keeps_what_was_read(self, files, db_session, test_engine, monkeypatch):
        from backend.app.services import print_cost_estimate

        racing = PrintArchive(filename="two.gcode.3mf", file_path="", file_size=0)
        deleted = PrintArchive(filename="gone.3mf", file_path=f"{DIR}/gone.3mf", file_size=1)
        db_session.add_all([racing, deleted])
        await db_session.commit()
        sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        async def meanwhile(fn, row):
            # Its 3MF recovered and read, the other one deleted, while the
            # fill reads the first.
            if row.id == racing.id:
                async with sessions() as other:
                    (await other.get(PrintArchive, racing.id)).plate_estimates = PLATES
                    await other.delete(await other.get(PrintArchive, deleted.id))
                    await other.commit()
            return fn(row)

        monkeypatch.setattr(print_cost_estimate, "asyncio", SimpleNamespace(to_thread=meanwhile))

        assert await fill_plate_estimates(sessions) == 0
        await db_session.refresh(racing)
        assert racing.plate_estimates == PLATES

    async def test_an_archive_set_back_to_unread_is_read_again(self, files, db_session, test_engine):
        archive = PrintArchive(filename="two.gcode.3mf", file_path=f"{DIR}/two.gcode.3mf", file_size=1)
        archive.plate_estimates = {}
        db_session.add(archive)
        await db_session.commit()
        # As the print start's rollback of an adoption restores its columns.
        archive.plate_estimates = None
        await db_session.commit()

        sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        assert await fill_plate_estimates(sessions) == 1


@pytest.fixture
async def printed(db_session, printer_factory, files):
    """Plate 1 of the file printed in its estimate, then plate 2 in 2000 s
    of its 1800."""
    printer = await printer_factory()
    archive = PrintArchive(
        printer_id=printer.id,
        filename="two.gcode.3mf",
        file_path=f"{DIR}/two.gcode.3mf",
        file_size=1,
        print_name="two",
        print_time_seconds=5400,
        filament_used_grams=60.0,
        status="completed",
        plate_estimates=PLATES,
    )
    db_session.add(archive)
    await db_session.flush()
    first = PrintLogEntry(
        archive_id=archive.id,
        printer_id=printer.id,
        plate_id=1,
        status="completed",
        started_at=T1,
        completed_at=T1 + timedelta(seconds=3600),
        duration_seconds=3600,
        filament_used_grams=40.0,
    )
    second = PrintLogEntry(
        archive_id=archive.id,
        printer_id=printer.id,
        plate_id=2,
        status="completed",
        started_at=T1 + timedelta(hours=2),
        completed_at=T1 + timedelta(hours=2, seconds=2000),
        duration_seconds=2000,
        filament_used_grams=21.0,
    )
    db_session.add_all([first, second])
    await db_session.commit()
    return SimpleNamespace(archive=archive, printer=printer, first=first.id, second=second.id)


async def _card(async_client, archive_id):
    return next(a for a in (await async_client.get("/api/v1/archives/")).json() if a["id"] == archive_id)


@pytest.mark.asyncio
@pytest.mark.integration
class TestReading:
    async def test_the_card_measures_the_latest_print_against_its_plate(self, async_client, printed):
        card = await _card(async_client, printed.archive.id)

        assert (card["time_estimate_seconds"], card["actual_time_seconds"], card["time_accuracy"]) == (1800, 2000, 90.0)

    async def test_a_file_whose_plates_are_unknown_shows_no_badge_once_printed_twice(
        self, async_client, db_session, printed
    ):
        printed.archive.plate_estimates = None
        await db_session.commit()

        card = await _card(async_client, printed.archive.id)

        assert (card["time_estimate_seconds"], card["time_accuracy"]) == (None, None)

    async def test_a_print_not_completed_shows_its_estimate_and_no_badge(self, async_client, db_session, printed):
        second = await db_session.get(PrintLogEntry, printed.second)
        second.status = "failed"
        await db_session.commit()

        card = await _card(async_client, printed.archive.id)

        assert (card["time_estimate_seconds"], card["actual_time_seconds"], card["time_accuracy"]) == (1800, None, None)

    async def test_a_print_of_an_unknown_plate_shows_its_time_and_no_badge(self, async_client, db_session, printed):
        second = await db_session.get(PrintLogEntry, printed.second)
        second.plate_id = None
        await db_session.commit()

        card = await _card(async_client, printed.archive.id)

        assert (card["time_estimate_seconds"], card["actual_time_seconds"], card["time_accuracy"]) == (None, 2000, None)

    async def test_an_archive_shown_as_another_print_has_no_badge_of_the_latest(self, async_client, printed):
        shown = (await async_client.get(f"/api/v1/archives/{printed.archive.id}?run_id={printed.first}")).json()

        assert (shown["plate_id"], shown["time_estimate_seconds"], shown["time_accuracy"]) == (1, None, None)

    async def test_each_print_event_has_its_plates_estimate(self, async_client, printed):
        events = (await async_client.get("/api/v1/archives/slim")).json()

        assert sorted(e["print_time_seconds"] for e in events) == [1800, 3600]

    async def test_each_print_carries_its_plates_estimate(self, async_client, db_session, printed):
        third = PrintLogEntry(archive_id=printed.archive.id, printer_id=printed.printer.id, status="failed")
        db_session.add(third)
        await db_session.commit()

        runs = (await async_client.get(f"/api/v1/archives/{printed.archive.id}/runs")).json()["items"]
        by_id = {r["id"]: (r["estimate_seconds"], r["estimate_grams"]) for r in runs}

        assert by_id == {printed.first: (3600, 40.0), printed.second: (1800, 20.0), third.id: (None, None)}

    async def test_prints_of_a_file_whose_plates_are_unknown_get_no_estimate(self, async_client, db_session, printed):
        printed.archive.plate_estimates = None
        await db_session.commit()

        runs = (await async_client.get(f"/api/v1/archives/{printed.archive.id}/runs")).json()["items"]

        # The file's 5400 s describes neither plate.
        assert [r["estimate_seconds"] for r in runs] == [None, None]

    async def test_the_statistics_measure_each_print_against_its_plate(self, async_client, printed):
        body = (await async_client.get("/api/v1/archives/stats")).json()

        # 100 % and 90 %: against the file's 5400 s they were 150 % and 270 %.
        assert body["average_time_accuracy"] == pytest.approx(95.0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_queued_job_moved_to_another_plate_takes_its_time(async_client, db_session, printed):
    from backend.app.models.print_queue import PrintQueueItem

    item = PrintQueueItem(
        printer_id=printed.printer.id, archive_id=printed.archive.id, position=1, plate_id=1, print_time_seconds=3600
    )
    db_session.add(item)
    await db_session.commit()

    response = await async_client.patch(f"/api/v1/queue/{item.id}", json={"plate_id": 2})

    assert response.status_code == 200, response.text
    await db_session.refresh(item)
    assert item.print_time_seconds == 1800


def test_the_start_notification_gives_the_plates_time(monkeypatch):
    from backend.app import main

    archive = SimpleNamespace(id=7, plate_id=1, plate_estimates=PLATES, print_time_seconds=5400, filament_used_grams=60)
    monkeypatch.setattr(main, "_print_plate_ids", {(3, 7): 2})

    assert main._run_print_time_seconds(archive, 3) == 1800
    # Another printer's dispatch of it: the archive's plate.
    assert main._run_print_time_seconds(archive, 4) == 3600
    # Plates not known: the file's.
    assert main._run_print_time_seconds(SimpleNamespace(**{**vars(archive), "plate_estimates": None}), 3) == 5400
    # A plate not known of several: none, the printer's remaining time is used.
    assert main._run_print_time_seconds(SimpleNamespace(**{**vars(archive), "plate_id": None}), 4) is None
