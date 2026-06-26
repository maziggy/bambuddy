"""Regression test for the user_print_* notification template rename migration (#1792).

The four ``user_print_*`` notification templates seeded with names like
"User Print Completed" looked indistinguishable from the provider-level
"Print Completed" template in the Message Templates list (the EVENT_NAMES
display map in routes/notification_templates.py already used the disambiguated
"User Print Completed Email" label, but the seed wrote the short name to the
DB, so the UI rendered the ambiguous one).

The migration appends " Email" to those four template names IF AND ONLY IF
the row still has the old default name — admins who renamed the template
themselves keep their custom name. This test verifies both branches.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import _migrate_rename_user_print_template_names


@pytest.fixture
async def engine():
    """In-memory SQLite with just the notification_templates table.

    The migration is a single UPDATE on one table, so the fixture only needs
    that table — avoids the brittleness of registering every model in the
    project just to satisfy run_migrations's broader DDL surface.
    """
    from backend.app.models.notification_template import NotificationTemplate

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(NotificationTemplate.__table__.create)
    try:
        yield engine
    finally:
        await engine.dispose()


_OLD_DEFAULTS = {
    "user_print_start": "User Print Started",
    "user_print_complete": "User Print Completed",
    "user_print_failed": "User Print Failed",
    "user_print_stopped": "User Print Stopped",
}
_NEW_DEFAULTS = {
    "user_print_start": "User Print Started Email",
    "user_print_complete": "User Print Completed Email",
    "user_print_failed": "User Print Failed Email",
    "user_print_stopped": "User Print Stopped Email",
}


async def _insert_template(conn, event_type: str, name: str) -> None:
    await conn.execute(
        text(
            "INSERT INTO notification_templates "
            "(event_type, name, title_template, body_template, is_default) "
            "VALUES (:et, :n, 't', 'b', 1)"
        ),
        {"et": event_type, "n": name},
    )


async def _name_for(conn, event_type: str) -> str:
    return (
        await conn.execute(
            text("SELECT name FROM notification_templates WHERE event_type = :et"),
            {"et": event_type},
        )
    ).scalar_one()


async def test_migration_renames_default_named_user_print_rows(engine):
    """Rows with the old default name get the new disambiguated name."""
    async with engine.begin() as conn:
        for event_type, old_name in _OLD_DEFAULTS.items():
            await _insert_template(conn, event_type, old_name)

    async with engine.begin() as conn:
        await _migrate_rename_user_print_template_names(conn)

    async with engine.begin() as conn:
        for event_type, new_name in _NEW_DEFAULTS.items():
            assert await _name_for(conn, event_type) == new_name


async def test_migration_preserves_user_edited_names(engine):
    """An admin who renamed a template keeps their custom name across the migration."""
    async with engine.begin() as conn:
        await _insert_template(conn, "user_print_complete", "My Custom Renamed Template")
        await _insert_template(conn, "user_print_failed", "User Print Failed")  # still default

    async with engine.begin() as conn:
        await _migrate_rename_user_print_template_names(conn)

    async with engine.begin() as conn:
        # Custom name preserved
        assert await _name_for(conn, "user_print_complete") == "My Custom Renamed Template"
        # Default name renamed
        assert await _name_for(conn, "user_print_failed") == "User Print Failed Email"


async def test_migration_does_not_touch_provider_templates(engine):
    """The non-user provider templates with similar names must not be renamed."""
    async with engine.begin() as conn:
        await _insert_template(conn, "print_complete", "Print Completed")
        await _insert_template(conn, "print_failed", "Print Failed")

    async with engine.begin() as conn:
        await _migrate_rename_user_print_template_names(conn)

    async with engine.begin() as conn:
        assert await _name_for(conn, "print_complete") == "Print Completed"
        assert await _name_for(conn, "print_failed") == "Print Failed"


async def test_migration_is_idempotent(engine):
    """Running the migration twice must not double-suffix already-renamed rows."""
    async with engine.begin() as conn:
        for event_type, old_name in _OLD_DEFAULTS.items():
            await _insert_template(conn, event_type, old_name)

    async with engine.begin() as conn:
        await _migrate_rename_user_print_template_names(conn)
    async with engine.begin() as conn:
        await _migrate_rename_user_print_template_names(conn)

    async with engine.begin() as conn:
        for event_type, new_name in _NEW_DEFAULTS.items():
            current = await _name_for(conn, event_type)
            assert current == new_name
            assert "Email Email" not in current


async def test_migration_handles_empty_table(engine):
    """Migration on an empty table must be a safe no-op (fresh install path)."""
    async with engine.begin() as conn:
        await _migrate_rename_user_print_template_names(conn)

    async with engine.begin() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM notification_templates"))).scalar_one()
        assert count == 0
