"""A database error while checking an API key is not an invalid key.

``_validate_api_key`` caught every exception and returned None, which every
caller answers with 401 "Invalid API key": a valid key was refused as wrong
during a pool timeout, or when writing its last-used time hit "database is
locked", and the web interface clears its sign-in on that message. Such an
error now refuses the request with 503, as the auth checks in the middleware
do, and is logged.

Writing the key's last-used time is the exception: the key has been checked by
then, so a failed write (a read-only replica after a failover) is logged and
the key accepted. The time is written at most once a minute (#3360).
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import event, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core import auth
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
def _failing(test_engine, *statement_starts: str):
    """Make the next statements starting with *statement_starts*, in turn, fail
    as SQLite does when locked."""
    state = {"failed": False, "left": list(statement_starts)}

    def hook(conn, cursor, statement, parameters, context, executemany):
        if state["left"] and statement.lstrip().upper().startswith(state["left"][0]):
            state["left"].pop(0)
            state["failed"] = not state["left"]
            raise OperationalError(statement, parameters, Exception("database is locked"))

    event.listen(test_engine.sync_engine, "before_cursor_execute", hook)
    try:
        yield state
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", hook)


@contextmanager
def _counting(test_engine, statement_start: str):
    seen: list[str] = []

    def hook(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(statement_start):
            seen.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", hook)
    try:
        yield seen
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
async def test_a_failed_last_used_write_still_lets_the_key_in(async_client, test_engine, caplog):
    """The reporter's read-only replica: the key matched, only the write failed."""
    key = await _key(async_client)

    with _failing(test_engine, "UPDATE API_KEYS") as state, caplog.at_level(logging.WARNING):
        response = await async_client.get(PROTECTED, headers={"X-API-Key": key})

    assert state["failed"]
    assert response.status_code == 200, response.text
    assert "Could not record API key" in caplog.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failed_write_and_a_failed_reread_is_a_503(async_client, test_engine):
    key = await _key(async_client)

    with _failing(test_engine, "UPDATE API_KEYS", "SELECT API_KEYS") as state:
        response = await async_client.get(PROTECTED, headers={"X-API-Key": key})

    assert state["failed"]
    assert response.status_code == 503, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_key_turned_off_before_the_reread_is_refused(async_client, test_engine):
    key = await _key(async_client)
    original = AsyncSession.refresh

    async def refresh_after_disabling(self, instance, *args, **kwargs):
        await original(self, instance, *args, **kwargs)
        if isinstance(instance, APIKey):
            instance.enabled = False

    with (
        _failing(test_engine, "UPDATE API_KEYS"),
        patch.object(AsyncSession, "refresh", refresh_after_disabling),
    ):
        response = await async_client.get(PROTECTED, headers={"X-API-Key": key})

    assert response.status_code == 401, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_key_set_to_expire_before_the_reread_is_refused(async_client, test_engine):
    """Setting a past expiry revokes a key just as turning it off does."""
    key = await _key(async_client)
    original = AsyncSession.refresh

    async def refresh_after_expiring(self, instance, *args, **kwargs):
        await original(self, instance, *args, **kwargs)
        if isinstance(instance, APIKey):
            instance.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    with (
        _failing(test_engine, "UPDATE API_KEYS"),
        patch.object(AsyncSession, "refresh", refresh_after_expiring),
    ):
        response = await async_client.get(PROTECTED, headers={"X-API-Key": key})

    assert response.status_code == 401, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failing_write_is_not_retried_on_every_request(async_client, test_engine):
    """On SQLite each try waits out the busy timeout first."""
    key = await _key(async_client)

    with _counting(test_engine, "UPDATE API_KEYS") as writes, _failing(test_engine, "UPDATE API_KEYS"):
        for _ in range(3):
            response = await async_client.get(PROTECTED, headers={"X-API-Key": key})
            assert response.status_code == 200, response.text

    assert len(writes) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_last_used_is_written_at_most_once_a_minute(async_client, test_engine, db_session):
    key = await _key(async_client)

    with _counting(test_engine, "UPDATE API_KEYS") as writes:
        for _ in range(3):
            response = await async_client.get(PROTECTED, headers={"X-API-Key": key})
            assert response.status_code == 200, response.text
    assert len(writes) == 1

    await db_session.execute(
        update(APIKey)
        .values(last_used=datetime.now(timezone.utc) - timedelta(minutes=2))
        .where(APIKey.name == "integration")
    )
    await db_session.commit()
    # The attempt this process remembers is a minute old too.
    auth._api_key_last_used._attempts.clear()
    with _counting(test_engine, "UPDATE API_KEYS") as writes:
        response = await async_client.get(PROTECTED, headers={"X-API-Key": key})
    assert response.status_code == 200, response.text
    assert len(writes) == 1


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
