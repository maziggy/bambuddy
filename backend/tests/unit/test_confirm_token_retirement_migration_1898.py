"""Migration coverage for the one-tap token columns, index and prompt body (#1898).

``confirm_token_used_at`` is what turns a spent link into "already answered"
instead of a 404, and ``user_verdict_source`` is what the hint next to the
verdict badge reads — both have to reach an install that upgraded rather than
one created fresh from the models. So does the index on ``confirm_token``: the
route that reads it runs with no authentication, so without the index anyone
who can reach the host turns a stream of invented tokens into a stream of full
scans of print_archives.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.models.external_link  # noqa: F401 - required by a legacy ALTER in run_migrations
import backend.app.models.print_log  # noqa: F401 - required by a legacy ALTER in run_migrations
import backend.app.models.virtual_printer  # noqa: F401 - required by a legacy ALTER in run_migrations
from backend.app.core.database import Base, run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """run_migrations branches on the global dialect, not on the connection, so
    a dev config pointing at Postgres would run Postgres-only syntax against the
    SQLite engine below. Same fixture as test_billing_run_id_migration.py."""
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    # database.py imported is_sqlite at module load time — patch there too.
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


async def _archive_columns(conn) -> set[str]:
    return {row[1] for row in (await conn.execute(text("PRAGMA table_info(print_archives)"))).all()}


@pytest.mark.asyncio
async def test_retirement_columns_are_added_and_migration_is_idempotent(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'confirm-retirement.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Simulate the pre-#1898-follow-up schema: the columns only exist
            # here because create_all built the table from today's models.
            await conn.execute(text("ALTER TABLE print_archives DROP COLUMN confirm_token_used_at"))
            await conn.execute(text("ALTER TABLE print_archives DROP COLUMN user_verdict_source"))
            assert "confirm_token_used_at" not in await _archive_columns(conn)

            await run_migrations(conn)
            columns = await _archive_columns(conn)
            assert "confirm_token_used_at" in columns
            assert "user_verdict_source" in columns

            # Re-running the migrations on an already-migrated install is a
            # no-op, not an error: _safe_execute swallows the duplicate ALTER.
            await run_migrations(conn)
            assert await _archive_columns(conn) >= {"confirm_token_used_at", "user_verdict_source"}

            # ...with the SQLite-flavoured types the ALTERs declare, so a
            # stamp round-trips as a datetime rather than as opaque text.
            types = {
                row[1]: row[2].upper() for row in (await conn.execute(text("PRAGMA table_info(print_archives)"))).all()
            }
            assert types["confirm_token_used_at"] == "DATETIME"
            assert types["user_verdict_source"].startswith("VARCHAR")
    finally:
        await engine.dispose()


async def _archive_indexes(conn) -> dict[str, bool]:
    """Index name -> whether it is UNIQUE, for print_archives."""
    rows = (await conn.execute(text("PRAGMA index_list(print_archives)"))).all()
    return {row[1]: bool(row[2]) for row in rows}


def test_the_model_declares_the_index():
    """A fresh install gets its schema from the models, not from
    run_migrations, so the declaration is half the fix."""
    from backend.app.models.archive import PrintArchive

    column = PrintArchive.__table__.c.confirm_token
    assert column.index is True
    assert column.unique is True


@pytest.mark.asyncio
async def test_confirm_requested_is_nullable_on_both_kinds_of_install(tmp_path):
    """A fresh database and an upgraded one must describe the column the same way.

    ``ALTER TABLE ... ADD COLUMN confirm_requested BOOLEAN DEFAULT FALSE`` cannot
    carry NOT NULL, so an upgraded install has a nullable column. The model has
    to agree, or every install created from it has a stricter table than every
    install that grew into it -- and the difference only ever shows up as an
    IntegrityError on somebody else's machine.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'confirm-nullable.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            def notnull(rows):
                return {row[1]: row[3] for row in rows}

            fresh = notnull((await conn.execute(text("PRAGMA table_info(print_archives)"))).all())
            assert fresh["confirm_requested"] == 0

            # ...and the column an upgrade adds, for comparison.
            await conn.execute(text("ALTER TABLE print_archives DROP COLUMN confirm_requested"))
            await run_migrations(conn)
            upgraded = notnull((await conn.execute(text("PRAGMA table_info(print_archives)"))).all())
            assert upgraded["confirm_requested"] == fresh["confirm_requested"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_confirm_token_index_reaches_an_upgraded_install(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'confirm-token-index.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # An install that upgraded from before the index: the column is
            # there, the index is not.
            await conn.execute(text("DROP INDEX ix_print_archives_confirm_token"))
            assert "ix_print_archives_confirm_token" not in await _archive_indexes(conn)

            await run_migrations(conn)
            indexes = await _archive_indexes(conn)
            assert "ix_print_archives_confirm_token" in indexes
            assert indexes["ix_print_archives_confirm_token"] is True, "must be UNIQUE"

            # Re-running is a no-op, not an error.
            await run_migrations(conn)
            assert "ix_print_archives_confirm_token" in await _archive_indexes(conn)

            # Most archives never get a token, so the unique index has to
            # tolerate any number of NULLs — otherwise the second archive on a
            # fresh install would fail to insert.
            from backend.app.models.archive import PrintArchive

            rows = [
                {"filename": "a.3mf", "file_path": "a", "file_size": 1, "status": "completed"},
                {"filename": "b.3mf", "file_path": "b", "file_size": 1, "status": "completed"},
            ]
            await conn.execute(PrintArchive.__table__.insert(), rows)
            stored = (await conn.execute(text("SELECT confirm_token FROM print_archives"))).scalars().all()
            assert stored == [None, None]
    finally:
        await engine.dispose()


OLD_CONFIRM_BODY = "{printer}: {filename}\nGood: {good_url}\nReject: {reject_url}"


async def _confirm_body(conn) -> str | None:
    return (
        await conn.execute(
            text("SELECT body_template FROM notification_templates WHERE event_type = 'print_confirm_request'")
        )
    ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_the_capability_urls_leave_the_prompt_body_on_an_upgraded_install(tmp_path):
    """Seeding only inserts templates that are missing, so an install already
    running this feature would keep the shape the review found: both single-use
    verdict URLs as plain text in every channel's message body, where the
    unfurler that answers the prompt reads them."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'confirm-body.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "INSERT INTO notification_templates (event_type, name, title_template, body_template, is_default) "
                    "VALUES ('print_confirm_request', 'Print Outcome Confirmation', "
                    "'How did your print come out?', :body, 1)"
                ),
                {"body": OLD_CONFIRM_BODY},
            )

            await run_migrations(conn)

            body = await _confirm_body(conn)
            assert body is not None
            assert "{good_url}" not in body
            assert "{reject_url}" not in body
            assert "{confirm_url}" in body

            # Idempotent, and it agrees with what a fresh install seeds.
            await run_migrations(conn)
            from backend.app.models.notification_template import DEFAULT_TEMPLATES

            seeded = next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "print_confirm_request")
            assert await _confirm_body(conn) == seeded["body_template"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_template_the_admin_edited_is_left_alone(tmp_path):
    """Same guard as the two template renames: match the old default verbatim
    or do not touch the row."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'confirm-body-custom.db'}")
    custom = "Plate off {printer}? {good_url}"
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "INSERT INTO notification_templates (event_type, name, title_template, body_template, is_default) "
                    "VALUES ('print_confirm_request', 'Mine', 'Well?', :body, 0)"
                ),
                {"body": custom},
            )

            await run_migrations(conn)

            assert await _confirm_body(conn) == custom
    finally:
        await engine.dispose()
