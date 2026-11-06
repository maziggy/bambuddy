"""The printer kill switch acts on prints Bambuddy did not start.

Two ways it did not:

- The fast check counted every print in ``_active_prints`` as Bambuddy's, and
  ``on_print_start`` puts every print it archives there, those started from
  Bambu Studio, Handy or the printer's screen included. A print running when the
  switch was turned on, or one archived before the switch looked, was never
  stopped.
- A LAN print reports ``subtask_id`` "0". The database check answered "not sure
  yet" for it and waited, every frame, until the print was archived and the
  first hole let it through.

Bambuddy's own prints are now the ones it dispatched: an expected print, the
print user it recorded, or an archive it adopted for a dispatch or that carries
its dispatch marker. A "0" print is matched to Bambuddy's printing archive on
that printer by file name; if there is none it waits only while a queue job of
Bambuddy's is printing there.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app import main as main_module
from backend.app.main import register_expected_print
from backend.app.models.archive import PrintArchive
from backend.tests.integration.test_external_print_confirmation_1898 import (  # noqa: F401
    SUBTASK,
    _clear_print_state,
    _drive_print_start,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _clear_owned(monkeypatch):
    main_module._owned_print_archive_ids.clear()
    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    yield
    main_module._owned_print_archive_ids.clear()


def _status(subtask_id="0"):
    return SimpleNamespace(
        current_print=None, subtask_name=SUBTASK, subtask_id=subtask_id, gcode_file=f"{SUBTASK}.gcode.3mf"
    )


async def test_a_print_bambuddy_did_not_start_stays_unauthorized_once_archived(test_engine, printer_factory):
    """The AP-1 scenario: archived by on_print_start, the print used to pass the
    fast check from then on."""
    printer_id = (await printer_factory()).id

    await _drive_print_start(test_engine, SimpleNamespace(id=printer_id), download_ok=True)

    assert any(key[0] == printer_id for key in main_module._active_prints)
    assert main_module._is_bambuddy_authorized_print_in_memory(printer_id, _status()) is False


async def test_a_print_bambuddy_dispatched_is_its_own_once_adopted(test_engine, db_session, printer_factory):
    printer_id = (await printer_factory()).id
    archive = PrintArchive(
        printer_id=printer_id,
        filename=f"{SUBTASK}.gcode.3mf",
        file_path=f"archives/test/{SUBTASK}.gcode.3mf",
        file_size=1,
        print_name=SUBTASK,
        status="pending",
    )
    db_session.add(archive)
    await db_session.commit()
    register_expected_print(printer_id, f"{SUBTASK}.gcode.3mf", archive.id)

    await _drive_print_start(test_engine, SimpleNamespace(id=printer_id), download_ok=False)

    # Without the expected entries (print start consumes the names the printer
    # reported; the rest are evicted later), the print is still Bambuddy's.
    main_module._expected_prints.clear()
    assert archive.id in main_module._owned_print_archive_ids
    assert main_module._is_bambuddy_authorized_print_in_memory(printer_id, _status()) is True


def _db(archive, dispatched_queue_item_id=None):
    """Both lookups: by subtask id (one row) and, for "0", Bambuddy's printing rows."""
    rows = [archive] if archive is not None else []
    return SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: archive, scalars=lambda: rows)),
        scalar=AsyncMock(return_value=dispatched_queue_item_id),
    )


def _archive(archive_id, filename, *, marker="run-1", print_name=None):
    return SimpleNamespace(
        id=archive_id, filename=filename, print_name=print_name, billing_run_id=marker, created_by_id=None
    )


async def test_a_lan_print_with_no_dispatch_behind_it_is_unauthorized():
    assert await main_module._is_bambuddy_authorized_print(9, _status("0"), _db(None)) is False


async def test_a_lan_print_matching_bambuddys_dispatched_archive_is_its_own():
    """After a restart: no id to match, so the file name and the dispatch marker."""
    archive = _archive(31, f"{SUBTASK}.gcode.3mf")

    assert await main_module._is_bambuddy_authorized_print(9, _status("0"), _db(archive)) is True
    assert 31 in main_module._owned_print_archive_ids


async def test_a_lan_print_waits_while_bambuddy_has_a_job_printing_there():
    """Nothing of Bambuddy's by that name, but a queue job is printing there."""
    assert await main_module._is_bambuddy_authorized_print(9, _status("0"), _db(None, 401)) is None


async def test_a_name_the_printer_echoes_with_underscores_still_matches():
    """Bambuddy uploads "My Part" and the printer reports "My_Part"."""
    archive = _archive(33, "My Part.gcode.3mf")
    state = SimpleNamespace(current_print=None, subtask_name="My_Part", subtask_id="0", gcode_file="My_Part.gcode.3mf")

    assert await main_module._is_bambuddy_authorized_print(9, state, _db(archive)) is True


