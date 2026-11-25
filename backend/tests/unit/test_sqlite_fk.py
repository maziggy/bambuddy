"""core/sqlite_fk.py applies the selected ON DELETE rules that SQLite leaves out.

The app's SQLite database runs with PRAGMA foreign_keys off, so a delete left
every ON DELETE CASCADE / SET NULL undone: rows pointing at the deleted row kept
its id, and SQLite hands the highest freed id to the next row created.
"""

import pytest
from sqlalchemy import delete, event, select, text

from backend.app.core import sqlite_fk
from backend.app.core.database import load_all_models
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.notification import NotificationProvider
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant
from backend.app.models.printer import Printer

# The SQLite stand-in for foreign key rules PostgreSQL applies itself.
pytestmark = pytest.mark.sqlite_only


async def _archive_with_dependents(db):
    printer = Printer(name="P", serial_number="S1", ip_address="192.0.2.1", access_code="x")
    library_file = LibraryFile(filename="f.3mf", file_path="l/f.3mf", file_type="3mf", file_size=1)
    db.add_all([printer, library_file])
    await db.flush()
    archive = PrintArchive(
        printer_id=printer.id, filename="a.3mf", file_path="a/a.3mf", file_size=1, status="completed"
    )
    db.add(archive)
    await db.flush()
    item = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, position=1)
    db.add(item)
    await db.flush()
    variant = PrintQueueVariant(queue_item_id=item.id, library_file_id=library_file.id, target_model="X1C")
    run = PrintLogEntry(status="completed", print_name="a", archive_id=archive.id, queue_item_id=item.id)
    db.add_all([variant, run])
    await db.commit()
    return archive.id, item.id, variant.id, run.id


async def _exists(db, model, row_id) -> bool:
    return (await db.execute(select(model.id).where(model.id == row_id))).scalar_one_or_none() is not None


async def _run(db, run_id):
    statement = select(PrintLogEntry).where(PrintLogEntry.id == run_id).execution_options(populate_existing=True)
    return (await db.execute(statement)).scalar_one()


async def _assert_rules_applied(db, item_id, variant_id, run_id):
    assert not await _exists(db, PrintQueueItem, item_id)  # CASCADE
    assert not await _exists(db, PrintQueueVariant, variant_id)  # CASCADE of the CASCADE
    run = await _run(db, run_id)
    assert run.archive_id is None  # SET NULL
    assert run.queue_item_id is None  # SET NULL of the cascaded row


@pytest.mark.asyncio
async def test_an_orm_delete_applies_the_rules(db_session):
    archive_id, item_id, variant_id, run_id = await _archive_with_dependents(db_session)

    await db_session.delete(await db_session.get(PrintArchive, archive_id))
    await db_session.commit()

    await _assert_rules_applied(db_session, item_id, variant_id, run_id)


@pytest.mark.asyncio
async def test_a_bulk_delete_applies_the_rules(db_session):
    archive_id, item_id, variant_id, run_id = await _archive_with_dependents(db_session)

    await db_session.execute(delete(PrintArchive).where(PrintArchive.id == archive_id))
    await db_session.commit()

    await _assert_rules_applied(db_session, item_id, variant_id, run_id)


@pytest.mark.asyncio
async def test_a_bulk_delete_sees_changes_not_flushed_yet(db_session):
    """A queue item moved off the archive in the same transaction must survive."""
    archive_id, item_id, _, _ = await _archive_with_dependents(db_session)
    other = PrintArchive(filename="b.3mf", file_path="a/b.3mf", file_size=1, status="completed")
    db_session.add(other)
    await db_session.commit()
    item = await db_session.get(PrintQueueItem, item_id)
    item.archive_id = other.id

    await db_session.execute(delete(PrintArchive).where(PrintArchive.id == archive_id))
    await db_session.commit()

    assert await _exists(db_session, PrintQueueItem, item_id)


@pytest.mark.asyncio
async def test_references_not_on_the_list_are_left_alone(db_session):
    """Not every model rule is right: deleting a printer must not widen a
    printer-scoped notification provider (SET NULL means all printers)."""
    printer = Printer(name="P", serial_number="S2", ip_address="192.0.2.2", access_code="x")
    db_session.add(printer)
    await db_session.flush()
    provider = NotificationProvider(name="n", provider_type="webhook", config="{}", printer_id=printer.id)
    db_session.add(provider)
    await db_session.commit()
    printer_id, provider_id = printer.id, provider.id

    await db_session.execute(delete(Printer).where(Printer.id == printer_id))
    await db_session.commit()

    scoped = (
        await db_session.execute(select(NotificationProvider.printer_id).where(NotificationProvider.id == provider_id))
    ).scalar_one()
    assert scoped == printer_id


