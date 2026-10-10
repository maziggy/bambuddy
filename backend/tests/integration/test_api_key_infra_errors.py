"""A database error while checking an API key is not an invalid key.

``_validate_api_key`` caught every exception and returned None, which every
caller answers with 401 "Invalid API key": a valid key was refused as wrong
during a pool timeout, or when writing its last-used time hit "database is
locked", and the web interface clears its sign-in on that message. Such an
error now refuses the request with 503, as the auth checks in the middleware
do, and is logged.
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, update
from sqlalchemy.exc import OperationalError

from backend.app.models.api_key import APIKey


async def _admin_headers(async_client) -> dict[str, str]:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "keyadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "keyadmin", "password": "AdminPass1!"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _key(async_client) -> str:
    admin = await _admin_headers(async_client)
    created = await async_client.post(
        "/api/v1/api-keys/", headers=admin, json={"name": "integration", "can_read_status": True}
    )
    assert created.status_code in (200, 201), created.text
    return created.json()["key"]


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


PROTECTED = "/api/v1/printers/"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_valid_key_works(async_client):
    key = await _key(async_client)

    response = await async_client.get(PROTECTED, headers={"X-API-Key": key})

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failed_key_lookup_is_a_logged_503(async_client, test_engine, caplog):
    key = await _key(async_client)

    with _failing(test_engine, "SELECT API_KEYS") as state, caplog.at_level(logging.ERROR):
        response = await async_client.get(PROTECTED, headers={"X-API-Key": key})

    assert state["failed"]
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "Authentication service temporarily unavailable"
    assert "database is locked" in caplog.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failed_last_used_write_is_a_503_not_an_invalid_key(async_client, test_engine):
    key = await _key(async_client)

    with _failing(test_engine, "UPDATE API_KEYS") as state:
        response = await async_client.get(PROTECTED, headers={"X-API-Key": key})

    assert state["failed"]
    assert response.status_code == 503, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_wrong_key_is_still_a_401(async_client):
    key = await _key(async_client)

    response = await async_client.get(PROTECTED, headers={"X-API-Key": key[:-4] + "XXXX"})

    # The message depends on the route's dependency ("Invalid API key" or
    # "Authentication required"); the refusal is what matters.
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_expired_key_is_still_a_401(async_client, db_session):
    key = await _key(async_client)
    await db_session.execute(
        update(APIKey)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        .where(APIKey.name == "integration")
    )
    await db_session.commit()

    response = await async_client.get(PROTECTED, headers={"X-API-Key": key})

    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_key_row_with_an_unreadable_hash_is_no_match_and_is_reported(async_client, db_session, caplog):
    key = await _key(async_client)
    await db_session.execute(update(APIKey).values(key_hash="not-a-hash").where(APIKey.name == "integration"))
    await db_session.commit()

    with caplog.at_level(logging.WARNING):
        response = await async_client.get(PROTECTED, headers={"X-API-Key": key})

    assert response.status_code == 401
    assert "unreadable hash" in caplog.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_oversized_key_is_refused_without_blaming_the_stored_hash(async_client, caplog):
    """passlib refuses secrets over 4096 characters; that is the client's input,
    not a broken row, and must not be logged as one."""
    key = await _key(async_client)

    with caplog.at_level(logging.WARNING):
        response = await async_client.get(PROTECTED, headers={"X-API-Key": key[:8] + "a" * 5000})

    assert response.status_code == 401
    assert "unreadable hash" not in caplog.text
