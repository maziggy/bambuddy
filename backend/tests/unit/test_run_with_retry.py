"""Tests for database.run_with_retry — SQLite lock retry logic (#897)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import OperationalError


@pytest.fixture(autouse=True)
def _force_sqlite():
    """Make is_sqlite() return True for all tests in this module."""
    with patch("backend.app.core.database.is_sqlite", return_value=True):
        yield


def _make_locked_error() -> OperationalError:
    """Create a realistic 'database is locked' OperationalError."""
    return OperationalError(
        statement="UPDATE print_queue SET status=?",
        params=("completed",),
        orig=Exception("database is locked"),
    )


def _make_other_error() -> OperationalError:
    """Create a non-lock OperationalError."""
    return OperationalError(
        statement="SELECT 1",
        params=(),
        orig=Exception("no such table: foo"),
    )


@pytest.mark.asyncio
async def test_succeeds_on_first_attempt():
    """Happy path — fn succeeds immediately."""
    from backend.app.core.database import run_with_retry

    mock_fn = AsyncMock(return_value="ok")

    with patch("backend.app.core.database.async_session") as mock_session_factory:
        mock_db = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_with_retry(mock_fn, label="test")

    assert result == "ok"
    mock_fn.assert_awaited_once_with(mock_db)


@pytest.mark.asyncio
async def test_retries_on_sqlite_locked():
    """fn fails with 'database is locked' then succeeds on retry."""
    from backend.app.core.database import run_with_retry

    call_count = 0

    async def flaky_fn(db):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _make_locked_error()
        return "recovered"

    with (
        patch("backend.app.core.database.async_session") as mock_session_factory,
        patch("backend.app.core.database.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        mock_db = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_with_retry(flaky_fn, label="test")

    assert result == "recovered"
    assert call_count == 2
    mock_sleep.assert_awaited_once_with(0.5)  # first retry: 0.5s delay


@pytest.mark.asyncio
async def test_raises_after_max_attempts():
    """fn fails with 'database is locked' on all attempts — raises."""
    from backend.app.core.database import run_with_retry

    async def always_locked(db):
        raise _make_locked_error()

    with (
        patch("backend.app.core.database.async_session") as mock_session_factory,
        patch("backend.app.core.database.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_db = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(OperationalError, match="database is locked"):
            await run_with_retry(always_locked, max_attempts=3, label="test")


@pytest.mark.asyncio
async def test_non_lock_error_not_retried():
    """Non-lock OperationalErrors are raised immediately, not retried."""
    from backend.app.core.database import run_with_retry

    call_count = 0

    async def bad_fn(db):
        nonlocal call_count
        call_count += 1
        raise _make_other_error()

    with (
        patch("backend.app.core.database.async_session") as mock_session_factory,
        patch("backend.app.core.database.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        mock_db = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(OperationalError, match="no such table"):
            await run_with_retry(bad_fn, label="test")

    assert call_count == 1
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_backoff_increases():
    """Retry delays increase: 0.5s, 1.0s, 1.5s."""
    from backend.app.core.database import run_with_retry

    call_count = 0

    async def recovers_on_third(db):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise _make_locked_error()
        return "ok"

    with (
        patch("backend.app.core.database.async_session") as mock_session_factory,
        patch("backend.app.core.database.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        mock_db = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_with_retry(recovers_on_third, max_attempts=3, label="test")

    assert result == "ok"
    assert call_count == 3
    assert mock_sleep.await_args_list[0].args == (0.5,)
    assert mock_sleep.await_args_list[1].args == (1.0,)


@pytest.mark.asyncio
async def test_postgres_no_retry():
    """On PostgreSQL, fn is called once with no retry logic."""
    from backend.app.core.database import run_with_retry

    mock_fn = AsyncMock(return_value="pg_ok")

    with (
        patch("backend.app.core.database.is_sqlite", return_value=False),
        patch("backend.app.core.database.async_session") as mock_session_factory,
    ):
        mock_db = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_with_retry(mock_fn, label="test")

    assert result == "pg_ok"
    mock_fn.assert_awaited_once_with(mock_db)


@pytest.mark.asyncio
async def test_postgres_error_not_retried():
    """On PostgreSQL, OperationalErrors are raised immediately."""
    from backend.app.core.database import run_with_retry

    async def bad_fn(db):
        raise _make_locked_error()

    with (
        patch("backend.app.core.database.is_sqlite", return_value=False),
        patch("backend.app.core.database.async_session") as mock_session_factory,
    ):
        mock_db = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(OperationalError):
            await run_with_retry(bad_fn, label="test")
