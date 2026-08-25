"""Regression test for the ha_sensor_alert notification template rename migration (#2824).

"Home Assistant Sensor Alert" was fine as a name while it was the only such
template. Once "Storage Location Sensor Alert" existed alongside it, the
printer one no longer said which sensor feature it belonged to. The migration
renames it to "Printer Sensor Alert" IF AND ONLY IF the row still has the old
default name — an admin who renamed the template themselves keeps their
custom name, mirroring _migrate_rename_user_print_template_names.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import _migrate_rename_ha_sensor_alert_template


@pytest.fixture
async def engine():
    """In-memory SQLite with just the notification_templates table."""
    from backend.app.models.notification_template import NotificationTemplate

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(NotificationTemplate.__table__.create)
    try:
        yield engine
    finally:
        await engine.dispose()


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


async def test_renames_the_default_named_row(engine):
    async with engine.begin() as conn:
        await _insert_template(conn, "ha_sensor_alert", "Home Assistant Sensor Alert")

    async with engine.begin() as conn:
        await _migrate_rename_ha_sensor_alert_template(conn)

    async with engine.begin() as conn:
        assert await _name_for(conn, "ha_sensor_alert") == "Printer Sensor Alert"


async def test_preserves_a_user_edited_name(engine):
    async with engine.begin() as conn:
        await _insert_template(conn, "ha_sensor_alert", "My Enclosure Alerts")

    async with engine.begin() as conn:
        await _migrate_rename_ha_sensor_alert_template(conn)

    async with engine.begin() as conn:
        assert await _name_for(conn, "ha_sensor_alert") == "My Enclosure Alerts"


async def test_does_not_touch_the_location_template(engine):
    async with engine.begin() as conn:
        await _insert_template(conn, "ha_sensor_alert", "Home Assistant Sensor Alert")
        await _insert_template(conn, "location_ha_sensor_alert", "Storage Location Sensor Alert")

    async with engine.begin() as conn:
        await _migrate_rename_ha_sensor_alert_template(conn)

    async with engine.begin() as conn:
        assert await _name_for(conn, "location_ha_sensor_alert") == "Storage Location Sensor Alert"


async def test_is_idempotent(engine):
    async with engine.begin() as conn:
        await _insert_template(conn, "ha_sensor_alert", "Home Assistant Sensor Alert")

    async with engine.begin() as conn:
        await _migrate_rename_ha_sensor_alert_template(conn)
    async with engine.begin() as conn:
        await _migrate_rename_ha_sensor_alert_template(conn)

    async with engine.begin() as conn:
        assert await _name_for(conn, "ha_sensor_alert") == "Printer Sensor Alert"


async def test_handles_empty_table(engine):
    async with engine.begin() as conn:
        await _migrate_rename_ha_sensor_alert_template(conn)

    async with engine.begin() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM notification_templates"))).scalar_one()
        assert count == 0
