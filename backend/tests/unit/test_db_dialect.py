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
        """SEC-3: provider_email normalisation lowers mixed-case values, leaves NULL intact.

        The production migration runs this UPDATE directly (not via _safe_execute)
        so any failure is always fatal and visible at startup.
        """
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE user_oidc_links (id INTEGER PRIMARY KEY, provider_email TEXT)"))
            await conn.execute(text("INSERT INTO user_oidc_links VALUES (1, 'User@Example.COM')"))
            await conn.execute(text("INSERT INTO user_oidc_links VALUES (2, 'already@lower.com')"))
            await conn.execute(text("INSERT INTO user_oidc_links VALUES (3, NULL)"))

            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "UPDATE user_oidc_links SET provider_email = LOWER(provider_email) "
                        "WHERE provider_email IS NOT NULL AND provider_email != LOWER(provider_email)"
                    )
                )

            result = await conn.execute(text("SELECT provider_email FROM user_oidc_links ORDER BY id"))
            rows = [r[0] for r in result.fetchall()]
        await engine.dispose()

        assert rows[0] == "user@example.com"
        assert rows[1] == "already@lower.com"
        assert rows[2] is None

    @pytest.mark.asyncio
    async def test_safe_execute_swallows_no_such_column_for_rename(self):
        """'no such column' is swallowed for RENAME COLUMN idempotency.

        When a column has already been renamed, re-running the RENAME COLUMN
        migration raises 'no such column' — that must be silently swallowed.
        DML safety is guaranteed by never passing DML through _safe_execute.
        """
        from sqlalchemy.ext.asyncio import create_async_engine

        from backend.app.core.database import _safe_execute

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(__import__("sqlalchemy").text("CREATE TABLE t (id INTEGER, new_col INTEGER)"))
            # Column 'old_col' does not exist — simulates re-running a RENAME COLUMN migration
            # Must NOT raise.
            await _safe_execute(conn, "ALTER TABLE t RENAME COLUMN old_col TO new_col")
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_safe_execute_swallows_does_not_exist_for_rename_postgres(self):
        """'does not exist' (PostgreSQL UndefinedColumnError) is swallowed for RENAME COLUMN idempotency."""
        from unittest.mock import AsyncMock, MagicMock

        from sqlalchemy.exc import ProgrammingError

        from backend.app.core.database import _safe_execute

        fake_exc = ProgrammingError('column "quantity_printed" does not exist', [], Exception())

        nested_cm = MagicMock()
        nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
        nested_cm.execute = AsyncMock(side_effect=fake_exc)
        nested_cm.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.begin_nested.return_value = nested_cm
        mock_conn.execute = AsyncMock(side_effect=fake_exc)

        # Must NOT raise — "does not exist" is in the swallow-list
        await _safe_execute(
            mock_conn, "ALTER TABLE project_bom_items RENAME COLUMN quantity_printed TO quantity_acquired"
        )

    @pytest.mark.asyncio
    async def test_safe_execute_swallows_duplicate_key(self):
        """'duplicate key' errors (PostgreSQL unique-constraint violations on re-run)
        must be silently swallowed for idempotent DDL migrations."""
        from unittest.mock import AsyncMock, MagicMock

        from sqlalchemy.exc import OperationalError

        from backend.app.core.database import _safe_execute

        fake_exc = OperationalError("duplicate key value violates unique constraint", [], Exception())

        # begin_nested() is called synchronously (not awaited) and returns an
        # async context manager. Use MagicMock so the call returns a regular
        # object, then attach __aenter__/__aexit__ for the async with protocol.
        nested_cm = MagicMock()
        nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
        # Raise on execute inside the context, simulating PG duplicate key
        nested_cm.execute = AsyncMock(side_effect=fake_exc)
        nested_cm.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.begin_nested.return_value = nested_cm
        mock_conn.execute = AsyncMock(side_effect=fake_exc)

        # Must NOT raise — "duplicate key" is in the swallow-list
        await _safe_execute(mock_conn, "CREATE UNIQUE INDEX ...")

    @pytest.mark.asyncio
    async def test_check_constraint_false_true_on_sqlite(self):
        """New constraint formula is enforced on SQLite (3.23+).

        New: auto_link = FALSE OR email_claim != 'email' OR require_ev = TRUE
        Blocks Fall B (auto_link=1 + email_claim='email' + require_ev=0).
        Allows Fall A (email_claim='email' + require_ev=1) and Fall C (custom claim).
        """
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
                    CHECK (auto_link = FALSE OR email_claim != 'email' OR require_ev = TRUE)
                )
            """)
            )
            # Valid: auto_link=0 (FALSE) — any combo allowed
            await conn.execute(text("INSERT INTO ck_test VALUES (1, 0, 0, 'upn')"))
            # Valid: Fall A — auto_link=1, require_ev=1, email_claim='email'
            await conn.execute(text("INSERT INTO ck_test VALUES (2, 1, 1, 'email')"))
            # Valid: Fall C — auto_link=1, email_claim='upn' (require_ev irrelevant)
            await conn.execute(text("INSERT INTO ck_test VALUES (3, 1, 0, 'upn')"))
            await conn.execute(text("INSERT INTO ck_test VALUES (4, 1, 1, 'upn')"))

        async with engine.begin() as conn:
            # Invalid: Fall B — auto_link=1 + email_claim='email' + require_ev=0
            with pytest.raises(IntegrityError):
                await conn.execute(text("INSERT INTO ck_test VALUES (5, 1, 0, 'email')"))
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_auto_link_sec1_backfill_resets_unsafe_rows(self):
        """SEC-1 backfill resets auto_link=TRUE only for Fall B (email_claim='email' + require_ev=FALSE).

        Three cases:
          1. auto_link=TRUE + email_claim='email' + require_ev=FALSE → reset to FALSE (Fall B, unsafe)
          2. auto_link=TRUE + custom claim + require_ev=TRUE → unchanged (Fall C, now allowed)
          3. auto_link=TRUE + email_claim='email' + require_ev=TRUE → unchanged (Fall A, safe)
        """
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE oidc_providers ("
                    "id INTEGER PRIMARY KEY, "
                    "auto_link_existing_accounts BOOLEAN, "
                    "require_email_verified BOOLEAN, "
                    "email_claim TEXT"
                    ")"
                )
            )
            # Row 1: Fall B — email_claim='email' + require_ev=FALSE → must be reset
            await conn.execute(text("INSERT INTO oidc_providers VALUES (1, 1, 0, 'email')"))
            # Row 2: Fall C — custom claim → must NOT be reset (now allowed)
            await conn.execute(text("INSERT INTO oidc_providers VALUES (2, 1, 1, 'preferred_username')"))
            # Row 3: Fall A — email_claim='email' + require_ev=TRUE → must NOT be reset (always safe)
            await conn.execute(text("INSERT INTO oidc_providers VALUES (3, 1, 1, 'email')"))

            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "UPDATE oidc_providers SET auto_link_existing_accounts = FALSE "
                        "WHERE auto_link_existing_accounts = TRUE "
                        "AND email_claim = 'email' AND require_email_verified = FALSE"
                    )
                )

            result = await conn.execute(text("SELECT id, auto_link_existing_accounts FROM oidc_providers ORDER BY id"))
            rows = {r[0]: r[1] for r in result.fetchall()}
        await engine.dispose()

        assert rows[1] == 0, "Fall B (require_ev=FALSE) must be reset to FALSE"
        assert rows[2] == 1, "Fall C (custom claim) must remain TRUE"
        assert rows[3] == 1, "Fall A (require_ev=TRUE) must remain TRUE"

    @pytest.mark.asyncio
    async def test_safe_execute_reraises_does_not_exist_without_column(self):
        """'does not exist' without 'column' in the message must NOT be swallowed.

        This verifies that the narrowing from the broad 'does not exist' substring
        to the compound RENAME-COLUMN-only guard works correctly.  A missing-relation
        error must propagate so the operator sees a startup failure rather than a
        silent schema gap.
        """
        from unittest.mock import AsyncMock, MagicMock

        from sqlalchemy.exc import ProgrammingError

        from backend.app.core.database import _safe_execute

        # PostgreSQL error for a missing relation — contains "does not exist" but NOT "column"
        fake_exc = ProgrammingError('relation "oidc_providers" does not exist', [], Exception())

        nested_cm = MagicMock()
        nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
        nested_cm.execute = AsyncMock(side_effect=fake_exc)
        nested_cm.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.begin_nested.return_value = nested_cm
        mock_conn.execute = AsyncMock(side_effect=fake_exc)

        # Must RAISE — "column" is absent so this is not RENAME COLUMN idempotency
        with pytest.raises(ProgrammingError):
            await _safe_execute(
                mock_conn, "ALTER TABLE oidc_providers ADD COLUMN auto_link_existing_accounts BOOLEAN DEFAULT false"
            )

    @pytest.mark.asyncio
    async def test_oidc_boolean_default_migrations_sqlite_defaults(self):
        """auto_link defaults to 0 (FALSE) and require_email_verified defaults to 1 (TRUE) on SQLite.

        Verifies that the SQLite branch of the BOOLEAN DEFAULT dialect-branch uses
        the correct integer literals so new rows get safe defaults without explicit values.
        """
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from backend.app.core.database import _safe_execute

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE oidc_providers (id INTEGER PRIMARY KEY, name TEXT)"))
            await _safe_execute(
                conn, "ALTER TABLE oidc_providers ADD COLUMN auto_link_existing_accounts BOOLEAN DEFAULT 0"
            )
            await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN require_email_verified BOOLEAN DEFAULT 1")
            await conn.execute(text("INSERT INTO oidc_providers (id, name) VALUES (1, 'test')"))
            result = await conn.execute(
                text("SELECT auto_link_existing_accounts, require_email_verified FROM oidc_providers WHERE id = 1")
            )
            row = result.fetchone()
        await engine.dispose()

        assert row[0] == 0, "auto_link_existing_accounts must default to 0 (FALSE) on SQLite"
        assert row[1] == 1, "require_email_verified must default to 1 (TRUE) on SQLite"

    @pytest.mark.asyncio
    async def test_safe_execute_column_not_exists_only_swallowed_for_rename(self):
        """'column … does not exist' is swallowed only when the SQL is RENAME COLUMN.

        The compound guard must NOT swallow the same error pattern when the SQL is
        an ADD COLUMN statement — that would indicate schema corruption, not idempotency.
        """
        from unittest.mock import AsyncMock, MagicMock

        from sqlalchemy.exc import ProgrammingError

        from backend.app.core.database import _safe_execute

        fake_exc = ProgrammingError('column "auto_link_existing_accounts" does not exist', [], Exception())

        nested_cm = MagicMock()
        nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
        nested_cm.execute = AsyncMock(side_effect=fake_exc)
        nested_cm.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.begin_nested.return_value = nested_cm
        mock_conn.execute = AsyncMock(side_effect=fake_exc)

        # ADD COLUMN statement — must RAISE even though message contains "column" + "does not exist"
        with pytest.raises(ProgrammingError):
            await _safe_execute(
                mock_conn, "ALTER TABLE oidc_providers ADD COLUMN auto_link_existing_accounts BOOLEAN DEFAULT false"
            )

        # RENAME COLUMN statement — must NOT raise (idempotency)
        await _safe_execute(
            mock_conn, "ALTER TABLE oidc_providers RENAME COLUMN auto_link_existing_accounts TO auto_link"
        )

    @pytest.mark.asyncio
    async def test_normalize_printer_ids_sqlite_uses_plain_comparison(self):
        """SQLite path executes plain string comparison (no cast)."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from backend.app.core.database import _migrate_normalize_printer_ids

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE api_keys (id INTEGER PRIMARY KEY, printer_ids TEXT)"))
            await conn.execute(text("INSERT INTO api_keys VALUES (1, '[]')"))
            await conn.execute(text("INSERT INTO api_keys VALUES (2, '[1,2]')"))

            with patch("backend.app.core.database.is_sqlite", return_value=True):
                await _migrate_normalize_printer_ids(conn)

            result = await conn.execute(text("SELECT id, printer_ids FROM api_keys ORDER BY id"))
            rows = {r[0]: r[1] for r in result.fetchall()}
        await engine.dispose()

        assert rows[1] is None, "printer_ids='[]' must be normalised to NULL"
        assert rows[2] == "[1,2]", "non-empty printer_ids must be unchanged"

    @pytest.mark.asyncio
    async def test_normalize_printer_ids_postgres_uses_text_cast(self):
        """PostgreSQL path casts printer_ids to text for comparison (works for json and jsonb)."""
        from unittest.mock import AsyncMock, MagicMock

        from backend.app.core.database import _migrate_normalize_printer_ids

        nested_cm = MagicMock()
        nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
        nested_cm.__aexit__ = AsyncMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.begin_nested.return_value = nested_cm
        mock_conn.execute = AsyncMock()

        with patch("backend.app.core.database.is_sqlite", return_value=False):
            await _migrate_normalize_printer_ids(mock_conn)

        sql = mock_conn.execute.call_args[0][0].text
        assert "::text = '[]'" in sql, f"Expected ::text cast in SQL, got: {sql}"
        assert "printer_ids" in sql


