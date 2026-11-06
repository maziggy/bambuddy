"""A print started outside the cloud is linked to its archive (#3327).

Bambu Studio over LAN, Handy and the printer's screen report subtask_id "0",
which Bambuddy stores as no id at all, so status found the archive of such a
print by subtask_id never: current_archive_id stayed null for the whole print,
and the printer card could not name the plate of a multi-plate file. Status now
falls back to the archive Bambuddy bound to the running print, and after a
restart, which forgets that binding, to its printing archive by name. The
cover reads the running print's 3MF from that archive too instead of the
printer. Skip-objects does only through the binding: by name it could offer an
earlier run's objects to skip.
"""

import logging
import zipfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core.config import settings as app_settings
from backend.app.main import _active_prints, _restore_printable_objects
from backend.app.services.bambu_mqtt import PrinterState

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

NAME = "Benchy plates"


@pytest.fixture(autouse=True)
def _bindings():
    _active_prints.clear()
    yield
    _active_prints.clear()


def _state(run_state="RUNNING", subtask_id="0"):
    state = PrinterState()
    state.connected = True
    state.state = run_state
    state.subtask_id = subtask_id
    state.subtask_name = NAME
    state.gcode_file = "/data/Metadata/plate_5.gcode"
    return state


async def _statuses(async_client, states):
    with patch("backend.app.api.routes.printers.printer_manager") as manager:
        manager.get_status = MagicMock(side_effect=lambda pid: states.get(pid))
        manager.is_awaiting_plate_clear = MagicMock(return_value=False)
        singles = {}
        for pid in states:
            response = await async_client.get(f"/api/v1/printers/{pid}/status")
            assert response.status_code == 200
            singles[pid] = response.json()["current_archive_id"]
        bulk = await async_client.get("/api/v1/bulk/printer-statuses", params={"ids": ",".join(map(str, states))})
        assert bulk.status_code == 200
        assert {row["id"]: row["current_archive_id"] for row in bulk.json()} == singles
    return singles


async def test_a_lan_print_is_linked_to_its_archive(async_client, printer_factory, archive_factory):
    printer = await printer_factory(model="H2C")
    archive = await archive_factory(printer.id, status="printing", subtask_id=None, filename=f"{NAME}.3mf")
    _active_prints[(printer.id, f"{NAME}.3mf")] = archive.id

    linked = await _statuses(async_client, {printer.id: _state()})

    assert linked[printer.id] == archive.id


async def test_also_while_paused(async_client, printer_factory, archive_factory):
    printer = await printer_factory()
    archive = await archive_factory(printer.id, status="printing", subtask_id=None)
    _active_prints[(printer.id, NAME)] = archive.id

    linked = await _statuses(async_client, {printer.id: _state("PAUSE")})

    assert linked[printer.id] == archive.id


async def test_not_before_its_archive_exists(async_client, printer_factory, archive_factory):
    """Nor to an archive of an earlier print that also had no id."""
    printer = await printer_factory()
    await archive_factory(printer.id, status="printing", subtask_id=None)
    await archive_factory(printer.id, status="printing", subtask_id="0")

    linked = await _statuses(async_client, {printer.id: _state()})

    assert linked[printer.id] is None


async def test_not_to_another_printers_print(async_client, printer_factory, archive_factory):
    printer, other = await printer_factory(), await printer_factory()
    archive = await archive_factory(other.id, status="printing", subtask_id=None)
    _active_prints[(other.id, NAME)] = archive.id

    linked = await _statuses(async_client, {printer.id: _state()})

    assert linked[printer.id] is None


async def test_not_once_the_print_is_over(async_client, printer_factory, archive_factory):
    printer = await printer_factory()
    archive = await archive_factory(printer.id, status="printing", subtask_id=None)
    _active_prints[(printer.id, NAME)] = archive.id

    linked = await _statuses(async_client, {printer.id: _state("FINISH")})

    assert linked[printer.id] is None


async def test_a_cloud_print_is_still_linked_by_its_id(async_client, printer_factory, archive_factory):
    printer = await printer_factory()
    by_id = await archive_factory(printer.id, status="printing", subtask_id="524288")
    bound = await archive_factory(printer.id, status="printing", subtask_id=None)
    _active_prints[(printer.id, NAME)] = bound.id

    linked = await _statuses(async_client, {printer.id: _state(subtask_id="524288")})

    assert linked[printer.id] == by_id.id