async def test_a_bambuddy_archive_of_another_name_does_not_cover_a_lan_print():
    other = _archive(34, "Other_job.gcode.3mf")

    assert await main_module._is_bambuddy_authorized_print(9, _status("0"), _db(other)) is False


async def test_only_bambuddys_printing_archives_on_that_printer_are_read():
    from sqlalchemy.dialects import sqlite

    db = _db(None)
    await main_module._is_bambuddy_authorized_print(9, _status("0"), db)

    query = db.execute.await_args.args[0]
    sql = str(query.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    assert "print_archives.printer_id = 9" in sql
    assert "print_archives.status = 'printing'" in sql
    assert "print_archives.billing_run_id IS NOT NULL" in sql


async def test_a_lan_print_that_names_nothing_yet_waits():
    state = SimpleNamespace(current_print=None, subtask_name="", subtask_id="0", gcode_file="")
    db = _db(None)

    assert await main_module._is_bambuddy_authorized_print(9, state, db) is None
    db.execute.assert_not_awaited()


async def test_the_printers_built_in_calibration_is_left_alone():
    """Bambuddy's Calibrate and the printer's screen play gcode from the
    printer's system partition (measured: /usr/etc/print/<model>/...)."""
    state = SimpleNamespace(
        current_print=None,
        subtask_name="auto_cali_for_user_param.gcode",
        subtask_id="0",
        gcode_file="/usr/etc/print/O1C2/auto_cali_for_user_param.gcode",
    )
    db = _db(None)

    assert await main_module._is_bambuddy_authorized_print(9, state, db) is True
    db.execute.assert_not_awaited()


async def test_a_print_named_like_a_calibration_is_still_a_print():
    """Bambu Studio's pressure-advance print reports /data/Metadata/plate_1.gcode
    and subtask auto_pa_line_calib_mode (measured on an H2C), exactly as a user
    print given that name would."""
    state = SimpleNamespace(
        current_print=None,
        subtask_name="pa_line_calib_mode",
        subtask_id="0",
        gcode_file="/data/Metadata/plate_1.gcode",
    )

    assert await main_module._is_bambuddy_authorized_print(9, state, _db(None)) is False


async def test_a_lan_print_is_not_covered_by_an_archive_that_has_its_own_id():
    from sqlalchemy.dialects import sqlite

    db = _db(None)
    await main_module._is_bambuddy_authorized_print(9, _status("0"), db)

    sql = str(db.execute.await_args.args[0].compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    assert "print_archives.subtask_id IS NULL" in sql


async def test_a_missing_subtask_id_still_waits():
    """The first frames after a restart can arrive before the field is filled."""
    db = _db(None)

    assert await main_module._is_bambuddy_authorized_print(9, _status(None), db) is None
    db.execute.assert_not_awaited()


async def test_a_finished_print_is_no_longer_owned():
    main_module._owned_print_archive_ids.add(41)
    main_module._active_prints[(9, f"{SUBTASK}.gcode.3mf")] = 41

    main_module._active_prints.clear()
    main_module._evict_stale_expected_prints()

    assert 41 not in main_module._owned_print_archive_ids


@pytest.mark.parametrize(("marker", "owned"), [("run-7", True), (None, False)])
async def test_a_print_resumed_after_a_restart_is_owned_only_with_bambuddys_marker(
    test_engine, db_session, printer_factory, marker, owned
):
    """on_print_start resumes the printing archive it finds by name; that
    archive is Bambuddy's only if Bambuddy dispatched it."""
    printer_id = (await printer_factory()).id
    archive = PrintArchive(
        printer_id=printer_id,
        filename=f"{SUBTASK}.gcode.3mf",
        file_path=f"archives/test/{SUBTASK}.gcode.3mf",
        file_size=1,
        print_name=SUBTASK,
        status="printing",
        billing_run_id=marker,
    )
    db_session.add(archive)
    await db_session.commit()

    await _drive_print_start(
        test_engine, SimpleNamespace(id=printer_id), download_ok=False, state_fields={"progress": 40}
    )

    assert (archive.id in main_module._owned_print_archive_ids) is owned


async def test_a_bare_calibration_name_is_not_exempt_from_the_kill_switch():
    """A1, A1 Mini, P1 and A2L report their built-in calibration as a bare
    auto_cali_for_user_param.gcode, which a user's own file can be called too;
    nothing on these printers tells the two apart (no project URL is ever
    recorded there), so the kill switch does not exempt it (maintainer
    decision 2026-10-18)."""
    state = SimpleNamespace(
        current_print=None,
        subtask_name="auto_cali_for_user_param.gcode",
        subtask_id="0",
        gcode_file="auto_cali_for_user_param.gcode",
        current_project_url=None,
    )

    assert await main_module._is_bambuddy_authorized_print(9, state, _db(None)) is False
