"""A write is committed before its response is sent (#3320).

``get_db`` commits after ``yield``. With FastAPI's default ``scope="request"``
that code runs after ``await response(...)``: the client had its 200 while the
row was still uncommitted, so a read issued straight after it could 404, and a
commit that failed was rolled back behind a 200 the client already had.

An httpx client only returns once the whole ASGI call has finished, so it
cannot see the order. These tests drive the app directly and look at the
database at the moment the response starts.
"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.database import Base, get_db
from backend.app.main import app
from backend.app.models.project import Project


async def _call(method: str, path: str, body: dict, on_start) -> list[dict]:
    """Run one HTTP request through the ASGI app; ``on_start`` runs as the response starts."""
    payload = json.dumps(body).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    messages: list[dict] = []

    async def send(message):
        if message["type"] == "http.response.start":
            await on_start(message)
        messages.append(message)

    await app(scope, receive, send)
    return messages


@pytest.fixture
async def file_engine(async_client, tmp_path, monkeypatch):
    """The production ``get_db`` on a file database instead of the conftest override.

    The shared test engine is in-memory SQLite: one connection, on which a
    "fresh" session sees rows nobody committed yet. A file gives every session
    its own connection, as a real install has.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bambuddy.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("backend.app.core.database.async_session", sessions)
    monkeypatch.setattr("backend.app.core.auth.async_session", sessions)
    override = app.dependency_overrides.pop(get_db, None)
    yield engine
    if override is not None:
        app.dependency_overrides[get_db] = override
    await engine.dispose()


class TestCommitBeforeResponse:
    async def test_new_row_is_committed_when_the_response_starts(self, file_engine):
        fresh = async_sessionmaker(file_engine, class_=AsyncSession, expire_on_commit=False)
        seen: list[int] = []

        async def on_start(message):
            assert message["status"] == 200
            async with fresh() as session:
                rows = (await session.execute(select(Project.id).where(Project.name == "issue-3320"))).all()
            seen.append(len(rows))

        # create_project only flushes; the commit is get_db's.
        await _call("POST", "/api/v1/projects/", {"name": "issue-3320"}, on_start)

        assert seen == [1]

    async def test_failed_commit_is_an_error_not_a_200(self, file_engine, monkeypatch):
        failing = async_sessionmaker(file_engine, class_=AsyncSession, expire_on_commit=False)

        async def refuse(self):
            raise OperationalError("COMMIT", {}, Exception("database is locked"))

        def factory():
            session = failing()
            monkeypatch.setattr(session, "commit", refuse.__get__(session))
            return session

        monkeypatch.setattr("backend.app.core.database.async_session", factory)
        statuses: list[int] = []

        async def on_start(message):
            statuses.append(message["status"])

        try:
            await _call("POST", "/api/v1/projects/", {"name": "issue-3320-locked"}, on_start)
        except OperationalError:
            pass  # surfaced to the server once nothing was sent yet

        assert 200 not in statuses
