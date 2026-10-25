"""The webhook routes check an API key the way every other route does.

They used ``get_api_key``, a copy of ``_validate_api_key`` that missed its
fixes: a database error, a key too long to hash and an unreadable stored hash
all came back as a 500, and a failed last-used write left the session dirty.
``get_api_key`` now reads the headers and leaves the check to
``_validate_api_key``, so these answer as they do everywhere: 503 when the key
can't be checked, 401 when it is not a valid key. A failed last-used write
after the key matched lets the key in (#3360).
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import event, update
from sqlalchemy.exc import OperationalError

from backend.app.models.api_key import APIKey

# A webhook route that answers 404 for a printer that doesn't exist, so a
# request that gets past the key check is easy to tell from one that doesn't.
ROUTE = "/api/v1/webhook/printer/99999/status"


@pytest.fixture
async def key(db_session) -> str:
    from backend.app.core.auth import generate_api_key

    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add(
        APIKey(name="webhook-key", key_hash=key_hash, key_prefix=key_prefix, can_read_status=True, enabled=True)
    )
    await db_session.commit()
    return full_key


@contextmanager
def _failing(test_engine, statement_start: str):
    """Make the next statement starting with *statement_start* fail as SQLite does when locked."""
    state = {"failed": False}

    def hook(conn, cursor, statement, parameters, context, executemany):
        if not state["failed"] and statement.lstrip().upper().startswith(statement_start):
            state["failed"] = True
            raise OperationalError(statement, parameters, Exception("database is locked"))

    event.listen(test_engine.sync_engine, "before_cursor_execute", hook)
    try:
        yield state
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", hook)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_valid_key_gets_through(async_client: AsyncClient, key):
    response = await async_client.get(ROUTE, headers={"X-API-Key": key})

    assert response.status_code == 404, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failed_key_lookup_is_a_503(async_client: AsyncClient, key, test_engine):
    with _failing(test_engine, "SELECT API_KEYS") as state:
        response = await async_client.get(ROUTE, headers={"X-API-Key": key})

    assert state["failed"]
    assert response.status_code == 503, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failed_last_used_write_lets_the_key_in(async_client: AsyncClient, key, test_engine):
    with _failing(test_engine, "UPDATE API_KEYS") as state:
        response = await async_client.get(ROUTE, headers={"X-API-Key": key})

    assert state["failed"]
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_oversized_key_is_a_401(async_client: AsyncClient, key):
    """passlib refuses secrets over 4096 characters; that is a wrong key, not a server error."""
    response = await async_client.get(ROUTE, headers={"X-API-Key": key[:8] + "a" * 5000})

    assert response.status_code == 401, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_key_row_with_an_unreadable_hash_is_a_401_and_reported(
    async_client: AsyncClient, key, db_session, caplog
):
    await db_session.execute(update(APIKey).values(key_hash="not-a-hash").where(APIKey.name == "webhook-key"))
    await db_session.commit()

    with caplog.at_level(logging.WARNING):
        response = await async_client.get(ROUTE, headers={"X-API-Key": key})

    assert response.status_code == 401, response.text
    assert "unreadable hash" in caplog.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_expired_key_is_a_401(async_client: AsyncClient, key, db_session):
    await db_session.execute(
        update(APIKey)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        .where(APIKey.name == "webhook-key")
    )
    await db_session.commit()

    response = await async_client.get(ROUTE, headers={"X-API-Key": key})

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Invalid API key"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_wrong_key_and_no_key_are_401s(async_client: AsyncClient, key):
    wrong = await async_client.get(ROUTE, headers={"X-API-Key": key[:-4] + "XXXX"})
    missing = await async_client.get(ROUTE)

    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "Invalid API key"
    assert missing.status_code == 401


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("route", [ROUTE, "/api/v1/printers/"])
@pytest.mark.parametrize("header", ["X-API-Key", "Authorization"])
async def test_a_disabled_key_is_a_401(async_client: AsyncClient, key, db_session, route, header):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="auth_enabled", value="true"))
    await db_session.execute(update(APIKey).values(enabled=False).where(APIKey.name == "webhook-key"))
    await db_session.commit()
    value = key if header == "X-API-Key" else f"Bearer {key}"

    response = await async_client.get(route, headers={header: value})

    assert response.status_code == 401, response.text
