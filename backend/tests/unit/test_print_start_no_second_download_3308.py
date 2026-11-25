"""A 3MF whose transfer timed out is not downloaded a second time (#3308).

The reporter's A1 serves FTPS at ~87 KB/s during its start-up routine. The
sweep found ``/side_skull_Medium 7x5.5x2 in.gcode.3mf`` -- the printer answered
SIZE -- and spent the 300 s its size bought it without finishing. The directory
walk then listed the folder, picked that same file as the exact-name match and
spent another 300 s on it: twelve minutes holding the printer's one FTPS slot
at its slowest, with the cover lookups queued behind it, before the fallback
archive and its recovery retry (which may take 900 s) could even start.

A path the sweep failed to transfer is skipped by the walk. Only that path:
while a file of the print's own name is listed the walk offers no file that
merely contains the name, so skipping it cannot let a look-alike stand in. And it counts as a
file listed for the print whose transfer failed, so an earlier run's 3MF
(#3307) does not stand in for it either -- the retry fetches the real file.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core.config import settings as app_settings
from backend.app.main import (
    _active_prints,
    _expected_print_creators,
    _expected_print_registered_at,
    _expected_prints,
    _print_ams_mappings,
    _timelapse_baselines,
)
from backend.app.services.bambu_ftp import FileNotOnPrinterError

pytestmark = pytest.mark.unit

FILE = "side_skull_Medium 7x5.5x2 in.gcode.3mf"
EXACT = f"/{FILE}"


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
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
    with patch.object(app_settings, "archive_dir", tmp_path / "archive"):
        yield
    for d in dicts:
        d.clear()


def _printer():
    printer = MagicMock()
    printer.id = 2
    printer.auto_archive = True
    printer.external_camera_enabled = False
    printer.external_camera_url = None
    printer.plate_detection_enabled = False
    printer.name = "A1"
    printer.model = "A1"
    printer.ip_address = "192.0.2.20"
    printer.access_code = "12345678"
    return printer


async def _print_start(*, transfer, listing):
    """on_print_start for the reporter's A1 print; *transfer* decides each path."""
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

    attempts: list[str] = []

    async def download(ip, code, remote_path, local_path, **kwargs):
        attempts.append(remote_path)
        return await transfer(remote_path, local_path)

    state = MagicMock(current_project_url=None, sdcard=True, sdcard_reported=True)
    archived = MagicMock(id=99, printer_id=2, status="printing", filename=FILE)
    service = MagicMock()
    service.archive_print = AsyncMock(return_value=archived)
    finder = AsyncMock(return_value=None)
    retry = MagicMock()

    with ExitStack() as stack:
        session_maker = stack.enter_context(patch("backend.app.main.async_session"))
        notif = stack.enter_context(patch("backend.app.main.notification_service"))
        plug = stack.enter_context(patch("backend.app.main.smart_plug_manager"))
        ws = stack.enter_context(patch("backend.app.main.ws_manager"))
        relay = stack.enter_context(patch("backend.app.main.mqtt_relay"))
        pm = stack.enter_context(patch("backend.app.main.printer_manager"))
        stack.enter_context(patch("backend.app.main.download_file_async", new=download))
        stack.enter_context(patch("backend.app.main.download_file_try_paths_async", new=AsyncMock(return_value=None)))
        stack.enter_context(patch("backend.app.main.get_cached_3mf", return_value=None))
        stack.enter_context(patch("backend.app.main.cache_3mf_download"))
        stack.enter_context(patch("backend.app.main.peek_plate_index_in_3mf", return_value=None))
        stack.enter_context(
            patch("backend.app.services.bambu_ftp.list_files_async", new=AsyncMock(return_value=list(listing)))
        )
        stack.enter_context(patch("backend.app.main.ftps_handshake_blocked", return_value=False))
        stack.enter_context(
            patch("backend.app.main.get_ftp_retry_settings", new=AsyncMock(return_value=(False, 3, 2.0, 30)))
        )
        stack.enter_context(patch("backend.app.main._record_energy_start", new_callable=AsyncMock))
        stack.enter_context(patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock))
        stack.enter_context(patch("backend.app.main._maybe_start_layer_timelapse"))
        stack.enter_context(patch("backend.app.main._capture_timelapse_baseline_at_start", new_callable=AsyncMock))
        stack.enter_context(patch("backend.app.main._schedule_fallback_3mf_retry", new=retry))
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
        pm.get_printer = MagicMock(return_value=MagicMock(serial_number="TEST3308"))

        from backend.app.main import on_print_start

        await on_print_start(2, {"filename": FILE, "subtask_name": FILE})

    fallback = next(
        (r for r in added if isinstance(getattr(r, "extra_data", None), dict) and r.extra_data.get("no_3mf_available")),
        None,
    )
    return SimpleNamespace(attempts=attempts, fallback=fallback, service=service, finder=finder, retry=retry)


