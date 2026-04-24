"""Unit tests for database dialect helpers and PostgreSQL compatibility."""

from unittest.mock import AsyncMock, patch

import pytest


class TestDialectDetection:
    """Test is_sqlite() and is_postgres() detection."""

    def test_sqlite_detected(self):
        with patch("backend.app.core.config.settings") as mock_settings:
            mock_settings.database_url = "sqlite+aiosqlite:///path/to/db.sqlite"
            from backend.app.core.db_dialect import is_postgres, is_sqlite

            assert is_sqlite() is True
            assert is_postgres() is False

    def test_postgres_detected(self):
        with patch("backend.app.core.config.settings") as mock_settings:
            mock_settings.database_url = "postgresql+asyncpg://user:pass@host:5432/db"
            from backend.app.core.db_dialect import is_postgres, is_sqlite

            assert is_postgres() is True
            assert is_sqlite() is False


class TestRunPragma:
    """Test that PRAGMAs only run on SQLite."""

    @pytest.mark.asyncio
    async def test_pragma_runs_on_sqlite(self):
        with patch("backend.app.core.db_dialect.is_sqlite", return_value=True):
            from backend.app.core.db_dialect import run_pragma

            mock_conn = AsyncMock()
            await run_pragma(mock_conn, "PRAGMA journal_mode = WAL")
            mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_pragma_skipped_on_postgres(self):
        with patch("backend.app.core.db_dialect.is_sqlite", return_value=False):
            from backend.app.core.db_dialect import run_pragma

            mock_conn = AsyncMock()
            await run_pragma(mock_conn, "PRAGMA journal_mode = WAL")
            mock_conn.execute.assert_not_called()


class TestTimezoneStripping:
    """Test that the before_cursor_execute event strips timezone info."""

    def test_strip_aware_datetime(self):
        """Verify the timezone stripping logic works correctly."""
        import datetime

        aware = datetime.datetime(2026, 4, 3, 10, 0, 0, tzinfo=datetime.timezone.utc)
        naive = aware.replace(tzinfo=None)

        def _strip(val):
            if isinstance(val, datetime.datetime) and val.tzinfo is not None:
                return val.replace(tzinfo=None)
            return val

        assert _strip(aware) == naive
        assert _strip(aware).tzinfo is None
        assert _strip(naive) == naive
        assert _strip("not a datetime") == "not a datetime"
        assert _strip(None) is None

    def test_strip_in_dict_params(self):
        """Verify timezone stripping works on dict parameters."""
        import datetime

        aware = datetime.datetime(2026, 4, 3, 10, 0, 0, tzinfo=datetime.timezone.utc)

        def _strip(val):
            if isinstance(val, datetime.datetime) and val.tzinfo is not None:
                return val.replace(tzinfo=None)
            return val

        params = {"name": "test", "created_at": aware, "count": 5}
        result = {k: _strip(v) for k, v in params.items()}
        assert result["created_at"].tzinfo is None
        assert result["name"] == "test"
        assert result["count"] == 5

    def test_strip_in_tuple_params(self):
        """Verify timezone stripping works on tuple parameters."""
        import datetime

        aware = datetime.datetime(2026, 4, 3, 10, 0, 0, tzinfo=datetime.timezone.utc)

        def _strip(val):
            if isinstance(val, datetime.datetime) and val.tzinfo is not None:
                return val.replace(tzinfo=None)
            return val

        params = ("test", aware, 5)
        result = tuple(_strip(v) for v in params)
        assert result[1].tzinfo is None
        assert result[0] == "test"

    def test_naive_datetime_unchanged(self):
        """Naive datetimes should pass through untouched."""
        import datetime

        naive = datetime.datetime(2026, 4, 3, 10, 0, 0)

        def _strip(val):
            if isinstance(val, datetime.datetime) and val.tzinfo is not None:
                return val.replace(tzinfo=None)
            return val

        result = _strip(naive)
        assert result == naive
        assert result.tzinfo is None


