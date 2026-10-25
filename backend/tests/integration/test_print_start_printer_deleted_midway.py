"""A printer deleted while its print-start handler downloads the 3MF.

Deleting a printer cuts its MQTT link, but an ``on_print_start`` already in its
FTP phase (seconds to minutes) carried on: it wrote a "printing" archive that no
completion would ever end, registered it in ``_active_prints``, started a layer
timelapse and re-added the 3MF cache entry ``forget_printer`` had cleared. It
now checks the printer still exists once the downloads are done, and forgets it
again if not.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, update

from backend.app.main import _active_prints
from backend.app.models.archive import PrintArchive
from backend.app.models.printer import Printer
from backend.tests.integration.test_external_print_confirmation_1898 import (
    _clear_print_state,  # noqa: F401 - autouse fixture
    _drive_print_start,
    _StubArchiveService,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _download_deleting(db_session, printer_id: int, *, ok: bool):
    """The FTP download, during which the printer is deleted."""

    async def download(*_args, **_kwargs):
        await db_session.execute(delete(Printer).where(Printer.id == printer_id))
        await db_session.commit()
        return ok

    return patch("backend.app.main.download_file_async", new=AsyncMock(side_effect=download))


class _ArchiveService(_StubArchiveService):
    """The #1898 stub, recording its writes, with the real delete (the check
    after the archive's commit deletes through it)."""

    written: list[int] = []

    async def archive_print(self, **kwargs):
        _ArchiveService.written.append(kwargs.get("printer_id"))
        return await super().archive_print(**kwargs)

    async def delete_archive(self, archive_id: int) -> bool:
        from backend.app.services.archive import ArchiveService

        return await ArchiveService(self.db).delete_archive(archive_id)


async def _archives(db_session, printer_id: int) -> list[PrintArchive]:
    db_session.expire_all()
    return list((await db_session.scalars(select(PrintArchive).where(PrintArchive.printer_id == printer_id))).all())


@pytest.mark.parametrize("download_ok", [False, True], ids=["no-3mf fallback", "downloaded 3mf"])
async def test_a_printer_deleted_during_the_download_gets_nothing(
    test_engine, db_session, printer_factory, download_ok
):
    printer = await printer_factory()
    printer_id = printer.id
    _ArchiveService.written = []
    ask_outcome = AsyncMock(return_value=False)  # builds the fallback row

    with patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget:
        mocks = await _drive_print_start(
            test_engine,
            printer,
            download_ok=download_ok,
            extra_patches=(
                _download_deleting(db_session, printer_id, ok=download_ok),
                patch("backend.app.main.ArchiveService", _ArchiveService),
                patch("backend.app.main._ask_outcome_for_external_print", new=ask_outcome),
            ),
        )

    # Checked before writing: no archive was even started (the check after the
    # commit would remove one, but only after it was written).
    assert _ArchiveService.written == []
    ask_outcome.assert_not_awaited()
    assert await _archives(db_session, printer_id) == []
    assert not [key for key in _active_prints if key[0] == printer_id]
    mocks["_maybe_start_layer_timelapse"].assert_not_called()
    # What the download wrote under the id (the 3MF cache entry) goes again.
    forget.assert_awaited_once_with(printer_id)


@pytest.mark.parametrize("download_ok", [False, True], ids=["no-3mf fallback", "downloaded 3mf"])
async def test_a_printer_deleted_while_its_archive_is_written_gets_nothing(
    test_engine, db_session, printer_factory, download_ok
):
    """The delete commits between the check and the archive's commit (the route
    sweeps the printer's archives before it commits): the row written after
    that sweep is removed again."""
    printer = await printer_factory()
    printer_id = printer.id

    async def delete_printer():
        await db_session.execute(delete(Printer).where(Printer.id == printer_id))
        await db_session.commit()

    class _DeletingArchiveService(_ArchiveService):
        async def archive_print(self, **kwargs):
            await delete_printer()
            return await super().archive_print(**kwargs)

    async def ask_outcome(*_args, **_kwargs):  # runs while the fallback row is built
        await delete_printer()
        return False

    with patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget:
        mocks = await _drive_print_start(
            test_engine,
            printer,
            download_ok=download_ok,
            extra_patches=(
                patch("backend.app.main.ArchiveService", _DeletingArchiveService),
                patch("backend.app.main._ask_outcome_for_external_print", new=AsyncMock(side_effect=ask_outcome)),
            ),
        )

    assert await _archives(db_session, printer_id) == []
    assert not [key for key in _active_prints if key[0] == printer_id]
    mocks["_maybe_start_layer_timelapse"].assert_not_called()
    forget.assert_awaited_once_with(printer_id)


