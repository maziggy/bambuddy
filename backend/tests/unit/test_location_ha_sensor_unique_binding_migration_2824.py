"""Regression tests for the location_ha_sensors unique-binding migration (#2824).

The API's duplicate check is read-then-insert, so two concurrent creates could
both pass it before the unique index existed. The migration adds the index to
databases whose table predates it (create_all only covers fresh installs) —
and first collapses any duplicates the pre-index race let in, keeping the
oldest row, because CREATE UNIQUE INDEX refuses to build over duplicates and
that failure would abort startup.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import _migrate_location_ha_sensor_unique_binding


@pytest.fixture
async def engine():
    """In-memory SQLite with the table as it existed BEFORE the index."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE location_ha_sensors ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "location_id INTEGER NOT NULL, "
                "name VARCHAR(100) NOT NULL, "
                "entity_id VARCHAR(255) NOT NULL)"
            )
        )
    try:
        yield engine
    finally:
        await engine.dispose()


async def _insert(conn, location_id: int, entity_id: str, name: str = "Sensor") -> None:
    await conn.execute(
        text("INSERT INTO location_ha_sensors (location_id, name, entity_id) VALUES (:l, :n, :e)"),
        {"l": location_id, "n": name, "e": entity_id},
    )


async def _rows(conn) -> list[tuple]:
    result = await conn.execute(text("SELECT id, location_id, entity_id FROM location_ha_sensors ORDER BY id"))
    return list(result.all())


async def test_collapses_duplicates_to_the_oldest_row(engine):
    async with engine.begin() as conn:
        await _insert(conn, 1, "sensor.humidity", "Original")
        await _insert(conn, 1, "sensor.humidity", "Race duplicate")
        await _insert(conn, 1, "sensor.temperature")

    async with engine.begin() as conn:
        await _migrate_location_ha_sensor_unique_binding(conn)

    async with engine.begin() as conn:
        assert await _rows(conn) == [(1, 1, "sensor.humidity"), (3, 1, "sensor.temperature")]


async def test_the_same_entity_on_two_locations_is_not_a_duplicate(engine):
    async with engine.begin() as conn:
        await _insert(conn, 1, "sensor.humidity")
        await _insert(conn, 2, "sensor.humidity")

    async with engine.begin() as conn:
        await _migrate_location_ha_sensor_unique_binding(conn)

    async with engine.begin() as conn:
        assert len(await _rows(conn)) == 2


async def test_the_index_then_rejects_new_duplicates(engine):
    async with engine.begin() as conn:
        await _migrate_location_ha_sensor_unique_binding(conn)

    async with engine.begin() as conn:
        await _insert(conn, 1, "sensor.humidity")

    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await _insert(conn, 1, "sensor.humidity")


async def test_is_idempotent(engine):
    async with engine.begin() as conn:
        await _insert(conn, 1, "sensor.humidity")

    async with engine.begin() as conn:
        await _migrate_location_ha_sensor_unique_binding(conn)
    async with engine.begin() as conn:
        await _migrate_location_ha_sensor_unique_binding(conn)

    async with engine.begin() as conn:
        assert len(await _rows(conn)) == 1


async def test_handles_empty_table(engine):
    async with engine.begin() as conn:
        await _migrate_location_ha_sensor_unique_binding(conn)

    async with engine.begin() as conn:
        assert await _rows(conn) == []
