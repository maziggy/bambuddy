"""Deleting users, archives and library files leaves no ids pointing at them on SQLite.

SQLite runs with foreign keys off, so the ON DELETE SET NULL that PostgreSQL
applies to print_log_entries.created_by_id, print_log_entries.archive_id and
print_archives.library_file_id never fired: the print log kept a deleted user's
id (so the entries were missing from "no user"), and archives kept the id of a
deleted library file, which SQLite hands to the next file created.
"""

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.user import User


async def _user(db_session, name="leaver") -> User:
    user = User(username=name, password_hash="x", role="user")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _log_entry(db_session, **fields) -> PrintLogEntry:
    entry = PrintLogEntry(status="completed", print_name="Benchy", started_at=datetime.now(timezone.utc), **fields)
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)
    return entry


async def _reload(db_session, model, row_id):
    statement = select(model).where(model.id == row_id).execution_options(populate_existing=True)
    return (await db_session.execute(statement)).scalar_one_or_none()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deleting_a_user_clears_their_print_log_entries(async_client: AsyncClient, db_session):
    user = await _user(db_session)
    entry = await _log_entry(db_session, created_by_id=user.id)

    response = await async_client.delete(f"/api/v1/users/{user.id}")

    assert response.status_code == 204, response.text
    assert (await _reload(db_session, PrintLogEntry, entry.id)).created_by_id is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deleting_a_user_with_their_items_clears_the_log_entries_of_their_archives(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    user = await _user(db_session)
    printer = await printer_factory()
    archive = await archive_factory(printer.id, created_by_id=user.id, with_run=False)
    entry = await _log_entry(db_session, archive_id=archive.id, created_by_id=user.id)

    response = await async_client.delete(f"/api/v1/users/{user.id}?delete_items=true")

    assert response.status_code == 204, response.text
    assert await _reload(db_session, PrintArchive, archive.id) is None
    reloaded = await _reload(db_session, PrintLogEntry, entry.id)
    assert reloaded.archive_id is None
    assert reloaded.created_by_id is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deleting_a_user_with_their_items_clears_archives_of_their_library_files(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    user = await _user(db_session)
    library_file = LibraryFile(
        filename="part.3mf", file_path="library/files/part.3mf", file_type="3mf", file_size=1, created_by_id=user.id
    )
    db_session.add(library_file)
    await db_session.commit()
    printer = await printer_factory()
    archive = await archive_factory(printer.id, library_file_id=library_file.id, with_run=False)

    response = await async_client.delete(f"/api/v1/users/{user.id}?delete_items=true")

    assert response.status_code == 204, response.text
    assert (await _reload(db_session, PrintArchive, archive.id)).library_file_id is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deleting_a_printer_with_its_archives_clears_their_log_entries(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id, with_run=False)
    entry = await _log_entry(db_session, archive_id=archive.id, thumbnail_path="archive/1/thumb.png")

    response = await async_client.delete(f"/api/v1/printers/{printer.id}?delete_archives=true")

    assert response.status_code == 200, response.text
    assert await _reload(db_session, PrintArchive, archive.id) is None
    reloaded = await _reload(db_session, PrintLogEntry, entry.id)
    assert reloaded.archive_id is None
    # The archive files stay on disk here (unlike a single archive delete), so
    # the print log keeps its picture.
    assert reloaded.thumbnail_path == "archive/1/thumb.png"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deleting_a_library_file_clears_the_archives_printed_from_it(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    library_file = LibraryFile(
        filename="ext.3mf", file_path="/mnt/share/ext.3mf", file_type="3mf", file_size=1, is_external=True
    )
    db_session.add(library_file)
    await db_session.commit()
    printer = await printer_factory()
    archive = await archive_factory(printer.id, library_file_id=library_file.id, with_run=False)

    response = await async_client.delete(f"/api/v1/library/files/{library_file.id}")

    assert response.status_code == 200, response.text
    assert await _reload(db_session, LibraryFile, library_file.id) is None
    assert (await _reload(db_session, PrintArchive, archive.id)).library_file_id is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_any_library_file_delete_clears_the_archives_printed_from_it(
    db_session, printer_factory, archive_factory
):
    """Not only the routes: any delete of the row applies the rule (core/sqlite_fk hooks)."""
    library_file = LibraryFile(filename="a.3mf", file_path="library/files/a.3mf", file_type="3mf", file_size=1)
    db_session.add(library_file)
    await db_session.commit()
    printer = await printer_factory()
    archive = await archive_factory(printer.id, library_file_id=library_file.id, with_run=False)

    await db_session.delete(library_file)
    await db_session.commit()

    assert (await _reload(db_session, PrintArchive, archive.id)).library_file_id is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deleting_a_user_with_their_items_releases_other_users_queue_jobs(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """Another user's job on the deleted user's library file or archive must not
    keep its id: SQLite gives the highest freed id to the next file or archive,
    and the job would print that one."""
    from backend.app.models.print_queue import PrintQueueItem

    owner = await _user(db_session, "owner")
    other = await _user(db_session, "other")
    library_file = LibraryFile(
        filename="part.3mf", file_path="library/files/part.3mf", file_type="3mf", file_size=1, created_by_id=owner.id
    )
    db_session.add(library_file)
    await db_session.commit()
    printer = await printer_factory()
    archive = await archive_factory(printer.id, created_by_id=owner.id, with_run=False)
    on_file = PrintQueueItem(printer_id=printer.id, library_file_id=library_file.id, position=1, created_by_id=other.id)
    on_archive = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, position=2, created_by_id=other.id)
    db_session.add_all([on_file, on_archive])
    await db_session.commit()
    on_file_id, on_archive_id = on_file.id, on_archive.id

    response = await async_client.delete(f"/api/v1/users/{owner.id}?delete_items=true")

    assert response.status_code == 204, response.text
    kept = await _reload(db_session, PrintQueueItem, on_file_id)
    assert kept.library_file_id is None
    assert kept.status == "cancelled"
    # ON DELETE CASCADE on print_queue.archive_id, as on PostgreSQL.
    assert await _reload(db_session, PrintQueueItem, on_archive_id) is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_rescan_that_drops_a_vanished_external_file_clears_its_archives(
    async_client: AsyncClient, db_session, printer_factory, archive_factory, tmp_path, monkeypatch
):
    from backend.app.core.config import settings as app_settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(app_settings, "base_dir", data_dir)
    monkeypatch.setattr(app_settings, "archive_dir", data_dir / "archive")
    share = tmp_path / "share"
    share.mkdir()
    monkeypatch.setenv("BAMBUDDY_EXTERNAL_ROOTS", str(share))
    (share / "bracket.stl").write_bytes(b"fakestl")

    folder = await async_client.post(
        "/api/v1/library/folders/external",
        json={"name": "Share", "external_path": str(share), "readonly": True, "show_hidden": False},
    )
    assert folder.status_code == 200, folder.text
    folder_id = folder.json()["id"]
    assert (await async_client.post(f"/api/v1/library/folders/{folder_id}/scan")).json()["added"] == 1
    file_id = (await async_client.get(f"/api/v1/library/files?folder_id={folder_id}")).json()[0]["id"]
    printer = await printer_factory()
    archive = await archive_factory(printer.id, library_file_id=file_id, with_run=False)

    (share / "bracket.stl").unlink()
    rescan = await async_client.post(f"/api/v1/library/folders/{folder_id}/scan")

    assert rescan.json()["removed"] == 1
    assert (await _reload(db_session, PrintArchive, archive.id)).library_file_id is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deleting_a_user_with_their_items_applies_the_rules_to_their_jobs_and_batches(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """Their queue jobs and batches go with a bulk delete; what points at those
    goes too (a job's model variants) or loses the link (another user's job in
    their batch), as on PostgreSQL."""
    from backend.app.models.print_batch import PrintBatch
    from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant

    owner = await _user(db_session, "owner")
    other = await _user(db_session, "other")
    library_file = LibraryFile(filename="v.3mf", file_path="library/files/v.3mf", file_type="3mf", file_size=1)
    batch = PrintBatch(name="Order", created_by_id=owner.id)
    db_session.add_all([library_file, batch])
    await db_session.commit()
    printer = await printer_factory()
    archive = await archive_factory(printer.id, with_run=False)
    owned = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, position=1, created_by_id=owner.id)
    in_batch = PrintQueueItem(
        printer_id=printer.id, archive_id=archive.id, position=2, created_by_id=other.id, batch_id=batch.id
    )
    db_session.add_all([owned, in_batch])
    await db_session.commit()
    variant = PrintQueueVariant(queue_item_id=owned.id, library_file_id=library_file.id, target_model="X1C")
    db_session.add(variant)
    await db_session.commit()
    variant_id, in_batch_id = variant.id, in_batch.id

    response = await async_client.delete(f"/api/v1/users/{owner.id}?delete_items=true")

    assert response.status_code == 204, response.text
    assert await _reload(db_session, PrintQueueVariant, variant_id) is None
    assert (await _reload(db_session, PrintQueueItem, in_batch_id)).batch_id is None


async def _printing_reprint(db_session, printer_factory, archive_factory):
    """An archive made on printer A, being reprinted right now on printer B."""
    from backend.app.models.print_queue import PrintQueueItem

    printer_a = await printer_factory(name="A", serial_number="SA")
    printer_b = await printer_factory(name="B", serial_number="SB")
    archive = await archive_factory(printer_a.id, with_run=False)
    item = PrintQueueItem(printer_id=printer_b.id, archive_id=archive.id, position=1, status="printing")
    db_session.add(item)
    await db_session.commit()
    return printer_a.id, archive, item.id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deleting_a_printer_with_its_archives_waits_for_a_running_reprint(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """Like DELETE /archives/{id}: an archive whose queue item is printing is
    not deleted from under the dispatcher."""
    from backend.app.models.print_queue import PrintQueueItem

    printer_a_id, archive, item_id = await _printing_reprint(db_session, printer_factory, archive_factory)

    response = await async_client.delete(f"/api/v1/printers/{printer_a_id}?delete_archives=true")

    assert response.status_code == 409, response.text
    assert "printing" in response.json()["detail"]
    assert await _reload(db_session, PrintArchive, archive.id) is not None
    assert await _reload(db_session, PrintQueueItem, item_id) is not None


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("purge_stats", [False, True])
async def test_the_archive_purge_skips_an_archive_that_is_being_printed(
    async_client: AsyncClient, db_session, printer_factory, archive_factory, purge_stats
):
    from datetime import datetime, timedelta, timezone

    from backend.app.models.print_queue import PrintQueueItem

    _, archive, item_id = await _printing_reprint(db_session, printer_factory, archive_factory)
    archive.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/archives/purge", json={"older_than_days": 365, "purge_stats": purge_stats}
    )

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 0
    reloaded = await _reload(db_session, PrintArchive, archive.id)
    assert reloaded is not None and reloaded.deleted_at is None
    assert await _reload(db_session, PrintQueueItem, item_id) is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deleting_a_printer_unassigns_its_spools(async_client: AsyncClient, db_session, printer_factory):
    """SA-1: the spools of a deleted printer stayed "assigned" to it on SQLite."""
    from backend.app.models.spool import Spool
    from backend.app.models.spool_assignment import SpoolAssignment

    printer = await printer_factory()
    kept_printer = await printer_factory(name="Kept")
    spool = Spool(material="PLA", label_weight=1000, weight_used=0)
    db_session.add(spool)
    await db_session.flush()
    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=0))
    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=kept_printer.id, ams_id=0, tray_id=1))
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/printers/{printer.id}")

    assert response.status_code == 200, response.text
    rows = (await db_session.execute(select(SpoolAssignment).execution_options(populate_existing=True))).scalars()
    assert [row.printer_id for row in rows] == [kept_printer.id]
    assert await _reload(db_session, Spool, spool.id) is not None