async def test_an_archive_kept_by_the_delete_is_left_alone(test_engine, db_session, printer_factory):
    """Deleted with "keep archives" right after the archive's commit: the route
    detached it (printer_id NULL) as the user asked, so it stays. Nothing is
    registered for it."""
    printer = await printer_factory()
    printer_id = printer.id

    async def delete_keeping_archives(*_args, **_kwargs):  # runs after archive_print's commit
        await db_session.execute(
            update(PrintArchive).where(PrintArchive.printer_id == printer_id).values(printer_id=None)
        )
        await db_session.execute(delete(Printer).where(Printer.id == printer_id))
        await db_session.commit()
        return False

    with patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget:
        mocks = await _drive_print_start(
            test_engine,
            printer,
            download_ok=True,
            extra_patches=(
                patch("backend.app.main.ArchiveService", _ArchiveService),
                patch(
                    "backend.app.main._ask_outcome_for_external_print",
                    new=AsyncMock(side_effect=delete_keeping_archives),
                ),
            ),
        )

    db_session.expire_all()
    kept = (await db_session.scalars(select(PrintArchive))).all()
    assert len(kept) == 1 and kept[0].printer_id is None
    assert not [key for key in _active_prints if key[0] == printer_id]
    mocks["_maybe_start_layer_timelapse"].assert_not_called()
    forget.assert_awaited_once_with(printer_id)


async def test_a_printer_still_there_gets_its_archive(test_engine, db_session, printer_factory):
    printer = await printer_factory()

    with patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget:
        await _drive_print_start(test_engine, printer, download_ok=False)

    assert len(await _archives(db_session, printer.id)) == 1
    forget.assert_not_awaited()


