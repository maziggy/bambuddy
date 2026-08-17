"""Database-side timestamps are UTC on both dialects (#2855).

Bambuddy stores naive datetimes that hold UTC, and the frontend's
``parseUTCDate()`` reads a timestamp with no offset as UTC. #504 swept the
Python side onto ``datetime.now(timezone.utc)``, but roughly 96 columns take
their value from ``server_default=func.now()`` and the migration DDL carries
another ~49 ``DEFAULT CURRENT_TIMESTAMP`` — those are filled by the database.

SQLite's ``CURRENT_TIMESTAMP`` is UTC by definition, which is why the gap stayed
invisible for two years. PostgreSQL's ``now()`` is a ``timestamptz``, so writing
it into a ``timestamp without time zone`` column casts it through the session
``TimeZone``, and a Postgres container started with ``TZ=Europe/Istanbul`` bakes
that zone into postgresql.conf at initdb. Every defaulted timestamp then lands
as local wall-clock and renders three hours in the future.

Measured against a live PostgreSQL 16 while fixing this:

    no connect_args            TimeZone=UTC              now()::timestamp=05:50:46
    server_settings=Istanbul   TimeZone=Europe/Istanbul  now()::timestamp=08:50:46
    server_settings=UTC        TimeZone=UTC              now()::timestamp=05:50:46
"""

import os
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Column, DateTime, Integer, MetaData, Table, func, select
from sqlalchemy.ext.asyncio import create_async_engine


class TestConnectArgs:
    """What we hand the driver, per dialect."""

    def test_sqlite_gets_none(self, monkeypatch):
        """SQLite has no session timezone to pin, and passing an unknown connect
        arg to aiosqlite would be a TypeError at connect time."""
        from backend.app.core import database

        monkeypatch.setattr(database, "is_sqlite", lambda: True)

        assert database._resolve_connect_args() == {}

    def test_asyncpg_pins_the_session_to_utc(self, monkeypatch):
        from backend.app.core import database

        monkeypatch.setattr(database, "is_sqlite", lambda: False)
        monkeypatch.setattr(
            database.settings, "database_url", "postgresql+asyncpg://u:p@host:5432/bambuddy", raising=False
        )

        assert database._resolve_connect_args() == {"server_settings": {"timezone": "UTC"}}

    def test_other_postgres_drivers_go_through_libpq(self, monkeypatch):
        """``server_settings`` is an asyncpg keyword. psycopg would reject it, so
        a non-asyncpg URL gets the same setting the libpq way."""
        from backend.app.core import database

        monkeypatch.setattr(database, "is_sqlite", lambda: False)
        monkeypatch.setattr(
            database.settings, "database_url", "postgresql+psycopg://u:p@host:5432/bambuddy", raising=False
        )

        assert database._resolve_connect_args() == {"options": "-c timezone=UTC"}

    def test_create_engine_actually_passes_them(self, monkeypatch):
        """The resolver is only useful if it reaches ``create_async_engine`` —
        pin the wiring, not just the value."""
        from backend.app.core import database

        captured: dict = {}

        def fake_create_async_engine(url, **kwargs):
            captured.update(kwargs)
            return create_async_engine("sqlite+aiosqlite:///:memory:")

        monkeypatch.setattr(database, "is_sqlite", lambda: False)
        monkeypatch.setattr(
            database.settings, "database_url", "postgresql+asyncpg://u:p@host:5432/bambuddy", raising=False
        )
        monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)

        database._create_engine()

        assert captured["connect_args"] == {"server_settings": {"timezone": "UTC"}}

    def test_sqlite_engine_gets_no_connect_args(self, monkeypatch):
        """aiosqlite would raise on an unexpected keyword, so the empty dict has
        to be dropped rather than passed through."""
        from backend.app.core import database

        captured: dict = {}

        def fake_create_async_engine(url, **kwargs):
            captured.update(kwargs)
            return create_async_engine("sqlite+aiosqlite:///:memory:")

        monkeypatch.setattr(database, "is_sqlite", lambda: True)
        monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)

        database._create_engine()

        assert "connect_args" not in captured


@pytest.fixture
def istanbul_tz():
    """Run the process on UTC+3, the reporter's zone."""
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Istanbul"
    time.tzset()
    yield
    if original is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = original
    time.tzset()


class TestSqliteIsTheReference:
    """SQLite is what Postgres is being made to match, so pin its behaviour."""

    @pytest.mark.asyncio
    async def test_server_default_writes_utc_not_local(self, istanbul_tz):
        """``server_default=func.now()`` compiles to ``CURRENT_TIMESTAMP``, which
        SQLite defines as UTC regardless of the host clock. If this ever changed,
        every naive timestamp in the product would shift by the host offset."""
        metadata = MetaData()
        probe = Table(
            "tz_probe",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("created_at", DateTime, server_default=func.now()),
        )

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
                await conn.execute(probe.insert())
                stored = (await conn.execute(select(probe.c.created_at))).scalar()
        finally:
            await engine.dispose()

        # Local is three hours ahead of UTC here; a stored local value would sit
        # ~3h from utcnow and only ~0s from the local clock.
        assert abs(stored - datetime.utcnow()) < timedelta(minutes=5)
        assert abs(stored - datetime.now()) > timedelta(hours=2)


class TestQueueAgeUsesUtc:
    """The support bundle's ``oldest_pending_age_seconds`` (#2855)."""

    @pytest.mark.asyncio
    async def test_age_of_a_fresh_item_is_near_zero_on_a_non_utc_host(self, db_session, istanbul_tz):
        """The old code subtracted a naive *local* now() from a naive UTC column,
        so on UTC+3 a just-queued item reported as three hours old — and west of
        Greenwich the age came out negative."""
        from backend.app.api.routes.support import _collect_queue_info
        from backend.app.models.print_queue import PrintQueueItem
        from backend.app.utils.local_time import utcnow_naive

        db_session.add(PrintQueueItem(printer_id=1, status="pending", created_at=utcnow_naive()))
        await db_session.commit()

        info = await _collect_queue_info(db_session)

        assert info["pending_total"] == 1
        assert 0 <= info["oldest_pending_age_seconds"] < 60
