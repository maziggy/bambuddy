"""The skip-objects list has to survive a restart mid-print.

``PrinterState.printable_objects`` is in-memory and is filled by the print-start
path, which the #1304 guard suppresses on the first RUNNING push after Bambuddy
comes back up. So a restart during a print left the list empty for the rest of
that print: the printer card gates its Skip button on the object count, and the
one endpoint that can rebuild the list is reachable only from the modal that
button opens. Measured on the maintainer's H2C — 8 objects loaded at 09:02,
restart at 09:17, Skip dead for the remaining hour.

The recovery hook now reloads the objects from the archive of the print that is
still running, anchored on the subtask_id the firmware mints per print.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _client(objects=None):
    client = MagicMock()
    client.state = MagicMock(printable_objects=objects if objects is not None else {})
    return client


def _db(archive):
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=archive)
    return db


class TestRestartRecovery:
    @pytest.mark.asyncio
    async def test_objects_are_reloaded_from_the_running_print_s_archive(self):
        from backend.app.main import _restore_printable_objects

        archive = MagicMock(id=360, file_path="archive/7/job/job.3mf")
        state = MagicMock(subtask_id="330151809")

        with (
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main._load_objects_from_archive") as load,
        ):
            pm.get_client.return_value = _client()
            await _restore_printable_objects(7, state, _db(archive), logging.getLogger(__name__))

        load.assert_called_once()
        assert load.call_args.args[0] is archive
        assert load.call_args.args[1] == 7

    @pytest.mark.asyncio
    async def test_a_list_that_is_already_loaded_is_left_alone(self):
        """The hook also fires on a reconnect during a print Bambuddy saw start.
        Reloading there would discard what the user has already skipped, which
        only lives alongside the object list."""
        from backend.app.main import _restore_printable_objects

        state = MagicMock(subtask_id="330151809")

        with (
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main._load_objects_from_archive") as load,
        ):
            pm.get_client.return_value = _client({1: "cube"})
            await _restore_printable_objects(7, state, _db(MagicMock()), logging.getLogger(__name__))

        load.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("subtask_id", ["", "0", None])
    async def test_nothing_is_loaded_without_a_subtask_to_anchor_on(self, subtask_id):
        """A leftover status="printing" row from a completion we never saw must
        not hand this print someone else's objects. The endpoint's reload covers
        the case on demand instead."""
        from backend.app.main import _restore_printable_objects

        state = MagicMock(subtask_id=subtask_id)
        db = _db(MagicMock())

        with (
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main._load_objects_from_archive") as load,
        ):
            pm.get_client.return_value = _client()
            await _restore_printable_objects(7, state, db, logging.getLogger(__name__))

        load.assert_not_called()
        db.scalar.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_matching_archive_is_not_an_error(self):
        from backend.app.main import _restore_printable_objects

        state = MagicMock(subtask_id="330151809")

        with (
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main._load_objects_from_archive") as load,
        ):
            pm.get_client.return_value = _client()
            await _restore_printable_objects(7, state, _db(None), logging.getLogger(__name__))

        load.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_recovery_hook_calls_it(self):
        """Wiring guard: the restore is only useful if on_print_running_observed
        runs it alongside the archive and usage-tracking restores."""
        import backend.app.main as main

        state = MagicMock(subtask_id="330151809", subtask_name="job")
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        )

        with (
            patch("backend.app.main.async_session", return_value=session),
            patch("backend.app.main.printer_manager") as pm,
            patch("backend.app.main._is_bambuddy_authorized_print", new=AsyncMock(return_value=True)),
            patch("backend.app.main._restore_usage_tracking_session", new=AsyncMock()),
            patch("backend.app.main._restore_printable_objects", new=AsyncMock()) as restore,
        ):
            pm.get_status.return_value = state
            await main.on_print_running_observed(7, {})

        restore.assert_awaited_once()
        assert restore.await_args.args[0] == 7


class TestExtractFromArchiveFile:
    def test_a_missing_file_is_empty_rather_than_an_error(self, tmp_path):
        from backend.app.services.archive import extract_printable_objects_from_archive

        assert extract_printable_objects_from_archive(tmp_path / "gone.3mf") == ({}, None)

    def test_a_fallback_archive_with_no_3mf_is_empty(self, tmp_path):
        """An archive created without its 3MF has an empty file_path, which
        resolves to the data directory itself."""
        from backend.app.services.archive import extract_printable_objects_from_archive

        assert extract_printable_objects_from_archive(tmp_path) == ({}, None)

    def test_objects_come_back_with_their_positions(self, tmp_path):
        import zipfile

        from backend.app.services.archive import extract_printable_objects_from_archive

        slice_info = """<?xml version="1.0" encoding="UTF-8"?>
        <config>
          <plate>
            <metadata key="index" value="1"/>
            <object identify_id="101" name="left" skipped="false"/>
            <object identify_id="102" name="right" skipped="false"/>
          </plate>
        </config>"""
        path = tmp_path / "job.3mf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Metadata/slice_info.config", slice_info)

        objects, _bbox = extract_printable_objects_from_archive(path, plate_number=1)

        assert sorted(objects) == [101, 102]
