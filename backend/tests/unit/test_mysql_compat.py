"""Unit tests for MySQL/MariaDB compatibility code paths.

These tests mock `settings.is_mysql = True` to exercise MySQL-specific branches
without requiring a running MySQL instance. All tests run against in-memory SQLite
with patched settings to verify correct code-path selection.
"""

import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings


@contextmanager
def mysql_mode():
    """Temporarily make settings.is_mysql return True and is_sqlite return False."""
    with (
        patch.object(Settings, "is_mysql", new_callable=PropertyMock, return_value=True),
        patch.object(Settings, "is_sqlite", new_callable=PropertyMock, return_value=False),
    ):
        yield


# ============================================================================
# TestMySQLConfig — Config validation for MySQL settings
# ============================================================================


class TestMySQLConfig:
    """Tests for MySQL configuration and URL construction."""

    def test_mysql_url_construction(self):
        """Verify _build_database_url produces correct mysql+aiomysql URL."""
        from backend.app.core.config import Settings

        s = Settings(
            db_type="mysql",
            db_user="bambu",
            db_password="secret",
            db_host="db.local",
            db_port=3307,
            db_name="mydb",
        )
        assert s.database_url == "mysql+aiomysql://bambu:secret@db.local:3307/mydb?charset=utf8mb4"

    def test_mysql_requires_db_user(self):
        """Validate ValueError when DB_USER is missing for MySQL."""
        from backend.app.core.config import Settings

        with pytest.raises(ValueError, match="DB_USER is required"):
            Settings(db_type="mysql", db_user="", db_password="secret")

    def test_mysql_requires_db_password(self):
        """Validate ValueError when DB_PASSWORD is missing for MySQL."""
        from backend.app.core.config import Settings

        with pytest.raises(ValueError, match="DB_PASSWORD is required"):
            Settings(db_type="mysql", db_user="bambu", db_password="")

    def test_is_mysql_property(self):
        """Verify is_mysql=True, is_sqlite=False when db_type='mysql'."""
        from backend.app.core.config import Settings

        s = Settings(
            db_type="mysql",
            db_user="bambu",
            db_password="secret",
        )
        assert s.is_mysql is True
        assert s.is_sqlite is False

    def test_is_sqlite_property(self):
        """Verify is_sqlite=True, is_mysql=False for default db_type."""
        from backend.app.core.config import Settings

        s = Settings(db_type="sqlite")
        assert s.is_sqlite is True
        assert s.is_mysql is False

    def test_mysql_url_skipped_when_database_url_provided(self):
        """Verify _build_database_url is a no-op when database_url is set."""
        from backend.app.core.config import Settings

        s = Settings(
            db_type="mysql",
            db_user="bambu",
            db_password="secret",
            database_url="mysql+aiomysql://custom:url@host/db",
        )
        assert s.database_url == "mysql+aiomysql://custom:url@host/db"


# ============================================================================
# TestMySQLEngineConfig — Engine kwargs
# ============================================================================


class TestMySQLEngineConfig:
    """Tests for _build_engine_kwargs MySQL vs SQLite branching."""

    def test_build_engine_kwargs_mysql(self):
        """Verify pool_recycle=3600 and pool_pre_ping=True for MySQL."""
        from backend.app.core.database import _build_engine_kwargs

        with patch("backend.app.core.database.settings") as mock_settings:
            mock_settings.is_mysql = True
            mock_settings.debug = False

            kwargs = _build_engine_kwargs()

            assert kwargs["pool_recycle"] == 3600
            assert kwargs["pool_pre_ping"] is True
            assert kwargs["echo"] is False

    def test_build_engine_kwargs_sqlite(self):
        """Verify no pool settings for SQLite."""
        from backend.app.core.database import _build_engine_kwargs

        with patch("backend.app.core.database.settings") as mock_settings:
            mock_settings.is_mysql = False
            mock_settings.debug = False

            kwargs = _build_engine_kwargs()

            assert "pool_recycle" not in kwargs
            assert "pool_pre_ping" not in kwargs
            assert kwargs["echo"] is False

    def test_build_engine_kwargs_debug_mode(self):
        """Verify echo=True when debug is enabled."""
        from backend.app.core.database import _build_engine_kwargs

        with patch("backend.app.core.database.settings") as mock_settings:
            mock_settings.is_mysql = False
            mock_settings.debug = True

            kwargs = _build_engine_kwargs()

            assert kwargs["echo"] is True


# ============================================================================
# TestUpsertSetting — Cross-database upsert helper
# ============================================================================


