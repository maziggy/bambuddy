"""A print started from the printer's own screen names its file in full (#3009).

A job Bambuddy dispatches carries a bare subtask name, and the 3MF lookup
appends the extensions to it. A job started on the printer's touchscreen
reports the file's own name, extension included, so the lookup asked for
``X.3mf.gcode.3mf`` and ``X.3mf.3mf`` first -- six connections, about six
seconds, that could not succeed -- and the existing-archive check compared
against ``X.3mf.3mf`` and could never reattach such a print after a restart.
The name is from a reporter's A1 log.
"""

import re
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

pytestmark = pytest.mark.unit

NAME = "A1-Siraya_Tech_TPU-64D-18JAN26"


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
    printer.plate_detection_enabled = False
    printer.name = "A1"
    printer.model = "A1"
    printer.ip_address = "192.168.1.117"
    printer.access_code = "12345678"
    return printer


async def _run_print_start(subtask_name, filename):
    """Drive on_print_start with every FTP path answering 550, and return the
    remote paths it tried, in order, and the SQL it ran."""
    printer = _printer()
    statements = []

    def execute_router(stmt, *args, **kwargs):
        statements.append(stmt)
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

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_router)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()

    from backend.app.services.bambu_ftp import FileNotOnPrinterError

    download = AsyncMock(side_effect=FileNotOnPrinterError("550"))
    state = MagicMock(current_project_url=f"ftp://{filename}", sdcard=True, sdcard_reported=True)

    with (
        patch("backend.app.main.async_session") as session_maker,
        patch("backend.app.main.notification_service") as notif,
        patch("backend.app.main.smart_plug_manager") as plug,
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main.download_file_async", new=download),
        patch("backend.app.main.download_file_try_paths_async", new=AsyncMock(return_value=None)),
        patch("backend.app.main.with_ftp_retry", new=AsyncMock(return_value=False)),
        patch("backend.app.main.get_cached_3mf", return_value=None),
        patch("backend.app.services.bambu_ftp.list_files_async", new=AsyncMock(return_value=[])),
        patch("backend.app.main.ftps_handshake_blocked", return_value=False),
        patch("backend.app.main.get_ftp_retry_settings", new=AsyncMock(return_value=(False, 3, 2.0, 30))),
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
        patch("backend.app.main._maybe_start_layer_timelapse"),
        patch("backend.app.main._capture_timelapse_baseline_at_start", new_callable=AsyncMock),
        patch("backend.app.main._schedule_fallback_3mf_retry", new=MagicMock()),
    ):
        session_maker.return_value = session
        notif.on_print_start = AsyncMock()
        plug.on_print_start = AsyncMock()
        ws.send_print_start = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        ws.send_archive_created = AsyncMock()
        relay.on_print_start = AsyncMock()
        pm.get_status = MagicMock(return_value=state)
        pm.get_printer = MagicMock(return_value=MagicMock(serial_number="TEST3009"))

        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": filename, "subtask_name": subtask_name})

    tried = [call.args[2] for call in download.await_args_list]
    return tried, statements


def _existing_archive_lookup(statements):
    """The literal SQL of the name-based check for a printing archive to reattach."""
    for stmt in statements:
        try:
            sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        except Exception:
            continue
        if "print_archives" in sql and "print_name" in sql and "printing" in sql:
            return sql
    return None


@pytest.mark.asyncio
async def test_a_subtask_with_its_extension_is_tried_as_the_file_itself_first():
    tried, _ = await _run_print_start(f"{NAME}.3mf", f"{NAME}.3mf")

    assert tried[0] == f"/{NAME}.3mf"


@pytest.mark.asyncio
async def test_no_second_extension_is_appended_to_it():
    tried, _ = await _run_print_start(f"{NAME}.3mf", f"{NAME}.3mf")

    assert not [p for p in tried if ".3mf.gcode.3mf" in p or ".3mf.3mf" in p]