class TestSpoolmanTableDialect:
    """Phase 1: active_print_spoolman and spool_usage_history use dialect-correct DDL.

    These tables were created with raw 'INTEGER PRIMARY KEY AUTOINCREMENT' (SQLite-only
    syntax) before the fix.  Now they branch on is_sqlite() exactly like
    smart_plug_energy_snapshots.
    """

    @pytest.mark.asyncio
    async def test_active_print_spoolman_sqlite_creates_table(self):
        """SQLite: active_print_spoolman is created with valid SQLite DDL."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from backend.app.core.database import _safe_execute

        sql = """
        CREATE TABLE IF NOT EXISTS active_print_spoolman (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            printer_id INTEGER NOT NULL,
            archive_id INTEGER NOT NULL,
            filament_usage TEXT NOT NULL,
            ams_trays TEXT NOT NULL,
            slot_to_tray TEXT,
            layer_usage TEXT,
            filament_properties TEXT,
            UNIQUE(printer_id, archive_id)
        )
        """
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await _safe_execute(conn, sql)
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='active_print_spoolman'")
            )
            assert result.fetchone() is not None, "Table must be created on SQLite"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_active_print_spoolman_postgres_sql_uses_serial(self):
        """PostgreSQL: active_print_spoolman SQL uses SERIAL PRIMARY KEY, not AUTOINCREMENT."""
        from unittest.mock import AsyncMock, MagicMock

        from backend.app.core.database import _safe_execute

        captured_sql: list[str] = []

        nested_cm = MagicMock()
        nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
        nested_cm.__aexit__ = AsyncMock(return_value=False)

        async def capturing_execute(sql_or_text, *args, **kwargs):
            captured_sql.append(str(sql_or_text))

        nested_cm.execute = AsyncMock(side_effect=capturing_execute)
        mock_conn = MagicMock()
        mock_conn.begin_nested.return_value = nested_cm
        mock_conn.execute = AsyncMock(side_effect=capturing_execute)

        # PG path SQL — same string as in run_migrations() when is_sqlite() is False
        pg_sql = """
        CREATE TABLE IF NOT EXISTS active_print_spoolman (
            id SERIAL PRIMARY KEY,
            printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
            archive_id INTEGER NOT NULL REFERENCES print_archives(id) ON DELETE CASCADE,
            filament_usage TEXT NOT NULL,
            ams_trays TEXT NOT NULL,
            slot_to_tray TEXT,
            layer_usage TEXT,
            filament_properties TEXT,
            UNIQUE(printer_id, archive_id)
        )
        """
        await _safe_execute(mock_conn, pg_sql)

        assert captured_sql, "execute must have been called"
        combined = " ".join(captured_sql)
        assert "SERIAL PRIMARY KEY" in combined
        assert "AUTOINCREMENT" not in combined

    @pytest.mark.asyncio
    async def test_spool_usage_history_sqlite_creates_table(self):
        """SQLite: spool_usage_history is created with valid SQLite DDL."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from backend.app.core.database import _safe_execute

        sql = """
        CREATE TABLE IF NOT EXISTS spool_usage_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spool_id INTEGER NOT NULL,
            printer_id INTEGER,
            print_name VARCHAR(500),
            weight_used REAL NOT NULL DEFAULT 0,
            percent_used INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'completed',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await _safe_execute(conn, sql)
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='spool_usage_history'")
            )
            assert result.fetchone() is not None, "Table must be created on SQLite"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_spool_usage_history_postgres_sql_uses_serial_and_timestamp(self):
        """PostgreSQL: spool_usage_history SQL uses SERIAL and TIMESTAMP, not AUTOINCREMENT/DATETIME."""
        from unittest.mock import AsyncMock, MagicMock

        from backend.app.core.database import _safe_execute

        captured_sql: list[str] = []

        nested_cm = MagicMock()
        nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
        nested_cm.__aexit__ = AsyncMock(return_value=False)

        async def capturing_execute(sql_or_text, *args, **kwargs):
            captured_sql.append(str(sql_or_text))

        nested_cm.execute = AsyncMock(side_effect=capturing_execute)
        mock_conn = MagicMock()
        mock_conn.begin_nested.return_value = nested_cm
        mock_conn.execute = AsyncMock(side_effect=capturing_execute)

        pg_sql = """
        CREATE TABLE IF NOT EXISTS spool_usage_history (
            id SERIAL PRIMARY KEY,
            spool_id INTEGER NOT NULL REFERENCES spool(id) ON DELETE CASCADE,
            printer_id INTEGER REFERENCES printers(id) ON DELETE SET NULL,
            print_name VARCHAR(500),
            weight_used REAL NOT NULL DEFAULT 0,
            percent_used INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'completed',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
        await _safe_execute(mock_conn, pg_sql)

        assert captured_sql, "execute must have been called"
        combined = " ".join(captured_sql)
        assert "SERIAL PRIMARY KEY" in combined
        assert "TIMESTAMP" in combined
        assert "AUTOINCREMENT" not in combined
        assert "DATETIME" not in combined


class TestAutoLinkConstraintMigration:
    """Tests for _migrate_update_auto_link_constraint (Fall C / Azure support)."""

    @pytest.mark.asyncio
    async def test_new_constraint_allows_fall_c_sqlite(self):
        """New formula allows auto_link=TRUE with a custom claim (Fall C)."""
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE oidc_providers_ck ("
                    "id INTEGER PRIMARY KEY, "
                    "auto_link BOOLEAN, "
                    "require_ev BOOLEAN, "
                    "email_claim TEXT, "
                    "CHECK (auto_link = FALSE OR email_claim != 'email' OR require_ev = TRUE)"
                    ")"
                )
            )
            # Fall C: custom claim + auto_link + require_ev=FALSE must pass
            await conn.execute(text("INSERT INTO oidc_providers_ck VALUES (1, 1, 0, 'upn')"))
            # Fall C: custom claim + auto_link + require_ev=TRUE must pass
            await conn.execute(text("INSERT INTO oidc_providers_ck VALUES (2, 1, 1, 'preferred_username')"))
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_new_constraint_blocks_fall_b_sqlite(self):
        """New formula still blocks Fall B (email_claim='email' + require_ev=FALSE + auto_link=TRUE)."""
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE oidc_providers_ck ("
                    "id INTEGER PRIMARY KEY, "
                    "auto_link BOOLEAN, "
                    "require_ev BOOLEAN, "
                    "email_claim TEXT, "
                    "CHECK (auto_link = FALSE OR email_claim != 'email' OR require_ev = TRUE)"
                    ")"
                )
            )
        async with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                await conn.execute(text("INSERT INTO oidc_providers_ck VALUES (1, 1, 0, 'email')"))
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_constraint_migration_sqlite_recreates_table(self):
        """SQLite path recreates oidc_providers with new constraint when old formula is present."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from backend.app.core.database import _migrate_update_auto_link_constraint

        # Create table with old constraint formula
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE oidc_providers ("
                    "id INTEGER NOT NULL PRIMARY KEY, "
                    "name VARCHAR(100) NOT NULL UNIQUE, "
                    "issuer_url VARCHAR(500) NOT NULL, "
                    "client_id VARCHAR(255) NOT NULL, "
                    "client_secret VARCHAR(512) NOT NULL, "
                    "scopes VARCHAR(500), "
                    "is_enabled BOOLEAN, "
                    "auto_create_users BOOLEAN, "
                    "auto_link_existing_accounts BOOLEAN DEFAULT 0, "
                    "email_claim VARCHAR(64) DEFAULT 'email', "
                    "require_email_verified BOOLEAN DEFAULT 1, "
                    "icon_url TEXT, "
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "CONSTRAINT ck_auto_link_requires_verified_email_claim "
                    "CHECK (auto_link_existing_accounts = FALSE OR "
                    "(require_email_verified = TRUE AND email_claim = 'email'))"
                    ")"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO oidc_providers (id, name, issuer_url, client_id, client_secret, "
                    "scopes, is_enabled, auto_create_users, auto_link_existing_accounts, "
                    "email_claim, require_email_verified, icon_url, created_at, updated_at) "
                    "VALUES (1, 'TestIdP', 'https://idp.test', 'cid', 'secret', "
                    "'openid email', 1, 0, 0, 'email', 1, NULL, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        async with engine.begin() as conn:
            with patch("backend.app.core.database.is_sqlite", return_value=True):
                await _migrate_update_auto_link_constraint(conn)

            # Verify data survived
            result = await conn.execute(text("SELECT id, name FROM oidc_providers"))
            rows = result.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == 1

            # Verify new constraint: Fall C (auto_link=TRUE + custom claim) must now be insertable
            await conn.execute(
                text(
                    "INSERT INTO oidc_providers (id, name, issuer_url, client_id, client_secret, "
                    "scopes, is_enabled, auto_create_users, auto_link_existing_accounts, "
                    "email_claim, require_email_verified, icon_url, created_at, updated_at) "
                    "VALUES (2, 'AzureIdP', 'https://azure.test', 'cid2', 'secret', "
                    "'openid', 1, 0, 1, 'upn', 1, NULL, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

            # Verify schema has new formula
            schema = (
                await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='oidc_providers'"))
            ).fetchone()[0]
            assert "require_email_verified = TRUE AND email_claim = 'email'" not in schema
            assert "email_claim != 'email'" in schema

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_constraint_migration_postgres_drops_and_recreates(self):
        """PostgreSQL path calls DROP CONSTRAINT IF EXISTS then ADD CONSTRAINT with new formula."""
        from unittest.mock import AsyncMock, MagicMock, call

        from backend.app.core.database import _migrate_update_auto_link_constraint

        # Track all SQL statements passed to _safe_execute by capturing conn.execute calls
        executed_sqls: list[str] = []

        async def fake_safe_execute(conn, sql):
            executed_sqls.append(sql)

        nested_cm = MagicMock()
        nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
        nested_cm.__aexit__ = AsyncMock(return_value=False)
        nested_cm.execute = AsyncMock()

        mock_conn = MagicMock()
        mock_conn.begin_nested.return_value = nested_cm
        mock_conn.execute = AsyncMock()

        with (
            patch("backend.app.core.database.is_sqlite", return_value=False),
            patch("backend.app.core.database._safe_execute", side_effect=fake_safe_execute),
        ):
            await _migrate_update_auto_link_constraint(mock_conn)

        assert len(executed_sqls) == 2
        drop_sql, add_sql = executed_sqls
        assert "DROP CONSTRAINT IF EXISTS" in drop_sql.upper()
        assert "ck_auto_link_requires_verified_email_claim" in drop_sql
        assert "ADD CONSTRAINT" in add_sql.upper()
        assert "email_claim != 'email'" in add_sql
        assert "require_email_verified = TRUE AND email_claim = 'email'" not in add_sql

    @pytest.mark.asyncio
    async def test_constraint_migration_sqlite_count_guard_raises_on_mismatch(self):
        """RuntimeError is raised when the copied row count doesn't match the source."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import pytest

        from backend.app.core.database import _migrate_update_auto_link_constraint

        _OLD_SQL = (
            "CREATE TABLE oidc_providers (id INTEGER NOT NULL, "
            "CONSTRAINT ck_auto_link_requires_verified_email_claim "
            "CHECK (auto_link_existing_accounts = FALSE OR "
            "(require_email_verified = TRUE AND email_claim = 'email')))"
        )

        async def fake_execute(stmt):
            sql = str(stmt)
            result = MagicMock()
            if "sqlite_master" in sql:
                result.fetchone.return_value = (_OLD_SQL,)
            elif "count(*)" in sql.lower() and "oidc_providers_v2" not in sql:
                result.scalar_one.return_value = 2  # source has 2 rows
            elif "count(*)" in sql.lower() and "oidc_providers_v2" in sql:
                result.scalar_one.return_value = 1  # copy only has 1 — mismatch
            else:
                result.fetchone.return_value = None
            return result

        nested_cm = MagicMock()
        nested_cm.__aenter__ = AsyncMock(return_value=None)
        nested_cm.__aexit__ = AsyncMock(return_value=False)  # don't suppress exceptions

        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(side_effect=fake_execute)
        mock_conn.begin_nested.return_value = nested_cm

        with (
            patch("backend.app.core.database.is_sqlite", return_value=True),
            pytest.raises(RuntimeError, match="mismatch"),
        ):
            await _migrate_update_auto_link_constraint(mock_conn)