@pytest.mark.asyncio
async def test_many_orm_deletes_in_one_flush_cost_one_statement_per_rule(db_session):
    """Applied once per flush, not once per row: a folder of thousands of files
    or a big purge must not run the rules per row while holding the write lock."""
    archives = [PrintArchive(filename=f"{i}.3mf", file_path=f"a/{i}.3mf", file_size=1, status="x") for i in range(50)]
    db_session.add_all(archives)
    await db_session.commit()
    rule_statements: list[str] = []
    sync_engine = db_session.bind.sync_engine

    def count(conn, cursor, statement, *args):
        head = statement.lstrip().upper()
        if head.startswith(("UPDATE", "DELETE")) and not head.startswith("DELETE FROM PRINT_ARCHIVES"):
            rule_statements.append(statement)

    event.listen(sync_engine, "before_cursor_execute", count)
    try:
        for archive in archives:
            await db_session.delete(archive)
        await db_session.commit()
    finally:
        event.remove(sync_engine, "before_cursor_execute", count)

    # The rules of print_archives and of the rows they cascade to, once each.
    assert len(rule_statements) < 30, len(rule_statements)


@pytest.mark.asyncio
async def test_a_rollback_drops_ids_collected_for_a_flush_that_failed(db_session):
    """Ids collected by a flush that then failed must not be applied by the next one."""
    await db_session.execute(select(1))  # an open transaction, as during a failed flush
    db_session.sync_session.info[sqlite_fk._PENDING] = {"print_archives": [1]}

    await db_session.rollback()

    assert sqlite_fk._PENDING not in db_session.sync_session.info


@pytest.mark.asyncio
async def test_nothing_to_do_on_postgresql(db_session, monkeypatch):
    """PostgreSQL applies the rules itself."""
    archive_id, item_id, _, run_id = await _archive_with_dependents(db_session)
    monkeypatch.setattr(sqlite_fk, "_applies", lambda connection: False)

    await db_session.execute(delete(PrintArchive).where(PrintArchive.id == archive_id))
    await db_session.commit()

    assert await _exists(db_session, PrintQueueItem, item_id)
    assert (await _run(db_session, run_id)).archive_id == archive_id


@pytest.mark.asyncio
async def test_the_startup_sweep_repairs_rows_whose_parent_is_already_gone(db_session):
    archive_id, item_id, variant_id, run_id = await _archive_with_dependents(db_session)
    # Deleted the old way (raw SQL bypasses the hooks), leaving every dependent behind.
    await db_session.execute(text("DELETE FROM print_archives WHERE id = :id"), {"id": archive_id})
    await db_session.commit()

    await sqlite_fk.sweep_orphans(await db_session.connection())
    await db_session.commit()

    await _assert_rules_applied(db_session, item_id, variant_id, run_id)


@pytest.mark.asyncio
async def test_the_sweep_leaves_valid_references_and_unlisted_ones_alone(db_session):
    archive_id, item_id, variant_id, run_id = await _archive_with_dependents(db_session)
    provider = NotificationProvider(name="n", provider_type="webhook", config="{}", printer_id=987654)
    db_session.add(provider)
    await db_session.commit()
    provider_id = provider.id

    await sqlite_fk.sweep_orphans(await db_session.connection())
    await db_session.commit()

    assert await _exists(db_session, PrintQueueItem, item_id)
    assert await _exists(db_session, PrintQueueVariant, variant_id)
    assert (await _run(db_session, run_id)).archive_id == archive_id
    stays = (
        await db_session.execute(select(NotificationProvider.printer_id).where(NotificationProvider.id == provider_id))
    ).scalar_one()
    assert stays == 987654


def test_every_listed_reference_is_a_model_foreign_key_with_a_rule():
    """A typo or a renamed column must not silently drop a rule."""
    metadata = load_all_models()
    with_rule = {
        (table.name, fk.parent.name)
        for table in metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.ondelete in ("CASCADE", "SET NULL")
    }
    assert sqlite_fk.APPLIED - with_rule == set()
    assert len(sqlite_fk._applied_foreign_keys()) == len(sqlite_fk.APPLIED)


def test_every_listed_set_null_column_can_hold_null():
    """A SET NULL rule on a NOT NULL column would make the delete itself fail."""
    not_nullable = {
        (table.name, fk.parent.name)
        for table in load_all_models().sorted_tables
        for fk in table.foreign_keys
        if (table.name, fk.parent.name) in sqlite_fk.APPLIED and fk.ondelete == "SET NULL" and not fk.parent.nullable
    }
    assert not_nullable == set()


@pytest.mark.asyncio
async def test_the_sweep_skips_a_set_null_its_column_still_refuses():
    """If a migration couldn't drop a NOT NULL, the sweep must not stop the app from starting."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            await conn.execute(
                text(
                    "CREATE TABLE wallet_transactions (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
                    "cost_center_id INTEGER, created_by_user_id INTEGER, print_archive_id INTEGER, print_queue_id INTEGER)"
                )
            )
            await conn.execute(text("INSERT INTO wallet_transactions (id, user_id) VALUES (1, 7)"))

            await sqlite_fk.sweep_orphans(conn)

            assert (await conn.execute(text("SELECT user_id FROM wallet_transactions"))).scalar_one() == 7
    finally:
        await engine.dispose()
