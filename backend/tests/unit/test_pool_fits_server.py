"""The pool must not silently be allowed to outgrow the PostgreSQL server.

``pool_size + max_overflow`` is the most connections one worker will open. When
that exceeds what the server permits, the pool never hits its own limit and so
never queues — it asks the server, which refuses with
``TooManyConnectionsError`` at whatever happened to need a connection next. In
the report behind this, that was the middle of a queue dispatch.

The check is diagnostic, not corrective: pool sizes are fixed at engine creation
(import time, before any connection exists to ask with), and the right ceiling
depends on the worker count and on other clients sharing the server. So the
contract under test is "says something accurate and loud, and never breaks
startup".
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _engine_reporting(max_conn: int, reserved: int, in_use: int | None = 0) -> MagicMock:
    """An engine whose connection answers the three probe queries in order.

    ``in_use=None`` makes the third query fail, standing in for PostgreSQL < 10
    where ``pg_stat_activity.backend_type`` does not exist.
    """
    conn = MagicMock()
    conn.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one=MagicMock(return_value=max_conn)),
            MagicMock(scalar_one=MagicMock(return_value=reserved)),
            (
                MagicMock(scalar_one=MagicMock(return_value=in_use))
                if in_use is not None
                else RuntimeError('column "backend_type" does not exist')
            ),
        ]
    )
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect = MagicMock(return_value=ctx)
    return engine


async def _run_check(*, pool_size, max_overflow, max_conn, reserved, in_use=0, sqlite=False):
    from backend.app.core import database

    with (
        patch.object(database, "is_sqlite", return_value=sqlite),
        patch.object(database, "_pool_config", {"pool_size": pool_size, "max_overflow": max_overflow}),
        patch.object(database, "engine", _engine_reporting(max_conn, reserved, in_use)),
        patch.object(database, "_server_connection_limits", None),
    ):
        await database.check_pool_fits_server()
        return database._server_connection_limits


@pytest.mark.asyncio
@pytest.mark.unit
async def test_warns_when_the_ceiling_exceeds_what_the_server_allows(caplog):
    """Bambuddy's own PostgreSQL default against a stock server: 100 vs 100-3."""
    with caplog.at_level(logging.WARNING, logger="backend.app.core.database"):
        await _run_check(pool_size=20, max_overflow=80, max_conn=100, reserved=3)

    assert any(r.levelno == logging.WARNING for r in caplog.records)
    msg = caplog.text
    # The numbers an operator needs, and the knobs to change.
    for expected in ("100", "97", "DB_POOL_SIZE", "DB_MAX_OVERFLOW", "max_connections"):
        assert expected in msg, f"warning omits {expected!r}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_silent_when_the_pool_fits(caplog):
    with caplog.at_level(logging.WARNING, logger="backend.app.core.database"):
        await _run_check(pool_size=20, max_overflow=80, max_conn=500, reserved=3)

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_the_reserved_slots_count_against_the_budget(caplog):
    """Exactly at max_connections is still too many — reserved slots are not ours."""
    with caplog.at_level(logging.WARNING, logger="backend.app.core.database"):
        await _run_check(pool_size=10, max_overflow=90, max_conn=100, reserved=3)

    assert [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_both_sides_are_recorded_for_the_support_bundle():
    limits = await _run_check(pool_size=20, max_overflow=80, max_conn=100, reserved=3, in_use=41)

    assert limits == {
        "max_connections": 100,
        "superuser_reserved_connections": 3,
        "available_to_bambuddy": 97,
        "client_backends_at_startup": 41,
        "pool_ceiling_per_worker": 100,
    }


@pytest.mark.asyncio
@pytest.mark.unit
async def test_sqlite_is_skipped_entirely():
    """No such concept, and the probe SQL is PostgreSQL-only."""
    limits = await _run_check(pool_size=20, max_overflow=200, max_conn=0, reserved=0, sqlite=True)

    assert limits is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_a_probe_failure_cannot_break_startup(caplog):
    """A restricted role or an older server may refuse these queries."""
    from backend.app.core import database

    engine = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("permission denied"))
    ctx.__aexit__ = AsyncMock(return_value=False)
    engine.connect = MagicMock(return_value=ctx)

    with (
        patch.object(database, "is_sqlite", return_value=False),
        patch.object(database, "_pool_config", {"pool_size": 20, "max_overflow": 80}),
        patch.object(database, "engine", engine),
        patch.object(database, "_server_connection_limits", None),
        caplog.at_level(logging.WARNING, logger="backend.app.core.database"),
    ):
        await database.check_pool_fits_server()  # must not raise

        assert database._server_connection_limits is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_an_old_server_without_backend_type_still_gets_the_warning(caplog):
    """`pg_stat_activity.backend_type` is PostgreSQL 10+; the docs recommend 14+
    but asyncpg reaches back to 9.5. Losing that count must not cost the
    warning, which only needs the two settings."""
    with caplog.at_level(logging.WARNING, logger="backend.app.core.database"):
        limits = await _run_check(pool_size=20, max_overflow=80, max_conn=100, reserved=3, in_use=None)

    assert [r for r in caplog.records if r.levelno == logging.WARNING], "warning was lost with the count"
    assert "100" in caplog.text and "97" in caplog.text
    # The sentence about other clients is dropped rather than rendered as None.
    assert "None client" not in caplog.text
    assert limits["client_backends_at_startup"] is None
    assert limits["max_connections"] == 100


@pytest.mark.unit
def test_get_pool_status_exposes_the_server_limits_key():
    """The support bundle reads this; the key must exist even on SQLite."""
    from backend.app.core.database import get_pool_status

    assert "server_limits" in get_pool_status()