async def test_a_queue_print_whose_printer_was_deleted_during_the_upload_credits_nobody(
    db_session, test_engine, monkeypatch
):
    """The dispatch would write the owner under the deleted printer's id after
    forget_printer cleared it."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.models.user import User
    from backend.app.services import print_scheduler as scheduler_module
    from backend.app.services.print_scheduler import PrintScheduler

    owner = User(username="olga", password_hash="x", role="user")
    printer = Printer(name="P", serial_number="DELMID1", ip_address="192.0.2.11", access_code="x")
    db_session.add_all([owner, printer])
    await db_session.commit()
    item = PrintQueueItem(printer_id=printer.id, position=1, created_by_id=owner.id)
    db_session.add(item)
    await db_session.commit()

    other = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with other() as admin_db:  # meanwhile, through the API (SQLite keeps the item)
        await admin_db.execute(delete(Printer).where(Printer.id == printer.id))
        await admin_db.commit()

    credited: list = []
    monkeypatch.setattr(scheduler_module.printer_manager, "set_current_print_user", lambda *args: credited.append(args))
    await PrintScheduler()._propagate_owner_to_printer_manager(db_session, item)

    assert credited == []


async def _queued_archive(db_session, printer_id: int) -> int:
    """An archive Bambuddy dispatched to the printer, waiting for its start."""
    from datetime import datetime, timezone

    from backend.app.main import register_expected_print

    archive = PrintArchive(
        printer_id=printer_id,
        filename="Bracket_plate_1.3mf",
        file_path="archives/test/Bracket_plate_1.3mf",
        file_size=1,
        print_name="Bracket_plate_1",
        status="completed",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(archive)
    await db_session.commit()
    register_expected_print(printer_id, "Bracket_plate_1.3mf", archive.id)
    return archive.id


async def _delete_keeping_archives(db_session, printer_id: int) -> None:
    await db_session.execute(update(PrintArchive).where(PrintArchive.printer_id == printer_id).values(printer_id=None))
    await db_session.execute(delete(Printer).where(Printer.id == printer_id))
    await db_session.commit()


async def _archive(db_session, archive_id: int) -> PrintArchive:
    db_session.expire_all()
    return await db_session.get(PrintArchive, archive_id)


async def test_a_queued_print_of_a_deleted_printer_is_not_adopted(test_engine, db_session, printer_factory):
    """Deleted with "keep archives" before the start was handled (the plate
    check waits seconds), but before the route forgot the expected print."""
    from backend.app import main

    printer = await printer_factory()
    printer_id = printer.id
    archive_id = await _queued_archive(db_session, printer_id)
    real = main._printer_gone
    deleted = []

    async def deleted_before_the_check(db, pid):
        if not deleted:  # after the handler loaded the printer, before the branch
            deleted.append(pid)
            await _delete_keeping_archives(db_session, printer_id)
        return await real(db, pid)

    with (
        patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget,
        patch("backend.app.main._printer_gone", new=deleted_before_the_check),
    ):
        mocks = await _drive_print_start(test_engine, printer, download_ok=False)

    archive = await _archive(db_session, archive_id)
    assert archive.printer_id is None and archive.status == "completed"
    assert not [key for key in _active_prints if key[0] == printer_id]
    mocks["_maybe_start_layer_timelapse"].assert_not_called()
    forget.assert_awaited_once_with(printer_id)


async def test_a_queued_print_whose_printer_goes_while_it_is_adopted_is_left_as_kept(
    test_engine, db_session, printer_factory, tmp_path, monkeypatch
):
    """The route's "keep archives" update lands between the check and the
    commit (the route's transaction lasts seconds), which then adopted the
    archive anyway: pointed back at the deleted printer, "printing" forever,
    its verdict cleared and its timelapse video deleted from disk."""
    from backend.app import main

    monkeypatch.setattr(main.app_settings, "base_dir", tmp_path)
    video = tmp_path / "archives" / "run.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"mp4")

    printer = await printer_factory()
    printer_id = printer.id
    archive_id = await _queued_archive(db_session, printer_id)
    kept = await db_session.get(PrintArchive, archive_id)
    kept.timelapse_path = "archives/run.mp4"  # same (unrecorded) plate: a reprint would delete it
    kept.confirm_requested = True
    kept.user_verdict = "good"
    await db_session.commit()
    real = main._printer_gone
    calls = []

    async def gone_after_the_check(db, pid):
        calls.append(pid)
        if len(calls) == 1:
            await _delete_keeping_archives(db_session, printer_id)
            return False  # read before the delete committed
        return await real(db, pid)

    with (
        patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget,
        patch("backend.app.main._printer_gone", new=gone_after_the_check),
    ):
        mocks = await _drive_print_start(test_engine, printer, download_ok=False)

    archive = await _archive(db_session, archive_id)
    assert archive.printer_id is None
    assert archive.status == "completed"
    assert archive.user_verdict == "good"
    assert archive.timelapse_path == "archives/run.mp4"
    assert video.exists()
    assert not [key for key in _active_prints if key[0] == printer_id]
    mocks["_maybe_start_layer_timelapse"].assert_not_called()
    forget.assert_awaited_once_with(printer_id)


async def test_another_printers_archive_adopted_for_a_deleted_printer_stays_that_printers(
    test_engine, db_session, printer_factory
):
    """A reprint or queue item can reuse printer A's archive on printer B. B
    deleted while it is adopted: the route never touched the row (it was A's),
    so it goes back to A, not to no printer."""
    from backend.app import main

    owner = await printer_factory()
    target = await printer_factory()
    owner_id, target_id = owner.id, target.id
    archive_id = await _queued_archive(db_session, owner_id)
    main._expected_prints.clear()
    main.register_expected_print(target_id, "Bracket_plate_1.3mf", archive_id)
    real = main._printer_gone
    calls = []

    async def gone_after_the_check(db, pid):
        calls.append(pid)
        if len(calls) == 1:
            await db_session.execute(delete(Printer).where(Printer.id == target_id))
            await db_session.commit()
            return False  # read before the delete committed
        return await real(db, pid)

    with (
        patch("backend.app.main.forget_printer", new_callable=AsyncMock),
        patch("backend.app.main._printer_gone", new=gone_after_the_check),
    ):
        await _drive_print_start(test_engine, target, download_ok=False)

    archive = await _archive(db_session, archive_id)
    assert archive.printer_id == owner_id
    assert archive.status == "completed"


@pytest.mark.sqlite_only  # deletes the printer with its archives still attached
@pytest.mark.parametrize("branch", ["fallback", "downloaded", "queued"])
async def test_a_printer_deleted_during_the_last_steps_is_forgotten_again(
    test_engine, db_session, printer_factory, branch
):
    """After the last check the handler still waits on the smart plug, the
    notifications and the timelapse baseline's FTP listing; the route may
    delete the printer and forget it meanwhile, and the handler then writes
    again (the baseline). It forgets the printer once more at the end."""
    printer = await printer_factory()
    printer_id = printer.id
    if branch == "queued":
        await _queued_archive(db_session, printer_id)

    async def deleted_during_the_listing(*_args, **_kwargs):
        await db_session.execute(delete(Printer).where(Printer.id == printer_id))
        await db_session.commit()

    with patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget:
        await _drive_print_start(
            test_engine,
            printer,
            download_ok=branch == "downloaded",
            extra_patches=(
                patch("backend.app.main.ArchiveService", _ArchiveService),
                patch(
                    "backend.app.main._capture_timelapse_baseline_at_start",
                    new=AsyncMock(side_effect=deleted_during_the_listing),
                ),
            ),
        )

    forget.assert_awaited_once_with(printer_id)


async def test_the_last_check_works_on_a_session_the_late_steps_broke(test_engine, db_session, printer_factory):
    """The smart plug's energy write hits the archive the delete removed and
    leaves the handler's session needing a rollback: the check must not use it."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app import main

    printer = await printer_factory()
    printer_id = printer.id
    archive_id = await _queued_archive(db_session, printer_id)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as handler_db:
        archive = await handler_db.get(PrintArchive, archive_id)
        await db_session.execute(delete(PrintArchive).where(PrintArchive.id == archive_id))
        await db_session.execute(delete(Printer).where(Printer.id == printer_id))
        await db_session.commit()
        archive.energy_start_kwh = 1.0
        with pytest.raises(Exception):  # noqa: B017 - StaleDataError, as _record_energy_start swallows it
            await handler_db.commit()

        with (
            patch("backend.app.main.async_session", maker),
            patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget,
        ):
            await main._forget_if_printer_gone(printer_id)

    forget.assert_awaited_once_with(printer_id)


async def test_the_last_check_never_raises(printer_factory):
    """It runs inside the fallback branch's try: an error there would report the
    archive as failed and send a second start notification."""
    from backend.app import main

    def broken_session():
        raise RuntimeError("database unavailable")

    with (
        patch("backend.app.main.async_session", broken_session),
        patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget,
    ):
        await main._forget_if_printer_gone(1)

    forget.assert_not_awaited()


async def test_a_refused_adoption_is_a_deleted_printer_not_an_error(test_engine, db_session, printer_factory):
    """PostgreSQL: an adoption rewriting printer_id (a VP-queue or another
    printer's archive) waits on the delete route's lock on the printer, then
    the printer foreign key refuses it (measured). That is the printer being
    deleted, not a failure of the print start."""
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app import main

    printer = await printer_factory()
    printer_id = printer.id
    archive_id = await _queued_archive(db_session, printer_id)
    real_gone = main._printer_gone
    real_commit = AsyncSession.commit
    refuse = []

    async def deleted_with_its_archives_kept(db, pid):
        if not refuse:
            await _delete_keeping_archives(db_session, printer_id)
            refuse.append(True)  # the adoption's commit, next
            return False  # read before the delete committed
        return await real_gone(db, pid)

    async def commit(self):
        if refuse and refuse[0]:
            refuse[0] = False
            raise IntegrityError("UPDATE print_archives", {}, Exception("violates foreign key constraint"))
        return await real_commit(self)

    with (
        patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget,
        patch("backend.app.main._printer_gone", new=deleted_with_its_archives_kept),
        patch.object(AsyncSession, "commit", commit),
    ):
        mocks = await _drive_print_start(test_engine, printer, download_ok=False)

    archive = await _archive(db_session, archive_id)
    assert archive.printer_id is None and archive.status == "completed"
    assert not [key for key in _active_prints if key[0] == printer_id]
    mocks["_maybe_start_layer_timelapse"].assert_not_called()
    forget.assert_awaited_once_with(printer_id)


async def test_a_refused_adoption_of_a_printer_still_there_is_still_an_error(test_engine, db_session, printer_factory):
    """Only the printer's delete explains a refused adoption; anything else
    is a real error and must not be swallowed as one."""
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app import main

    printer = await printer_factory()
    await _queued_archive(db_session, printer.id)
    real_gone = main._printer_gone
    real_commit = AsyncSession.commit
    refuse = []

    async def checked(db, pid):
        refuse.append(True)  # the adoption's commit, next
        return await real_gone(db, pid)

    async def commit(self):
        if len(refuse) == 1:
            refuse.append(False)
            raise IntegrityError("UPDATE print_archives", {}, Exception("some other constraint"))
        return await real_commit(self)

    with (
        patch("backend.app.main.forget_printer", new_callable=AsyncMock),
        patch("backend.app.main._printer_gone", new=checked),
        patch.object(AsyncSession, "commit", commit),
        pytest.raises(IntegrityError),
    ):
        await _drive_print_start(test_engine, printer, download_ok=False)


@pytest.mark.parametrize("keep", [False, True], ids=["delete archives", "keep archives"])
async def test_the_delete_locks_the_printer_from_its_archive_sweep_to_its_commit(
    async_client, db_session, printer_factory, keep
):
    """PostgreSQL (measured): a print start adopting a VP-queue or another
    printer's archive after the sweep passed the foreign key, and the delete's
    ORM cascade then deleted that archive, also with "keep archives". Locked,
    the adoption waits and is then refused. (SQLite drops FOR UPDATE; its
    single writer serialises this anyway, so the statement is what is checked.)"""
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql import Select

    printer = await printer_factory()
    real_execute = AsyncSession.execute
    statements: list = []

    async def recording(self, statement, *args, **kwargs):
        statements.append(statement)
        return await real_execute(self, statement, *args, **kwargs)

    with patch.object(AsyncSession, "execute", recording):
        response = await async_client.delete(
            f"/api/v1/printers/{printer.id}", params={"delete_archives": str(not keep).lower()}
        )
    assert response.status_code == 200, response.text

    def is_lock(s):
        return isinstance(s, Select) and s._for_update_arg is not None and "printers" in str(s)

    def is_sweep(s):
        return not isinstance(s, Select) and "print_archives" in str(s)

    locks = [i for i, s in enumerate(statements) if is_lock(s)]
    sweeps = [i for i, s in enumerate(statements) if is_sweep(s)]
    assert locks and sweeps and locks[0] < sweeps[0]


def _refusing_first_commit_after_the_check(db_session, printer_id, main_module):
    """The printer deleted right after the handler's check; the next commit is
    refused by its foreign key (PostgreSQL, the delete holding the printer)."""
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    real_gone = main_module._printer_gone
    real_commit = AsyncSession.commit
    state: list = []

    async def gone(db, pid):
        if not state:
            await db_session.execute(delete(Printer).where(Printer.id == printer_id))
            await db_session.commit()
            state.append(True)  # refuse the next commit
            return False  # read before the delete committed
        return await real_gone(db, pid)

    async def commit(self):
        if state and state[0]:
            state[0] = False
            raise IntegrityError("INSERT INTO print_archives", {}, Exception("violates foreign key constraint"))
        return await real_commit(self)

    return (
        patch("backend.app.main._printer_gone", new=gone),
        patch.object(AsyncSession, "commit", commit),
    )


@pytest.mark.parametrize("download_ok", [False, True], ids=["no-3mf fallback", "downloaded 3mf"])
async def test_a_refused_new_archive_is_a_deleted_printer_not_an_error(
    test_engine, db_session, printer_factory, download_ok
):
    """With the delete locking the printer, an archive written meanwhile waits
    and is refused: no error escaping the print start, no "Failed to create
    fallback archive", no start notification for the deleted printer."""
    from backend.app import main

    printer = await printer_factory()
    printer_id = printer.id

    with patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget:
        mocks = await _drive_print_start(
            test_engine,
            printer,
            download_ok=download_ok,
            extra_patches=(
                patch("backend.app.main.ArchiveService", _ArchiveService),
                *_refusing_first_commit_after_the_check(db_session, printer_id, main),
            ),
        )

    assert await _archives(db_session, printer_id) == []
    assert not [key for key in _active_prints if key[0] == printer_id]
    mocks["_maybe_start_layer_timelapse"].assert_not_called()
    mocks["_send_print_start_notification"].assert_not_awaited()
    forget.assert_awaited_once_with(printer_id)


async def test_an_archive_whose_commit_fails_leaves_no_folder(db_session, printer_factory, tmp_path, monkeypatch):
    """archive_print copies the 3MF into the new archive's folder before the
    row is written; a refused commit left that folder behind."""
    import zipfile

    from sqlalchemy.exc import IntegrityError

    from backend.app.core.config import settings
    from backend.app.services.archive import ArchiveService

    monkeypatch.setattr(settings, "base_dir", tmp_path)  # archive paths are stored relative to it
    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")
    source = tmp_path / "Cube.3mf"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("Metadata/slice_info.config", "<config><plate></plate></config>")
    printer = await printer_factory()

    async def refused():
        raise IntegrityError("INSERT INTO print_archives", {}, Exception("violates foreign key constraint"))

    monkeypatch.setattr(db_session, "commit", refused)
    with pytest.raises(IntegrityError):
        await ArchiveService(db_session).archive_print(printer_id=printer.id, source_file=source)

    assert [p for p in (tmp_path / "archive").rglob("*") if p.is_file()] == []


async def test_a_failed_archive_leaves_a_folder_it_did_not_create(db_session, printer_factory, tmp_path, monkeypatch):
    """Same name, same second: the folder was another archive's."""
    import zipfile
    from datetime import datetime

    from sqlalchemy.exc import IntegrityError

    from backend.app.core.config import settings
    from backend.app.services import archive as archive_module
    from backend.app.services.archive import ArchiveService

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 2, 3, 4, 5, tzinfo=tz)

    monkeypatch.setattr(archive_module, "datetime", _Frozen)
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")
    source = tmp_path / "Cube.3mf"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("Metadata/slice_info.config", "<config><plate></plate></config>")
    printer = await printer_factory()
    other = tmp_path / "archive" / str(printer.id) / "20260102_030405_Cube" / "other.3mf"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"another archive's file")

    async def refused():
        raise IntegrityError("INSERT INTO print_archives", {}, Exception("violates foreign key constraint"))

    monkeypatch.setattr(db_session, "commit", refused)
    with pytest.raises(IntegrityError):
        await ArchiveService(db_session).archive_print(printer_id=printer.id, source_file=source)

    assert other.exists()