async def test_a_cloud_print_without_an_archive_by_its_id_uses_the_binding(
    async_client, printer_factory, archive_factory
):
    printer = await printer_factory()
    bound = await archive_factory(printer.id, status="printing", subtask_id=None)
    _active_prints[(printer.id, NAME)] = bound.id

    linked = await _statuses(async_client, {printer.id: _state(subtask_id="524288")})

    assert linked[printer.id] == bound.id


class TestAfterARestart:
    """The binding is gone and only Bambuddy's own prints are bound again."""

    async def test_a_lan_print_is_found_by_its_name(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory(model="H2C")
        await archive_factory(printer.id, status="printing", subtask_id=None, filename="Older.3mf", print_name="Older")
        archive = await archive_factory(
            printer.id, status="printing", subtask_id=None, filename=f"{NAME}.3mf", print_name=NAME
        )

        linked = await _statuses(async_client, {printer.id: _state()})

        assert linked[printer.id] == archive.id

    async def test_as_the_printer_echoes_the_name(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="printing", subtask_id=None, filename=f"{NAME}.3mf")
        state = _state()
        state.subtask_name = NAME.replace(" ", "_")

        linked = await _statuses(async_client, {printer.id: state})

        assert linked[printer.id] == archive.id

    async def test_not_a_finished_run_of_the_same_file(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        await archive_factory(printer.id, status="completed", subtask_id=None, filename=f"{NAME}.3mf")

        linked = await _statuses(async_client, {printer.id: _state()})

        assert linked[printer.id] is None

    async def test_not_a_print_that_had_an_id_of_its_own(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        await archive_factory(printer.id, status="printing", subtask_id="524288", filename=f"{NAME}.3mf")

        linked = await _statuses(async_client, {printer.id: _state()})

        assert linked[printer.id] is None

    async def test_not_for_a_cloud_print(self, async_client, printer_factory, archive_factory):
        """Its own id finds its archive; a name is no better than that."""
        printer = await printer_factory()
        await archive_factory(printer.id, status="printing", subtask_id=None, filename=f"{NAME}.3mf")

        linked = await _statuses(async_client, {printer.id: _state(subtask_id="524288")})

        assert linked[printer.id] is None

    async def test_not_a_deleted_archive(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        await archive_factory(
            printer.id, status="printing", subtask_id=None, filename=f"{NAME}.3mf", deleted_at=datetime.now()
        )

        linked = await _statuses(async_client, {printer.id: _state()})

        assert linked[printer.id] is None


class TestADeletedArchive:
    """Deleting the archive of a running print leaves its binding behind."""

    async def test_is_not_named_through_the_binding(self, async_client, printer_factory, archive_factory, db_session):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="printing", subtask_id=None, filename=f"{NAME}.3mf")
        _active_prints[(printer.id, NAME)] = archive.id
        await db_session.delete(archive)
        await db_session.commit()

        linked = await _statuses(async_client, {printer.id: _state()})

        assert linked[printer.id] is None

    async def test_nor_when_moved_to_the_bin(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, status="printing", subtask_id=None, deleted_at=datetime.now())
        _active_prints[(printer.id, NAME)] = archive.id

        linked = await _statuses(async_client, {printer.id: _state()})

        assert linked[printer.id] is None

    async def test_nor_by_a_cloud_prints_id(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        await archive_factory(printer.id, status="printing", subtask_id="524288", deleted_at=datetime.now())

        linked = await _statuses(async_client, {printer.id: _state(subtask_id="524288")})

        assert linked[printer.id] is None


def _job_3mf(tmp_path):
    """A real zip without a cover, so the cover route answers 404 from it."""
    path = tmp_path / "job.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", "<config/>")
    return str(path.relative_to(tmp_path))


class TestTheRunningPrintsFileOnDisk:
    """What else finds the running print's archive finds a "0" print's too."""

    async def test_the_cover_comes_from_the_archive(self, async_client, printer_factory, archive_factory, tmp_path):
        printer = await printer_factory(model="H2C")
        await archive_factory(
            printer.id, status="printing", subtask_id=None, filename=f"{NAME}.3mf", file_path=_job_3mf(tmp_path)
        )
        state = _state()
        state.sdcard = True
        ftp = AsyncMock(return_value=None)

        with (
            patch.object(app_settings, "base_dir", tmp_path),
            patch("backend.app.api.routes.printers.printer_manager.get_status", return_value=state),
            patch("backend.app.api.routes.printers.get_cached_3mf", return_value=None),
            patch("backend.app.api.routes.printers.download_file_try_paths_async", new=ftp),
        ):
            response = await async_client.get(f"/api/v1/printers/{printer.id}/cover")

        assert response.status_code == 404, response.text
        ftp.assert_not_awaited()

    async def test_the_skip_objects_reload_reads_the_archive(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory(model="H2C")
        archive = await archive_factory(printer.id, status="printing", subtask_id=None)
        _active_prints[(printer.id, NAME)] = archive.id
        client = MagicMock(state=_state())
        ftp = AsyncMock(return_value=None)

        with (
            patch("backend.app.api.routes.printers.printer_manager.get_client", return_value=client),
            patch(
                "backend.app.services.archive.extract_printable_objects_from_archive",
                return_value=({101: {"name": "left"}}, None),
            ) as from_disk,
            patch("backend.app.services.bambu_ftp.download_file_try_paths_async", new=ftp),
        ):
            response = await async_client.get(f"/api/v1/printers/{printer.id}/print/objects")

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        from_disk.assert_called_once()
        ftp.assert_not_awaited()

    async def test_the_object_list_comes_from_the_bound_archive(self, printer_factory, archive_factory, db_session):
        """A reconnect without a restart: the binding is still there."""
        printer = await printer_factory(model="H2C")
        archive = await archive_factory(printer.id, status="printing", subtask_id=None)
        _active_prints[(printer.id, NAME)] = archive.id

        with (
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main._load_objects_from_archive") as load,
        ):
            pm.get_client.return_value = MagicMock(state=MagicMock(printable_objects={}))
            await _restore_printable_objects(printer.id, _state(), db_session, logging.getLogger(__name__))

        load.assert_called_once()
        assert load.call_args.args[0].id == archive.id

    async def test_but_never_by_name_after_a_restart(self, printer_factory, archive_factory, db_session):
        """An earlier run of the same name, left printing by a completion
        missed while Bambuddy was down, cannot be told from this one."""
        printer = await printer_factory(model="H2C")
        await archive_factory(printer.id, status="printing", subtask_id=None, filename=f"{NAME}.3mf")

        with (
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main._load_objects_from_archive") as load,
        ):
            pm.get_client.return_value = MagicMock(state=MagicMock(printable_objects={}))
            await _restore_printable_objects(printer.id, _state(), db_session, logging.getLogger(__name__))

        load.assert_not_called()

    async def test_nor_does_the_reload(self, async_client, printer_factory, archive_factory):
        """It asks the printer for the file it is running instead."""
        printer = await printer_factory(model="H2C")
        await archive_factory(printer.id, status="printing", subtask_id=None, filename=f"{NAME}.3mf")
        client = MagicMock(state=_state())
        ftp = AsyncMock(return_value=None)

        with (
            patch("backend.app.api.routes.printers.printer_manager.get_client", return_value=client),
            patch(
                "backend.app.services.archive.extract_printable_objects_from_archive",
                return_value=({101: {"name": "left"}}, None),
            ) as from_disk,
            patch("backend.app.services.bambu_ftp.download_file_try_paths_async", new=ftp),
        ):
            response = await async_client.get(f"/api/v1/printers/{printer.id}/print/objects")

        assert response.status_code == 200, response.text
        from_disk.assert_not_called()
        ftp.assert_awaited()

    async def test_not_from_an_archive_without_its_3mf(self, printer_factory, archive_factory, db_session):
        printer = await printer_factory(model="H2C")
        archive = await archive_factory(
            printer.id, status="printing", subtask_id=None, filename=f"{NAME}.3mf", file_path=""
        )
        _active_prints[(printer.id, NAME)] = archive.id

        with (
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main._load_objects_from_archive") as load,
        ):
            pm.get_client.return_value = MagicMock(state=MagicMock(printable_objects={}))
            await _restore_printable_objects(printer.id, _state(), db_session, logging.getLogger(__name__))

        load.assert_not_called()

    async def test_nor_from_a_finished_archive_with_the_same_job_id(self, printer_factory, archive_factory, db_session):
        """A leftover row of a completion never seen must not lend its objects."""
        printer = await printer_factory(model="H2C")
        await archive_factory(printer.id, status="completed", subtask_id="524288", filename=f"{NAME}.3mf")

        with (
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main._load_objects_from_archive") as load,
        ):
            pm.get_client.return_value = MagicMock(state=MagicMock(printable_objects={}))
            await _restore_printable_objects(
                printer.id, _state(subtask_id="524288"), db_session, logging.getLogger(__name__)
            )

        load.assert_not_called()