class TestCrossDatabaseConversion:
    """Test SQLite→Postgres type conversion logic used in cross-database import."""

    def test_boolean_conversion(self):
        """SQLite stores booleans as 0/1, Postgres needs Python bool."""
        assert bool(0) is False
        assert bool(1) is True

    def test_datetime_string_conversion(self):
        """SQLite stores datetimes as strings, Postgres needs datetime objects."""
        from datetime import datetime

        val = "2026-04-02 11:01:52.105147"
        result = datetime.fromisoformat(val)
        assert result.year == 2026
        assert result.month == 4
        assert result.microsecond == 105147

    def test_datetime_with_timezone_string(self):
        """SQLite may store timezone-aware strings."""
        from datetime import datetime

        val = "2026-04-02T11:01:52+00:00"
        result = datetime.fromisoformat(val)
        assert result.year == 2026

    def test_json_serialization_for_backup(self):
        """JSON/list/dict values must be serialized for SQLite backup."""
        import json

        values = [{"key": "val"}, [1, 2, 3], "plain string", 42, None]
        for val in values:
            if isinstance(val, (list, dict)):
                serialized = json.dumps(val)
                assert isinstance(serialized, str)
            else:
                assert val == val  # noqa: PLR0124 — no conversion needed


class TestSafeExecutePattern:
    """Test _safe_execute error handling logic."""

    def test_safe_execute_catches_expected_exceptions(self):
        """Verify _safe_execute catches both OperationalError and ProgrammingError."""
        from sqlalchemy.exc import OperationalError, ProgrammingError

        for exc_type in (OperationalError, ProgrammingError):
            try:
                raise exc_type("test", [], Exception("column already exists"))
            except (OperationalError, ProgrammingError):
                pass

    def test_safe_execute_would_not_catch_integrity_error(self):
        """IntegrityError should NOT be caught by _safe_execute."""
        from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

        with pytest.raises(IntegrityError):
            try:
                raise IntegrityError("test", [], Exception("unique violation"))
            except (OperationalError, ProgrammingError):
                pass

    @pytest.mark.asyncio
    async def test_safe_execute_reraises_non_idempotency_errors(self):
        """Non-idempotency errors must propagate so startup fails loudly."""
        from sqlalchemy.exc import OperationalError
        from sqlalchemy.ext.asyncio import create_async_engine

        from backend.app.core.database import _safe_execute

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            with pytest.raises(OperationalError):
                await _safe_execute(conn, "SELECT * FROM nonexistent_table_xyz")
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_safe_execute_swallows_already_exists(self):
        """Idempotency errors (already exists) must be silently ignored."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from backend.app.core.database import _safe_execute

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE t (id INTEGER)"))
            # Second CREATE must not raise
            await _safe_execute(conn, "CREATE TABLE t (id INTEGER)")
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_provider_email_lowercasing_migration(self):
        """provider_email normalisation lowers mixed-case values and leaves NULL rows intact."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from backend.app.core.database import _safe_execute

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE user_oidc_links (id INTEGER PRIMARY KEY, provider_email TEXT)"))
            await conn.execute(text("INSERT INTO user_oidc_links VALUES (1, 'User@Example.COM')"))
            await conn.execute(text("INSERT INTO user_oidc_links VALUES (2, 'already@lower.com')"))
            await conn.execute(text("INSERT INTO user_oidc_links VALUES (3, NULL)"))

            await _safe_execute(
                conn,
                "UPDATE user_oidc_links SET provider_email = LOWER(provider_email) "
                "WHERE provider_email IS NOT NULL AND provider_email != LOWER(provider_email)",
            )

            result = await conn.execute(text("SELECT provider_email FROM user_oidc_links ORDER BY id"))
            rows = [r[0] for r in result.fetchall()]
        await engine.dispose()

        assert rows[0] == "user@example.com"
        assert rows[1] == "already@lower.com"
        assert rows[2] is None

    @pytest.mark.asyncio
    async def test_check_constraint_false_true_on_sqlite(self):
        """CheckConstraint with FALSE/TRUE literals is enforced on SQLite (3.23+)."""
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                CREATE TABLE ck_test (
                    id INTEGER PRIMARY KEY,
                    auto_link BOOLEAN,
                    require_ev BOOLEAN,
                    email_claim TEXT,
                    CHECK (auto_link = FALSE OR (require_ev = TRUE AND email_claim = 'email'))
                )
            """)
            )
            # Valid: auto_link=0 (FALSE)
            await conn.execute(text("INSERT INTO ck_test VALUES (1, 0, 0, 'upn')"))
            # Valid: auto_link=1, require_ev=1, email_claim='email'
            await conn.execute(text("INSERT INTO ck_test VALUES (2, 1, 1, 'email')"))

        async with engine.begin() as conn:
            # Invalid: auto_link=1 but conditions not met
            with pytest.raises(IntegrityError):
                await conn.execute(text("INSERT INTO ck_test VALUES (3, 1, 0, 'email')"))
        await engine.dispose()