def _listed(*names):
    return [{"name": name, "is_directory": False} for name in names]


async def _times_out_at_root(remote_path, local_path):
    """The exact path is served but never finishes; every other path is a 550."""
    if remote_path == EXACT:
        return False
    raise FileNotOnPrinterError(remote_path)


class TestNoSecondDownload:
    @pytest.mark.asyncio
    async def test_the_reported_case_tries_the_file_once(self):
        run = await _print_start(
            transfer=_times_out_at_root,
            listing=_listed("FractMod.gcode.3mf", f"sort_of_{FILE}", FILE),
        )
        assert run.attempts.count(EXACT) == 1

    @pytest.mark.asyncio
    async def test_a_transfer_that_errored_is_not_tried_again_either(self):
        async def errors_at_root(remote_path, local_path):
            if remote_path == EXACT:
                raise OSError("connection reset mid-transfer")
            raise FileNotOnPrinterError(remote_path)

        run = await _print_start(transfer=errors_at_root, listing=_listed(FILE))
        assert run.attempts.count(EXACT) == 1

    @pytest.mark.asyncio
    async def test_and_leaves_it_to_the_retry(self):
        run = await _print_start(transfer=_times_out_at_root, listing=_listed(FILE))
        assert run.fallback is not None
        assert run.fallback.extra_data["no_3mf_reason"] == "ftp_transfer_failed"
        run.retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_file_that_only_contains_the_name_stands_in(self):
        run = await _print_start(transfer=_times_out_at_root, listing=_listed(f"sort_of_{FILE}", FILE))
        assert f"/sort_of_{FILE}" not in run.attempts
        run.service.archive_print.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nor_an_earlier_runs_3mf(self):
        """The file is on the card under its own name; #3307's stand-in is
        for a file the printer never names."""
        run = await _print_start(transfer=_times_out_at_root, listing=_listed(FILE))
        run.finder.assert_not_awaited()


def _served_on_second_try(path, *, timed_out=()):
    """*path* answers 550 to the sweep and is served to the walk, standing for
    a file the walk reaches; paths in *timed_out* never finish."""
    seen: dict[str, int] = {}

    async def transfer(remote_path, local_path):
        seen[remote_path] = seen.get(remote_path, 0) + 1
        if remote_path in timed_out:
            return False
        if remote_path == path and seen[remote_path] == 2:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(b"PK\x03\x04")
            return True
        raise FileNotOnPrinterError(remote_path)

    return transfer


class TestTheWalkOtherwiseAsBefore:
    @pytest.mark.asyncio
    async def test_550_everywhere_still_walks_and_downloads(self):
        run = await _print_start(transfer=_served_on_second_try(f"/cache/{FILE}"), listing=_listed(FILE))
        assert run.attempts.count(f"/cache/{FILE}") == 2
        run.service.archive_print.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_only_the_failed_path_is_skipped(self):
        run = await _print_start(
            transfer=_served_on_second_try(f"/cache/{FILE}", timed_out=(EXACT,)), listing=_listed(FILE)
        )
        assert run.attempts.count(EXACT) == 1
        run.service.archive_print.assert_awaited_once()