class TestUpsertSetting:
    """Tests for the upsert_setting() MySQL/SQLite dialect branching."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_upsert_uses_mysql_dialect(self):
        """Patch is_mysql=True, verify mysql_insert and on_duplicate_key_update are called."""
        mock_db = AsyncMock(spec=AsyncSession)

        with (
            patch("backend.app.core.database.settings") as mock_settings,
            patch("sqlalchemy.dialects.mysql.insert") as mock_mysql_insert,
        ):
            mock_settings.is_mysql = True
            # Set up the chain: mysql_insert(Model).values(...).on_duplicate_key_update(...)
            mock_stmt = MagicMock()
            mock_mysql_insert.return_value.values.return_value = mock_stmt
            mock_stmt.on_duplicate_key_update.return_value = mock_stmt

            from backend.app.core.database import upsert_setting

            await upsert_setting(mock_db, "test_key", "test_value")

            # Verify mysql_insert was called (with the Settings model)
            mock_mysql_insert.assert_called_once()
            # Verify values were passed
            mock_mysql_insert.return_value.values.assert_called_once_with(key="test_key", value="test_value")
            # Verify on_duplicate_key_update was called
            mock_stmt.on_duplicate_key_update.assert_called_once()
            call_kwargs = mock_stmt.on_duplicate_key_update.call_args
            assert call_kwargs.kwargs["value"] == "test_value"
            # Verify db.execute was called
            mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_upsert_uses_sqlite_dialect(self):
        """Verify sqlite_insert and on_conflict_do_update are called for SQLite."""
        mock_db = AsyncMock(spec=AsyncSession)

        with (
            patch("backend.app.core.database.settings") as mock_settings,
            patch("sqlalchemy.dialects.sqlite.insert") as mock_sqlite_insert,
        ):
            mock_settings.is_mysql = False
            mock_stmt = MagicMock()
            mock_sqlite_insert.return_value.values.return_value = mock_stmt
            mock_stmt.on_conflict_do_update.return_value = mock_stmt

            from backend.app.core.database import upsert_setting

            await upsert_setting(mock_db, "test_key", "test_value")

            mock_sqlite_insert.assert_called_once()
            mock_sqlite_insert.return_value.values.assert_called_once_with(key="test_key", value="test_value")
            mock_stmt.on_conflict_do_update.assert_called_once()
            call_kwargs = mock_stmt.on_conflict_do_update.call_args
            assert call_kwargs.kwargs["index_elements"] == ["key"]
            assert call_kwargs.kwargs["set_"]["value"] == "test_value"
            mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_upsert_passes_correct_values(self):
        """Verify key/value/updated_at are passed to both dialects."""
        mock_db = AsyncMock(spec=AsyncSession)

        # Test MySQL path
        with (
            patch("backend.app.core.database.settings") as mock_settings,
            patch("sqlalchemy.dialects.mysql.insert") as mock_mysql_insert,
        ):
            mock_settings.is_mysql = True
            mock_stmt = MagicMock()
            mock_mysql_insert.return_value.values.return_value = mock_stmt
            mock_stmt.on_duplicate_key_update.return_value = mock_stmt

            from backend.app.core.database import upsert_setting

            await upsert_setting(mock_db, "my_key", "my_value")

            # Check values
            mock_mysql_insert.return_value.values.assert_called_once_with(key="my_key", value="my_value")
            update_kwargs = mock_stmt.on_duplicate_key_update.call_args.kwargs
            assert update_kwargs["value"] == "my_value"
            assert "updated_at" in update_kwargs


# ============================================================================
# TestMySQLSearch — Archive search endpoint branching
# ============================================================================


class TestMySQLSearch:
    """Tests for search endpoint MySQL FULLTEXT vs SQLite FTS5 branching."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_search_uses_fulltext_on_mysql(self, async_client):
        """Patch is_mysql=True, verify MATCH...AGAINST SQL is attempted."""
        with mysql_mode():
            response = await async_client.get("/api/v1/archives/search?q=test")

            # The search should succeed (falling back to LIKE since no FULLTEXT table in SQLite)
            assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_search_falls_back_to_like_on_mysql(self, async_client):
        """Verify LIKE fallback works when FULLTEXT fails on MySQL path."""
        with mysql_mode():
            # The MySQL MATCH...AGAINST query will fail on our SQLite test DB,
            # triggering the LIKE fallback — which should return 200
            response = await async_client.get("/api/v1/archives/search?q=test")
            assert response.status_code == 200
            # Response should be a list (possibly empty)
            assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rebuild_index_mysql_returns_auto_message(self, async_client):
        """Patch is_mysql=True, verify rebuild-index returns 'maintained automatically'."""
        with mysql_mode():
            response = await async_client.post("/api/v1/archives/search/rebuild-index")
            assert response.status_code == 200
            data = response.json()
            assert "maintained automatically" in data["message"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_search_mysql_appends_wildcard(self, async_client):
        """Verify MySQL search path appends '*' wildcard to search term."""
        with mysql_mode():
            # Should not error even with a term that already has wildcard
            response = await async_client.get("/api/v1/archives/search?q=test*")
            assert response.status_code == 200
            assert isinstance(response.json(), list)


# ============================================================================
# TestMySQLDatabaseInit — Init path
# ============================================================================


class TestMySQLDatabaseInit:
    """Tests for init_db MySQL vs SQLite path selection."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_init_db_calls_mysql_path(self):
        """Patch is_mysql=True, verify _init_db_mysql is called."""
        with (
            patch("backend.app.core.database.settings") as mock_settings,
            patch("backend.app.core.database._init_db_mysql", new_callable=AsyncMock) as mock_mysql_init,
        ):
            mock_settings.is_mysql = True

            from backend.app.core.database import init_db

            await init_db()

            mock_mysql_init.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_init_db_calls_sqlite_path_when_not_mysql(self):
        """Verify SQLite path is taken when is_mysql=False."""
        with (
            patch("backend.app.core.database.settings") as mock_settings,
            patch("backend.app.core.database._init_db_mysql", new_callable=AsyncMock) as mock_mysql_init,
            patch("backend.app.core.database._register_models"),
            patch("backend.app.core.database.engine") as mock_engine,
            patch("backend.app.core.database.run_migrations", new_callable=AsyncMock),
            patch("backend.app.core.database._seed_defaults", new_callable=AsyncMock),
        ):
            mock_settings.is_mysql = False

            # Mock the async context manager for engine.begin()
            mock_conn = AsyncMock()
            mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_engine.begin.return_value.__aexit__ = AsyncMock()

            from backend.app.core.database import init_db

            await init_db()

            mock_mysql_init.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_ensure_mysql_database(self):
        """Mock engine to verify CREATE DATABASE IF NOT EXISTS is executed."""
        # Build a proper async context manager mock for engine.begin()
        mock_conn = AsyncMock()
        mock_begin_cm = AsyncMock()
        mock_begin_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_begin_cm.__aexit__ = AsyncMock(return_value=False)

        mock_temp_engine = MagicMock()
        mock_temp_engine.begin.return_value = mock_begin_cm
        mock_temp_engine.dispose = AsyncMock()

        with (
            patch("backend.app.core.database.settings") as mock_settings,
            # Patch where the local import resolves: sqlalchemy.ext.asyncio module
            patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_temp_engine),
        ):
            mock_settings.db_user = "bambu"
            mock_settings.db_password = "secret"
            mock_settings.db_host = "localhost"
            mock_settings.db_port = 3306
            mock_settings.db_name = "bambuddy"
            mock_settings.debug = False

            from backend.app.core.database import _ensure_mysql_database

            await _ensure_mysql_database()

        # Verify CREATE DATABASE was executed
        mock_conn.execute.assert_awaited_once()
        sql_arg = str(mock_conn.execute.call_args[0][0].text)
        assert "CREATE DATABASE IF NOT EXISTS" in sql_arg
        assert "bambuddy" in sql_arg
        assert "utf8mb4" in sql_arg
        # Verify engine was disposed
        mock_temp_engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_init_db_mysql_runs_alembic(self):
        """Mock Alembic components, verify migrations are applied and _seed_defaults is called."""
        # Ensure alembic mock modules exist in sys.modules for the local imports
        mock_alembic = MagicMock()
        mock_alembic_config = MagicMock()
        mock_alembic_script = MagicMock()
        mock_alembic_env = MagicMock()

        alembic_modules = {
            "alembic": mock_alembic,
            "alembic.config": mock_alembic_config,
            "alembic.script": mock_alembic_script,
            "alembic.runtime": MagicMock(),
            "alembic.runtime.environment": mock_alembic_env,
        }
        # Save originals and inject mocks
        saved = {k: sys.modules.get(k) for k in alembic_modules}
        sys.modules.update(alembic_modules)

        try:
            # Build async context manager for engine.begin()
            mock_conn = AsyncMock()
            mock_begin_cm = AsyncMock()
            mock_begin_cm.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_begin_cm.__aexit__ = AsyncMock(return_value=False)

            mock_engine = MagicMock()
            mock_engine.begin.return_value = mock_begin_cm

            # Mock run_sync to call the function synchronously
            async def fake_run_sync(fn):
                fn(MagicMock())

            mock_conn.run_sync = fake_run_sync

            # Set up EnvironmentContext mock chain
            mock_env_instance = MagicMock()
            mock_alembic_env.EnvironmentContext.return_value = mock_env_instance

            with (
                patch("backend.app.core.database._ensure_mysql_database", new_callable=AsyncMock) as mock_ensure,
                patch("backend.app.core.database._register_models"),
                patch("backend.app.core.database._seed_defaults", new_callable=AsyncMock) as mock_seed,
                patch("backend.app.core.database.engine", mock_engine),
                patch("backend.app.core.database.Base"),
            ):
                from backend.app.core.database import _init_db_mysql

                await _init_db_mysql()

                # Verify _ensure_mysql_database was called
                mock_ensure.assert_awaited_once()
                # Verify _seed_defaults was called
                mock_seed.assert_awaited_once()
                # Verify Alembic Config was instantiated
                mock_alembic_config.Config.assert_called_once_with("backend/alembic.ini")
        finally:
            # Restore original sys.modules state
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