async def test_a_reprint_whose_archive_the_delete_removed_is_a_deleted_printer_not_an_error(
    test_engine, db_session, printer_factory
):
    """ "Delete archives" removes the archive under the adoption (a reprint of
    the printer's own archive; a queue print is refused earlier by the route's
    409): the adoption's update matches no row (StaleDataError, not an
    IntegrityError)."""
    from backend.app import main

    printer = await printer_factory()
    printer_id = printer.id
    archive_id = await _queued_archive(db_session, printer_id)
    real = main._printer_gone
    calls = []

    async def deleted_with_its_archives(db, pid):
        calls.append(pid)
        if len(calls) == 1:
            await db_session.execute(delete(PrintArchive).where(PrintArchive.printer_id == printer_id))
            await db_session.execute(delete(Printer).where(Printer.id == printer_id))
            await db_session.commit()
            return False  # read before the delete committed
        return await real(db, pid)

    with (
        patch("backend.app.main.forget_printer", new_callable=AsyncMock) as forget,
        patch("backend.app.main._printer_gone", new=deleted_with_its_archives),
    ):
        mocks = await _drive_print_start(test_engine, printer, download_ok=False)

    assert await _archive(db_session, archive_id) is None
    assert not [key for key in _active_prints if key[0] == printer_id]
    mocks["_maybe_start_layer_timelapse"].assert_not_called()
    forget.assert_awaited_once_with(printer_id)


async def test_an_archive_whose_commit_may_have_landed_keeps_its_folder(
    db_session, printer_factory, tmp_path, monkeypatch
):
    """A connection lost during COMMIT may still have written the row: its
    files stay. Only a refusal (IntegrityError) proves the row was not written."""
    import zipfile

    from sqlalchemy.exc import OperationalError

    from backend.app.core.config import settings
    from backend.app.services.archive import ArchiveService

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")
    source = tmp_path / "Cube.3mf"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("Metadata/slice_info.config", "<config><plate></plate></config>")
    printer = await printer_factory()

    async def connection_lost():
        raise OperationalError("COMMIT", {}, Exception("connection was closed in the middle of operation"))

    monkeypatch.setattr(db_session, "commit", connection_lost)
    with pytest.raises(OperationalError):
        await ArchiveService(db_session).archive_print(printer_id=printer.id, source_file=source)

    assert [p.name for p in (tmp_path / "archive").rglob("*") if p.is_file()] == ["Cube.3mf"]
