"""Settings API for the loaded-filament-first queue mode (#3587)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.settings import Settings

URL = "/api/v1/settings/"


async def _row(db_session, key):
    result = await db_session.execute(select(Settings).where(Settings.key == key))
    row = result.scalar_one_or_none()
    return None if row is None else row.value


@pytest.mark.asyncio
@pytest.mark.integration
async def test_defaults(async_client: AsyncClient):
    data = (await async_client.get(URL)).json()
    assert data["queue_loaded_filament_first"] is False
    assert data["queue_max_wait_hours"] == 48


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bool_round_trip(async_client: AsyncClient):
    assert (await async_client.put(URL, json={"queue_loaded_filament_first": True})).status_code == 200
    assert (await async_client.get(URL)).json()["queue_loaded_filament_first"] is True
    await async_client.put(URL, json={"queue_loaded_filament_first": False})
    assert (await async_client.get(URL)).json()["queue_loaded_filament_first"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_null_toggle_is_refused(async_client: AsyncClient, db_session):
    """null would be stored as "None"; unlike the hours, the toggle has no "no limit"."""
    await async_client.put(URL, json={"queue_loaded_filament_first": True})
    response = await async_client.put(URL, json={"queue_loaded_filament_first": None})
    assert response.status_code == 422
    assert await _row(db_session, "queue_loaded_filament_first") in ("true", "True")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_hours_round_trip(async_client: AsyncClient):
    assert (await async_client.put(URL, json={"queue_max_wait_hours": 12})).status_code == 200
    assert (await async_client.get(URL)).json()["queue_max_wait_hours"] == 12


@pytest.mark.asyncio
@pytest.mark.integration
async def test_null_hours_round_trip(async_client: AsyncClient, db_session):
    response = await async_client.put(URL, json={"queue_max_wait_hours": None})
    assert response.status_code == 200
    assert await _row(db_session, "queue_max_wait_hours") == "None"
    assert (await async_client.get(URL)).json()["queue_max_wait_hours"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_missing_row_reads_default(async_client: AsyncClient, db_session):
    await async_client.put(URL, json={"queue_max_wait_hours": 5})
    row = (await db_session.execute(select(Settings).where(Settings.key == "queue_max_wait_hours"))).scalar_one()
    await db_session.delete(row)
    await db_session.commit()
    assert (await async_client.get(URL)).json()["queue_max_wait_hours"] == 48


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("value", [0, 8761])
async def test_hours_out_of_range_is_refused(async_client: AsyncClient, value):
    response = await async_client.put(URL, json={"queue_max_wait_hours": value})
    assert response.status_code == 422
    assert (await async_client.get(URL)).json()["queue_max_wait_hours"] == 48


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(("stored", "expected"), [(" 12 ", 12), ("0", 48), ("8761", 48), ("junk", 48), ("", None)])
async def test_a_stored_value_is_read_like_the_scheduler_reads_it(
    async_client: AsyncClient, db_session, stored, expected
):
    await async_client.put(URL, json={"queue_max_wait_hours": 5})
    row = (await db_session.execute(select(Settings).where(Settings.key == "queue_max_wait_hours"))).scalar_one()
    row.value = stored
    await db_session.commit()
    assert (await async_client.get(URL)).json()["queue_max_wait_hours"] == expected