@pytest.mark.asyncio
async def test_only_the_reported_file_is_tried():
    """The printer named the file, so no other name is guessed at -- the list
    is the old one minus the two that could not exist."""
    tried, _ = await _run_print_start(f"{NAME}.3mf", f"{NAME}.3mf")

    assert {p.rsplit("/", 1)[-1] for p in tried} == {f"{NAME}.3mf"}


def _archive_filenames(sql):
    """The file names the existing-archive check accepts."""
    match = re.search(r"filename IN \(([^)]*)\)", sql)
    assert match, sql
    return {name.strip().strip("'") for name in match.group(1).split(",")}


@pytest.mark.asyncio
async def test_the_existing_archive_check_also_accepts_the_file_itself():
    _, statements = await _run_print_start(f"{NAME}.3mf", f"{NAME}.3mf")

    assert f"{NAME}.3mf" in _archive_filenames(_existing_archive_lookup(statements))


@pytest.mark.asyncio
async def test_the_existing_archive_check_gets_nothing_looser():
    """A leftover 'printing' archive of another file with the same base name
    must not swallow this print: a match with progress is taken as the same
    print and no new archive is made."""
    _, statements = await _run_print_start(f"{NAME}.3mf", f"{NAME}.3mf")

    sql = _existing_archive_lookup(statements)
    assert f"{NAME}.gcode.3mf" not in _archive_filenames(sql)
    assert f"print_archives.print_name = '{NAME}.3mf'" in sql


@pytest.mark.asyncio
async def test_a_bare_subtask_is_looked_up_exactly_as_before():
    """The dispatched case, which is every queue and slicer print."""
    tried, _ = await _run_print_start(NAME, f"{NAME}.gcode.3mf")

    assert tried[0] == f"/{NAME}.gcode.3mf"
    assert f"/{NAME}.3mf" in tried


@pytest.mark.asyncio
async def test_a_dot_inside_the_model_name_is_kept():
    tried, _ = await _run_print_start("My.Model", "My.Model.gcode.3mf")

    assert tried[0] == "/My.Model.gcode.3mf"


@pytest.mark.asyncio
async def test_a_dispatched_model_named_with_3mf_keeps_the_old_order():
    """A model can be named "Foo.3mf"; its file is then Foo.3mf.gcode.3mf. The
    printer reports a different file than the subtask, so nothing about the
    name says the extension is the file's own, and a stale Foo.3mf on the card
    must not be tried first."""
    tried, statements = await _run_print_start("Foo.3mf", "/data/Metadata/plate_1.gcode")

    assert tried[0] == "/Foo.3mf.gcode.3mf"
    assert "/Foo.gcode.3mf" not in tried
    assert _archive_filenames(_existing_archive_lookup(statements)) == {"Foo.3mf.3mf", "Foo.3mf.gcode.3mf"}


class TestSubtaskIsTheFile:
    @pytest.mark.parametrize(
        "subtask,filename",
        [
            (f"{NAME}.3mf", f"{NAME}.3mf"),
            (f"{NAME}.gcode.3mf", f"{NAME}.gcode.3mf"),
            ("Part.3mf", "/sdcard/Part.3mf"),
            ("Part.3mf", "ftp://Part.3mf"),
        ],
    )
    def test_the_same_name_as_the_file(self, subtask, filename):
        from backend.app.main import _subtask_is_the_file

        assert _subtask_is_the_file(subtask, filename)

    @pytest.mark.parametrize(
        "subtask,filename",
        [
            (NAME, f"{NAME}.gcode.3mf"),
            ("Foo.3mf", "/data/Metadata/plate_1.gcode"),
            ("Foo.3mf", "Bar.3mf"),
            ("Part.gcode", "Part.gcode"),
            ("", ""),
            ("Part.3mf", None),
        ],
    )
    def test_anything_else(self, subtask, filename):
        from backend.app.main import _subtask_is_the_file

        assert not _subtask_is_the_file(subtask, filename)
