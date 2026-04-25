import asyncio
import logging

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.app.core.config import settings
from backend.app.core.db_dialect import is_sqlite

logger = logging.getLogger(__name__)


def _set_sqlite_pragmas(dbapi_conn, connection_record):
    """Set SQLite pragmas on each new connection for concurrency and performance."""
    cursor = dbapi_conn.cursor()
    # WAL mode allows concurrent readers + one writer (vs default DELETE mode which locks entirely)
    cursor.execute("PRAGMA journal_mode = WAL")
    # Wait up to 15 seconds when the database is locked instead of failing immediately
    cursor.execute("PRAGMA busy_timeout = 15000")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.close()


def _create_engine():
    """Create the async engine with dialect-appropriate settings."""
    if is_sqlite():
        kwargs = {"pool_size": 20, "max_overflow": 200}
    else:
        kwargs = {"pool_size": 10, "max_overflow": 20}
    eng = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        **kwargs,
    )
    if is_sqlite():
        event.listen(eng.sync_engine, "connect", _set_sqlite_pragmas)
    else:
        # Strip timezone info from aware datetimes before they reach asyncpg.
        # asyncpg rejects timezone-aware values for TIMESTAMP WITHOUT TIME ZONE columns.
        # The codebase uses datetime.now(timezone.utc) in many places — this makes
        # Postgres behave like SQLite which ignores timezone info entirely.
        @event.listens_for(eng.sync_engine, "before_cursor_execute", retval=True)
        def _strip_tz_from_params(conn, cursor, statement, parameters, context, executemany):
            import datetime

            if parameters is None:
                return statement, parameters

            # Recursive strip that walks any nesting of dict/list/tuple. Needed
            # because SQLAlchemy passes parameters in several shapes depending
            # on the path: a dict for named binds, a tuple for positional, a
            # list of dicts/tuples for executemany, and for insertmanyvalues
            # sometimes a list of tuples inside an outer list. The simplest
            # correct answer is "strip datetimes at any depth".
            def _strip(val):
                if isinstance(val, datetime.datetime) and val.tzinfo is not None:
                    return val.replace(tzinfo=None)
                if isinstance(val, dict):
                    return {k: _strip(v) for k, v in val.items()}
                if isinstance(val, list):
                    return [_strip(v) for v in val]
                if isinstance(val, tuple):
                    return tuple(_strip(v) for v in val)
                return val

            return statement, _strip(parameters)

    return eng


engine = _create_engine()

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def run_with_retry(fn, *, max_attempts: int = 3, label: str = ""):
    """Run an async DB operation with retry for SQLite 'database is locked' errors.

    ``fn`` is an async callable that receives an ``AsyncSession`` and performs
    the full query-mutate-commit cycle.  On each retry a fresh session is used
    so there are no stale-object / expired-attribute issues after rollback.

    On PostgreSQL this calls ``fn`` once with no retry (Postgres uses row-level
    locking and doesn't suffer from single-writer contention).
    """
    if not is_sqlite():
        async with async_session() as db:
            return await fn(db)

    last_exc: OperationalError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with async_session() as db:
                return await fn(db)
        except OperationalError as exc:
            last_exc = exc
            if "database is locked" not in str(exc) or attempt == max_attempts:
                raise
            delay = 0.5 * attempt  # 0.5s, 1.0s
            logger.warning(
                "SQLite locked%s (attempt %d/%d), retrying in %.1fs: %s",
                f" ({label})" if label else "",
                attempt,
                max_attempts,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    raise last_exc  # unreachable, but keeps type checkers happy


async def close_all_connections():
    """Close all database connections for backup/restore operations."""
    global engine
    await engine.dispose()


async def reinitialize_database():
    """Reinitialize database connection after restore."""
    global engine, async_session
    engine = _create_engine()
    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    # Import models to register them with SQLAlchemy
    from backend.app.models import (  # noqa: F401
        active_print_spoolman,
        ams_history,
        ams_label,
        api_key,
        archive,
        auth_ephemeral,
        bug_report,
        color_catalog,
        external_link,
        filament,
        github_backup,
        group,
        kprofile_note,
        library,
        local_preset,
        long_lived_token,
        maintenance,
        notification,
        notification_template,
        oidc_provider,
        orca_base_cache,
        pending_upload,
        print_batch,
        print_log,
        print_queue,
        printer,
        project,
        project_bom,
        settings,
        slot_preset,
        smart_plug,
        smart_plug_energy_snapshot,
        spool,
        spool_assignment,
        spool_catalog,
        spool_k_profile,
        spool_usage_history,
        spoolbuddy_device,
        user,
        user_email_pref,
        user_otp_code,
        user_totp,
        virtual_printer,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Run migrations for new columns (SQLite doesn't auto-add columns)
        await run_migrations(conn)

    # Seed default notification templates
    await seed_notification_templates()

    # Seed default groups and migrate existing users
    await seed_default_groups()

    # Seed default catalog entries
    await seed_spool_catalog()
    await seed_color_catalog()


async def _safe_execute(conn, sql):
    """Execute a DDL migration statement, silently ignoring idempotency errors.

    'already exists', 'duplicate column name' (SQLite ADD COLUMN), 'no such column'
    (SQLite RENAME COLUMN), 'duplicate key', and the compound
    'column … does not exist' (PostgreSQL RENAME COLUMN idempotency) are swallowed
    so that re-running DDL migrations is safe.  The compound check additionally
    requires the SQL to be a RENAME COLUMN statement so that "does not exist" errors
    from ADD COLUMN or CREATE INDEX (which would indicate schema corruption, not
    idempotency) are never silently swallowed.
    Any other error is logged and re-raised — callers must not assume silent
    recovery, as a failure will abort the migration sequence and prevent
    application startup.

    Only use for DDL statements (ALTER TABLE, CREATE INDEX, etc.).
    For DML backfills (UPDATE, DELETE) use conn.execute() directly inside
    async with conn.begin_nested() so failures are never silently swallowed.

    Uses a savepoint so that a failed statement doesn't poison the surrounding
    transaction (required for PostgreSQL).
    """
    from sqlalchemy import text

    try:
        async with conn.begin_nested():
            await conn.execute(text(sql))
    except (OperationalError, ProgrammingError) as exc:
        msg = str(exc).lower()
        # Only swallow "column … does not exist" for RENAME COLUMN — not for ADD COLUMN
        # or CREATE INDEX where it would indicate schema corruption, not idempotency.
        column_not_exists = "rename column" in sql.lower() and "column" in msg and "does not exist" in msg
        if (
            not any(k in msg for k in ("already exists", "duplicate key", "duplicate column name", "no such column"))
            and not column_not_exists
        ):
            logger.error("Migration statement failed: %s | SQL: %.200s", exc, sql)
            raise


async def run_migrations(conn):
    """Run all schema migrations and data backfills on startup.

    Includes ALTER TABLE (add columns, rename columns, add constraints),
    CREATE INDEX, CREATE TRIGGER, data UPDATE backfills, and table recreations
    for complex SQLite schema changes that ALTER TABLE cannot handle.

    DDL statements are wrapped in _safe_execute for idempotency.
    DML backfills (UPDATE/DELETE) are executed directly via conn.execute()
    inside begin_nested() so any failure is always fatal and never silently
    swallowed.
    """
    from sqlalchemy import text

    # Migration: Add is_favorite column to print_archives
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN is_favorite BOOLEAN DEFAULT 0")

    # Migration: Add content_hash column to print_archives for duplicate detection
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN content_hash VARCHAR(64)")

    # Migration: Add auto_off_executed column to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN auto_off_executed BOOLEAN DEFAULT 0")

    # Migration: Add on_print_stopped column to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_print_stopped BOOLEAN DEFAULT 1")

    # Migration: Add source_3mf_path column to print_archives
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN source_3mf_path VARCHAR(500)")

    # Migration: Add f3d_path column to print_archives for Fusion 360 design files
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN f3d_path VARCHAR(500)")

    # Migration: Add on_maintenance_due column to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_maintenance_due BOOLEAN DEFAULT 0")

    # Migration: Add location column to printers for grouping
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN location VARCHAR(100)")

    # Migration: Add interval_type column to maintenance_types
    await _safe_execute(conn, "ALTER TABLE maintenance_types ADD COLUMN interval_type VARCHAR(20) DEFAULT 'hours'")

    # Migration: Add is_deleted column to maintenance_types for soft-deletes
    await _safe_execute(conn, "ALTER TABLE maintenance_types ADD COLUMN is_deleted BOOLEAN DEFAULT 0")

    # Migration: Add custom_interval_type column to printer_maintenance
    await _safe_execute(conn, "ALTER TABLE printer_maintenance ADD COLUMN custom_interval_type VARCHAR(20)")

    # Migration: Add power alert columns to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN power_alert_enabled BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN power_alert_high REAL")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN power_alert_low REAL")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN power_alert_last_triggered DATETIME")

    # Migration: Add schedule columns to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN schedule_enabled BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN schedule_on_time VARCHAR(5)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN schedule_off_time VARCHAR(5)")

    # Migration: Add daily digest columns to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN daily_digest_enabled BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN daily_digest_time VARCHAR(5)")

    # Migration: Add missing-spool-assignment print-start notification toggle
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE notification_providers ADD COLUMN on_print_missing_spool_assignment BOOLEAN DEFAULT 0"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add project_id column to print_archives
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE print_archives ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add project_id column to print_queue
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE print_queue ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Enforce uniqueness on user_oidc_links for existing rows.
    # create_all() is idempotent and does not add constraints to existing tables,
    # so we create covering unique indexes explicitly here.
    await _safe_execute(
        conn,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_oidc_link_provider_sub"
        " ON user_oidc_links (provider_id, provider_user_id)",
    )
    await _safe_execute(
        conn,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_oidc_link_user_provider ON user_oidc_links (user_id, provider_id)",
    )

    # Migration: Create FTS5 virtual table for archive full-text search (SQLite only)
    # PostgreSQL uses tsvector + GIN index instead (set up in archives.py search route)
    if is_sqlite():
        try:
            await conn.execute(
                text("""
                CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5(
                    print_name,
                    filename,
                    tags,
                    notes,
                    designer,
                    filament_type,
                    content='print_archives',
                    content_rowid='id'
                )
            """)
            )
        except (OperationalError, ProgrammingError):
            pass  # Already applied

        # Migration: Create triggers to keep FTS index in sync
        try:
            await conn.execute(
                text("""
                CREATE TRIGGER IF NOT EXISTS archive_fts_insert AFTER INSERT ON print_archives BEGIN
                    INSERT INTO archive_fts(rowid, print_name, filename, tags, notes, designer, filament_type)
                    VALUES (new.id, new.print_name, new.filename, new.tags, new.notes, new.designer, new.filament_type);
                END
            """)
            )
        except (OperationalError, ProgrammingError):
            pass  # Already applied

        try:
            await conn.execute(
                text("""
                CREATE TRIGGER IF NOT EXISTS archive_fts_delete AFTER DELETE ON print_archives BEGIN
                    INSERT INTO archive_fts(archive_fts, rowid, print_name, filename, tags, notes, designer, filament_type)
                    VALUES ('delete', old.id, old.print_name, old.filename, old.tags, old.notes, old.designer, old.filament_type);
                END
            """)
            )
        except (OperationalError, ProgrammingError):
            pass  # Already applied

        try:
            await conn.execute(
                text("""
                CREATE TRIGGER IF NOT EXISTS archive_fts_update AFTER UPDATE ON print_archives BEGIN
                    INSERT INTO archive_fts(archive_fts, rowid, print_name, filename, tags, notes, designer, filament_type)
                    VALUES ('delete', old.id, old.print_name, old.filename, old.tags, old.notes, old.designer, old.filament_type);
                    INSERT INTO archive_fts(rowid, print_name, filename, tags, notes, designer, filament_type)
                    VALUES (new.id, new.print_name, new.filename, new.tags, new.notes, new.designer, new.filament_type);
                END
            """)
            )
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Add auto_off_pending columns to smart_plugs (for restart recovery)
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN auto_off_pending BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN auto_off_pending_since DATETIME")

    # Migration: Add auto_off_persistent column to smart_plugs (keep auto-off enabled between prints)
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN auto_off_persistent BOOLEAN DEFAULT 0")

    # Migration: Add AMS alarm notification columns to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_ams_humidity_high BOOLEAN DEFAULT 0")
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE notification_providers ADD COLUMN on_ams_temperature_high BOOLEAN DEFAULT 0")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add AMS-HT alarm notification columns to notification_providers
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE notification_providers ADD COLUMN on_ams_ht_humidity_high BOOLEAN DEFAULT 0")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE notification_providers ADD COLUMN on_ams_ht_temperature_high BOOLEAN DEFAULT 0")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add plate not empty notification column to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_plate_not_empty BOOLEAN DEFAULT 1")

    # Migration: Add notes column to projects (Phase 2)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN notes TEXT")

    # Migration: Add attachments column to projects (Phase 3)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN attachments JSON")

    # Migration: Add tags column to projects (Phase 4)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN tags TEXT")

    # Migration: Add due_date column to projects (Phase 5)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN due_date DATETIME")

    # Migration: Add priority column to projects (Phase 5)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN priority VARCHAR(20) DEFAULT 'normal'")

    # Migration: Add budget column to projects (Phase 6)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN budget REAL")

    # Migration: Add is_template column to projects (Phase 8)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN is_template BOOLEAN DEFAULT 0")

    # Migration: Add template_source_id column to projects (Phase 8)
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN template_source_id INTEGER")

    # Migration: Add parent_id column to projects (Phase 10)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE projects ADD COLUMN parent_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Rename quantity_printed to quantity_acquired in project_bom_items
    await _safe_execute(conn, "ALTER TABLE project_bom_items RENAME COLUMN quantity_printed TO quantity_acquired")

    # Migration: Add unit_price column to project_bom_items
    await _safe_execute(conn, "ALTER TABLE project_bom_items ADD COLUMN unit_price REAL")

    # Migration: Add sourcing_url column to project_bom_items
    await _safe_execute(conn, "ALTER TABLE project_bom_items ADD COLUMN sourcing_url VARCHAR(512)")

    # Migration: Rename notes to remarks in project_bom_items
    await _safe_execute(conn, "ALTER TABLE project_bom_items RENAME COLUMN notes TO remarks")

    # Migration: Add show_in_switchbar column to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN show_in_switchbar BOOLEAN DEFAULT 0")

    # Migration: Add runtime tracking columns to printers
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN runtime_seconds INTEGER DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN last_runtime_update DATETIME")

    # Migration: Add quantity column to print_archives for tracking item count
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN quantity INTEGER DEFAULT 1")

    # Migration: Add manual_start column to print_queue for staged prints
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN manual_start BOOLEAN DEFAULT 0")

    # Migration: Add wiki_url column to maintenance_types for documentation links
    await _safe_execute(conn, "ALTER TABLE maintenance_types ADD COLUMN wiki_url VARCHAR(500)")

    # Migration: Add tailscale_disabled column to virtual_printers. Opt-in: default TRUE so
    # the auto-detect + fallback noise only runs for users who explicitly enable it.
    # Postgres rejects `DEFAULT 1` for BOOLEAN (#1070 round-2 review).
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE virtual_printers ADD COLUMN tailscale_disabled BOOLEAN DEFAULT 1")
    else:
        await _safe_execute(conn, "ALTER TABLE virtual_printers ADD COLUMN tailscale_disabled BOOLEAN DEFAULT true")

    # Migration: Add ams_mapping column to print_queue for storing filament slot assignments
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN ams_mapping TEXT")

    # Migration: Add target_parts_count column to projects for tracking total parts needed
    await _safe_execute(conn, "ALTER TABLE projects ADD COLUMN target_parts_count INTEGER")

    # Migration: Make printer_id nullable in print_queue for unassigned queue items
    # SQLite doesn't support ALTER COLUMN, so we need to recreate the table
    # PostgreSQL gets the correct schema from create_all(), so skip this
    if is_sqlite():
        try:
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='print_queue'"))
            row = result.fetchone()
            if row and "printer_id INTEGER NOT NULL" in (row[0] or ""):
                await conn.execute(
                    text("""
                    CREATE TABLE print_queue_new (
                        id INTEGER PRIMARY KEY,
                        printer_id INTEGER REFERENCES printers(id) ON DELETE CASCADE,
                        archive_id INTEGER NOT NULL REFERENCES print_archives(id) ON DELETE CASCADE,
                        project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                        position INTEGER DEFAULT 0,
                        scheduled_time DATETIME,
                        manual_start BOOLEAN DEFAULT 0,
                        require_previous_success BOOLEAN DEFAULT 0,
                        auto_off_after BOOLEAN DEFAULT 0,
                        ams_mapping TEXT,
                        status VARCHAR(20) DEFAULT 'pending',
                        started_at DATETIME,
                        completed_at DATETIME,
                        error_message TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                )
                await conn.execute(
                    text("""
                    INSERT INTO print_queue_new
                    SELECT id, printer_id, archive_id, project_id, position, scheduled_time,
                           manual_start, require_previous_success, auto_off_after, ams_mapping,
                           status, started_at, completed_at, error_message, created_at
                    FROM print_queue
                """)
                )
                await conn.execute(text("DROP TABLE print_queue"))
                await conn.execute(text("ALTER TABLE print_queue_new RENAME TO print_queue"))
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Add plug_type column to smart_plugs for HA integration
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN plug_type VARCHAR(20) DEFAULT 'tasmota'")

    # Migration: Add ha_entity_id column to smart_plugs for HA integration
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN ha_entity_id VARCHAR(100)")

    # Migration: Add project_id column to library_folders for linking folders to projects
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE library_folders ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add archive_id column to library_folders for linking folders to archives
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE library_folders ADD COLUMN archive_id INTEGER REFERENCES print_archives(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Make ip_address nullable for HA plugs (SQLite requires table recreation)
    # PostgreSQL gets the correct schema from create_all(), so skip this
    if is_sqlite():
        try:
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='smart_plugs'"))
            row = result.fetchone()
            if row and "ip_address VARCHAR(45) NOT NULL" in (row[0] or ""):
                await conn.execute(
                    text("""
                    CREATE TABLE smart_plugs_new (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR(100) NOT NULL,
                        ip_address VARCHAR(45),
                        plug_type VARCHAR(20) DEFAULT 'tasmota',
                        ha_entity_id VARCHAR(100),
                        printer_id INTEGER UNIQUE REFERENCES printers(id) ON DELETE SET NULL,
                        enabled BOOLEAN NOT NULL DEFAULT 1,
                        auto_on BOOLEAN NOT NULL DEFAULT 1,
                        auto_off BOOLEAN NOT NULL DEFAULT 1,
                        auto_off_persistent BOOLEAN NOT NULL DEFAULT 0,
                        off_delay_mode VARCHAR(20) NOT NULL DEFAULT 'time',
                        off_delay_minutes INTEGER NOT NULL DEFAULT 5,
                        off_temp_threshold INTEGER NOT NULL DEFAULT 70,
                        username VARCHAR(50),
                        password VARCHAR(100),
                        power_alert_enabled BOOLEAN NOT NULL DEFAULT 0,
                        power_alert_high FLOAT,
                        power_alert_low FLOAT,
                        power_alert_last_triggered DATETIME,
                        schedule_enabled BOOLEAN NOT NULL DEFAULT 0,
                        schedule_on_time VARCHAR(5),
                        schedule_off_time VARCHAR(5),
                        show_in_switchbar BOOLEAN DEFAULT 0,
                        last_state VARCHAR(10),
                        last_checked DATETIME,
                        auto_off_executed BOOLEAN NOT NULL DEFAULT 0,
                        auto_off_pending BOOLEAN DEFAULT 0,
                        auto_off_pending_since DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                    )
                """)
                )
                await conn.execute(
                    text("""
                    INSERT INTO smart_plugs_new
                    SELECT id, name, ip_address,
                           COALESCE(plug_type, 'tasmota'), ha_entity_id, printer_id,
                           enabled, auto_on, auto_off, COALESCE(auto_off_persistent, 0),
                           off_delay_mode, off_delay_minutes, off_temp_threshold,
                           username, password, power_alert_enabled, power_alert_high, power_alert_low,
                           power_alert_last_triggered, schedule_enabled, schedule_on_time, schedule_off_time,
                           COALESCE(show_in_switchbar, 0), last_state, last_checked, auto_off_executed,
                           COALESCE(auto_off_pending, 0), auto_off_pending_since, created_at, updated_at
                    FROM smart_plugs
                """)
                )
                await conn.execute(text("DROP TABLE smart_plugs"))
                await conn.execute(text("ALTER TABLE smart_plugs_new RENAME TO smart_plugs"))
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Add plate_id column to print_queue for multi-plate 3MF support
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN plate_id INTEGER")

    # Migration: Add print options columns to print_queue
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN bed_levelling BOOLEAN DEFAULT 1")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN flow_cali BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN vibration_cali BOOLEAN DEFAULT 1")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN layer_inspect BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN timelapse BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN use_ams BOOLEAN DEFAULT 1")

    # Migration: Add library_file_id column to print_queue and make archive_id nullable
    # This allows queue items to reference library files directly (archive created at print start)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE print_queue ADD COLUMN library_file_id INTEGER REFERENCES library_files(id) ON DELETE CASCADE"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Check if archive_id needs to be made nullable (requires table recreation in SQLite)
    # PostgreSQL gets the correct schema from create_all(), so skip this
    if is_sqlite():
        try:
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='print_queue'"))
            row = result.fetchone()
            if row and "archive_id INTEGER NOT NULL" in (row[0] or ""):
                await conn.execute(
                    text("""
                    CREATE TABLE print_queue_new2 (
                        id INTEGER PRIMARY KEY,
                        printer_id INTEGER REFERENCES printers(id) ON DELETE CASCADE,
                        archive_id INTEGER REFERENCES print_archives(id) ON DELETE CASCADE,
                        library_file_id INTEGER REFERENCES library_files(id) ON DELETE CASCADE,
                        project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                        position INTEGER DEFAULT 0,
                        scheduled_time DATETIME,
                        manual_start BOOLEAN DEFAULT 0,
                        require_previous_success BOOLEAN DEFAULT 0,
                        auto_off_after BOOLEAN DEFAULT 0,
                        ams_mapping TEXT,
                        plate_id INTEGER,
                        bed_levelling BOOLEAN DEFAULT 1,
                        flow_cali BOOLEAN DEFAULT 0,
                        vibration_cali BOOLEAN DEFAULT 1,
                        layer_inspect BOOLEAN DEFAULT 0,
                        timelapse BOOLEAN DEFAULT 0,
                        use_ams BOOLEAN DEFAULT 1,
                        status VARCHAR(20) DEFAULT 'pending',
                        started_at DATETIME,
                        completed_at DATETIME,
                        error_message TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                )
                await conn.execute(
                    text("""
                    INSERT INTO print_queue_new2
                    SELECT id, printer_id, archive_id, NULL, project_id, position, scheduled_time,
                           manual_start, require_previous_success, auto_off_after, ams_mapping, plate_id,
                           COALESCE(bed_levelling, 1), COALESCE(flow_cali, 0), COALESCE(vibration_cali, 1),
                           COALESCE(layer_inspect, 0), COALESCE(timelapse, 0), COALESCE(use_ams, 1),
                           status, started_at, completed_at, error_message, created_at
                    FROM print_queue
                """)
                )
                await conn.execute(text("DROP TABLE print_queue"))
                await conn.execute(text("ALTER TABLE print_queue_new2 RENAME TO print_queue"))
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Add HA energy sensor entity columns to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN ha_power_entity VARCHAR(100)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN ha_energy_today_entity VARCHAR(100)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN ha_energy_total_entity VARCHAR(100)")

    # Migration: Create users table for authentication
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username VARCHAR(100) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL DEFAULT 'user',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_username ON users(username)"))
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add external camera columns to printers
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN external_camera_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN external_camera_type VARCHAR(20)")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN external_camera_enabled BOOLEAN DEFAULT 0")

    # Migration: Add external_url column to print_archives for user-defined links (Printables, etc.)
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN external_url VARCHAR(500)")

    # Migration: Add sliced_for_model column to print_archives for model-based queue assignment
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN sliced_for_model VARCHAR(50)")

    # Migration: Add is_external column to library_files for external cloud files
    await _safe_execute(conn, "ALTER TABLE library_files ADD COLUMN is_external BOOLEAN DEFAULT 0")

    # Migration: Add project_id column to library_files
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE library_files ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add is_external column to library_folders for external cloud folders
    await _safe_execute(conn, "ALTER TABLE library_folders ADD COLUMN is_external BOOLEAN DEFAULT 0")

    # Migration: Add external folder settings columns to library_folders
    await _safe_execute(conn, "ALTER TABLE library_folders ADD COLUMN external_readonly BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE library_folders ADD COLUMN external_show_hidden BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE library_folders ADD COLUMN external_path VARCHAR(500)")

    # Migration: Add plate_detection_enabled column to printers
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN plate_detection_enabled BOOLEAN DEFAULT 0")

    # Migration: Add plate detection ROI columns to printers
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN plate_detection_roi_x REAL")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN plate_detection_roi_y REAL")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN plate_detection_roi_w REAL")
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN plate_detection_roi_h REAL")

    # Migration: Remove UNIQUE constraint from smart_plugs.printer_id
    # This allows HA scripts to coexist with regular plugs (scripts are for multi-device control)
    # SQLite requires table recreation to drop constraints
    # PostgreSQL gets the correct schema from create_all(), so skip this
    if is_sqlite():
        try:
            needs_migration = False
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='smart_plugs'"))
            row = result.fetchone()
            table_sql = (row[0] or "").upper() if row else ""
            if "PRINTER_ID" in table_sql and "UNIQUE" in table_sql:
                import re

                if re.search(r'"?PRINTER_ID"?\s+\w+\s+UNIQUE', table_sql) or re.search(
                    r'UNIQUE\s*\([^)]*"?PRINTER_ID"?', table_sql
                ):
                    needs_migration = True
            idx_result = await conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='smart_plugs' AND sql IS NOT NULL")
            )
            for idx_row in idx_result.fetchall():
                idx_sql = (idx_row[0] or "").upper()
                if "UNIQUE" in idx_sql and "PRINTER_ID" in idx_sql:
                    needs_migration = True
                    break
            if needs_migration:
                # Create new table without UNIQUE constraint on printer_id
                await conn.execute(
                    text("""
                    CREATE TABLE smart_plugs_temp (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR(100) NOT NULL,
                        ip_address VARCHAR(45),
                        plug_type VARCHAR(20) DEFAULT 'tasmota',
                        ha_entity_id VARCHAR(100),
                        ha_power_entity VARCHAR(100),
                        ha_energy_today_entity VARCHAR(100),
                        ha_energy_total_entity VARCHAR(100),
                        printer_id INTEGER REFERENCES printers(id) ON DELETE SET NULL,
                        enabled BOOLEAN NOT NULL DEFAULT 1,
                        auto_on BOOLEAN NOT NULL DEFAULT 1,
                        auto_off BOOLEAN NOT NULL DEFAULT 1,
                        auto_off_persistent BOOLEAN NOT NULL DEFAULT 0,
                        off_delay_mode VARCHAR(20) NOT NULL DEFAULT 'time',
                        off_delay_minutes INTEGER NOT NULL DEFAULT 5,
                        off_temp_threshold INTEGER NOT NULL DEFAULT 70,
                        username VARCHAR(50),
                        password VARCHAR(100),
                        power_alert_enabled BOOLEAN NOT NULL DEFAULT 0,
                        power_alert_high FLOAT,
                        power_alert_low FLOAT,
                        power_alert_last_triggered DATETIME,
                        schedule_enabled BOOLEAN NOT NULL DEFAULT 0,
                        schedule_on_time VARCHAR(5),
                        schedule_off_time VARCHAR(5),
                        show_in_switchbar BOOLEAN DEFAULT 0,
                        last_state VARCHAR(10),
                        last_checked DATETIME,
                        auto_off_executed BOOLEAN NOT NULL DEFAULT 0,
                        auto_off_pending BOOLEAN DEFAULT 0,
                        auto_off_pending_since DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                    )
                """)
                )
                # Copy data
                await conn.execute(
                    text("""
                    INSERT INTO smart_plugs_temp
                    SELECT id, name, ip_address, plug_type, ha_entity_id, ha_power_entity,
                           ha_energy_today_entity, ha_energy_total_entity, printer_id, enabled,
                           auto_on, auto_off, COALESCE(auto_off_persistent, 0),
                           off_delay_mode, off_delay_minutes, off_temp_threshold,
                           username, password, power_alert_enabled, power_alert_high, power_alert_low,
                           power_alert_last_triggered, schedule_enabled, schedule_on_time, schedule_off_time,
                           show_in_switchbar, last_state, last_checked, auto_off_executed,
                           auto_off_pending, auto_off_pending_since, created_at, updated_at
                    FROM smart_plugs
                """)
                )
                # Drop old table and rename new one
                await conn.execute(text("DROP TABLE smart_plugs"))
                await conn.execute(text("ALTER TABLE smart_plugs_temp RENAME TO smart_plugs"))
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Add show_on_printer_card column to smart_plugs
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN show_on_printer_card BOOLEAN DEFAULT 1")

    # Migration: Add MQTT smart plug fields (legacy)
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_topic VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_power_path VARCHAR(100)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_energy_path VARCHAR(100)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_state_path VARCHAR(100)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_multiplier REAL DEFAULT 1.0")

    # Migration: Add enhanced MQTT smart plug fields (separate topics and multipliers)
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_power_topic VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_power_multiplier REAL DEFAULT 1.0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_energy_topic VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_energy_multiplier REAL DEFAULT 1.0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_state_topic VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN mqtt_state_on_value VARCHAR(50)")

    # Migration: Copy existing mqtt_topic to mqtt_power_topic for backward compatibility
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                UPDATE smart_plugs
                SET mqtt_power_topic = mqtt_topic,
                    mqtt_power_multiplier = mqtt_multiplier
                WHERE mqtt_topic IS NOT NULL AND mqtt_power_topic IS NULL
            """)
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Create groups table for permission-based access control
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    description VARCHAR(500),
                    permissions JSON,
                    is_system BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_groups_name ON groups(name)"))
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Create user_groups association table
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS user_groups (
                    user_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, group_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
                )
            """)
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add model-based queue assignment columns to print_queue
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN target_model VARCHAR(50)")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN required_filament_types TEXT")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN waiting_reason TEXT")

    # Migration: Add nozzle_count column to printers (for dual-extruder detection)
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN nozzle_count INTEGER DEFAULT 1")

    # Migration: Add print_hours_offset column to printers (baseline hours adjustment)
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN print_hours_offset REAL DEFAULT 0.0")

    # Migration: Add queue notification event columns to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_job_added BOOLEAN DEFAULT 0")
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE notification_providers ADD COLUMN on_queue_job_assigned BOOLEAN DEFAULT 0")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_job_started BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_job_waiting BOOLEAN DEFAULT 1")
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_job_skipped BOOLEAN DEFAULT 1")
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_job_failed BOOLEAN DEFAULT 1")
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_queue_completed BOOLEAN DEFAULT 0")

    # Migration: Add created_by_id column to print_archives for user tracking (Issue #206)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE print_archives ADD COLUMN created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add created_by_id column to print_queue for user tracking (Issue #206)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE print_queue ADD COLUMN created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add created_by_id column to library_files for user tracking (Issue #206)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE library_files ADD COLUMN created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add target_location column to print_queue for location-based filtering (Issue #220)
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN target_location VARCHAR(100)")

    # Migration: Convert absolute paths to relative paths in library_files table
    # This ensures backup/restore portability across different installations
    try:
        async with conn.begin_nested():
            base_dir_str = str(settings.base_dir)
            # Ensure we have a trailing slash for clean replacement
            if not base_dir_str.endswith("/"):
                base_dir_str += "/"

            # Update file_path - remove base_dir prefix from absolute paths
            await conn.execute(
                text("""
                UPDATE library_files
                SET file_path = SUBSTR(file_path, LENGTH(:base_dir) + 1)
                WHERE file_path LIKE :pattern
            """),
                {"base_dir": base_dir_str, "pattern": base_dir_str + "%"},
            )

            # Update thumbnail_path - remove base_dir prefix from absolute paths
            await conn.execute(
                text("""
                UPDATE library_files
                SET thumbnail_path = SUBSTR(thumbnail_path, LENGTH(:base_dir) + 1)
                WHERE thumbnail_path LIKE :pattern
            """),
                {"base_dir": base_dir_str, "pattern": base_dir_str + "%"},
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Create active_print_spoolman table for Spoolman per-filament tracking
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS active_print_spoolman (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
                    archive_id INTEGER NOT NULL REFERENCES print_archives(id) ON DELETE CASCADE,
                    filament_usage TEXT NOT NULL,
                    ams_trays TEXT NOT NULL,
                    slot_to_tray TEXT,
                    layer_usage TEXT,
                    filament_properties TEXT,
                    UNIQUE(printer_id, archive_id)
                )
            """)
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add preset_source column to slot_preset_mappings for local preset support
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE slot_preset_mappings ADD COLUMN preset_source VARCHAR(20) DEFAULT 'cloud'")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add email column to users for Advanced Auth (PR #322)
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN email VARCHAR(255)")

    # Migration: Add inventory spool tracking columns
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN added_full BOOLEAN")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN last_used DATETIME")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN encode_time DATETIME")

    # Migration: Add RFID tag matching columns to spool
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN tag_uid VARCHAR(16)")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN tray_uuid VARCHAR(32)")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN data_origin VARCHAR(20)")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN tag_type VARCHAR(20)")

    # Migration: Add core_weight_catalog_id to track which catalog entry was used for empty spool weight
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN core_weight_catalog_id INTEGER")

    # Migration: Create spool_usage_history table for filament consumption tracking
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS spool_usage_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spool_id INTEGER NOT NULL REFERENCES spool(id) ON DELETE CASCADE,
                    printer_id INTEGER REFERENCES printers(id) ON DELETE SET NULL,
                    print_name VARCHAR(500),
                    weight_used REAL NOT NULL DEFAULT 0,
                    percent_used INTEGER NOT NULL DEFAULT 0,
                    status VARCHAR(20) NOT NULL DEFAULT 'completed',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add open_in_new_tab column to external_links
    await _safe_execute(conn, "ALTER TABLE external_links ADD COLUMN open_in_new_tab BOOLEAN DEFAULT 0")

    # Migration: Add bed cooled notification column to notification_providers
    await _safe_execute(conn, "ALTER TABLE notification_providers ADD COLUMN on_bed_cooled BOOLEAN DEFAULT 0")

    # Migration: Add first layer complete notification column to notification_providers
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE notification_providers ADD COLUMN on_first_layer_complete BOOLEAN DEFAULT 0")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Add weight_locked flag to spool table (skip AMS auto-sync for manually-entered weights)
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN weight_locked BOOLEAN DEFAULT 0")

    # Migration: Add SpoolBuddy scale weight tracking columns to spool table
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN last_scale_weight INTEGER")
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN last_weighed_at DATETIME")

    # Migration: Add cost tracking fields to spool table
    await _safe_execute(conn, "ALTER TABLE spool ADD COLUMN cost_per_kg REAL")
    # Migration: Add cost field to spool_usage_history table
    await _safe_execute(conn, "ALTER TABLE spool_usage_history ADD COLUMN cost REAL")
    # Migration: Add archive_id field to spool_usage_history table
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE spool_usage_history ADD COLUMN archive_id INTEGER REFERENCES print_archives(id)")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Migration: Migrate single virtual printer key-value settings to virtual_printers table
    try:
        async with conn.begin_nested():
            result = await conn.execute(text("SELECT COUNT(*) FROM virtual_printers"))
            count = result.scalar() or 0

            if count == 0:
                result = await conn.execute(text("SELECT value FROM settings WHERE key = 'virtual_printer_enabled'"))
                row = result.fetchone()
                if row:
                    # Old settings exist — migrate to first virtual printer row
                    old_enabled = row[0] == "true" if row[0] else False

                    result = await conn.execute(
                        text("SELECT value FROM settings WHERE key = 'virtual_printer_access_code'")
                    )
                    row = result.fetchone()
                    old_access_code = row[0] if row else None

                    result = await conn.execute(text("SELECT value FROM settings WHERE key = 'virtual_printer_mode'"))
                    row = result.fetchone()
                    old_mode = row[0] if row else "immediate"
                    if old_mode == "queue":
                        old_mode = "review"

                    result = await conn.execute(text("SELECT value FROM settings WHERE key = 'virtual_printer_model'"))
                    row = result.fetchone()
                    old_model = row[0] if row else "BL-P001"

                    result = await conn.execute(
                        text("SELECT value FROM settings WHERE key = 'virtual_printer_target_printer_id'")
                    )
                    row = result.fetchone()
                    old_target_id = int(row[0]) if row and row[0] else None

                    result = await conn.execute(
                        text("SELECT value FROM settings WHERE key = 'virtual_printer_remote_interface_ip'")
                    )
                    row = result.fetchone()
                    old_remote_iface = row[0] if row else None

                    await conn.execute(
                        text("""
                            INSERT INTO virtual_printers
                                (name, enabled, mode, model, access_code, target_printer_id,
                                 bind_ip, remote_interface_ip, serial_suffix, position)
                            VALUES
                                (:name, :enabled, :mode, :model, :access_code, :target_id,
                                 NULL, :remote_iface, '391800001', 0)
                        """),
                        {
                            "name": "Bambuddy",
                            "enabled": old_enabled,
                            "mode": old_mode or "immediate",
                            "model": old_model,
                            "access_code": old_access_code,
                            "target_id": old_target_id,
                            "remote_iface": old_remote_iface,
                        },
                    )
    except (OperationalError, ProgrammingError, IntegrityError):
        pass  # Table may not exist yet on first run, or columns have different constraints

    # Migration: Add filament_overrides column to print_queue for filament override in model-based assignment
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN filament_overrides TEXT")

    # Migration: Add NFC reader and display control columns to spoolbuddy_devices
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN nfc_reader_type VARCHAR(20)")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN nfc_connection VARCHAR(20)")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN display_brightness INTEGER DEFAULT 100")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN display_blank_timeout INTEGER DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN has_backlight BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN last_calibrated_at DATETIME")

    # Migration: Add NFC tag write payload column to spoolbuddy_devices
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN pending_write_payload TEXT")

    # Migration: Add OTA update tracking columns to spoolbuddy_devices
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN update_status VARCHAR(20)")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN update_message VARCHAR(255)")

    # Migration: Persist SpoolBuddy backend URL and queued system payload
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN backend_url VARCHAR(255)")
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN pending_system_payload TEXT")

    # Migration: Add system_stats JSON blob column to spoolbuddy_devices
    await _safe_execute(conn, "ALTER TABLE spoolbuddy_devices ADD COLUMN system_stats TEXT")

    # Migration: Convert ams_labels table from (printer_id, ams_id) key to ams_serial_number key
    # Labels are now keyed by AMS serial number so they persist when the AMS is moved to another printer.
    # PostgreSQL gets the correct schema from create_all(), so skip this
    if is_sqlite():
        try:
            await conn.execute(text("DROP TABLE IF EXISTS ams_labels_new"))
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='ams_labels'"))
            row = result.fetchone()
            if row and "printer_id" in (row[0] or ""):
                # Old schema: rebuild the table with ams_serial_number as the unique key.
                # Existing rows get a synthetic serial "p{printer_id}a{ams_id}" so data is preserved.
                await conn.execute(
                    text("""
                    CREATE TABLE ams_labels_new (
                        id INTEGER PRIMARY KEY,
                        ams_serial_number VARCHAR(50) NOT NULL,
                        ams_id INTEGER,
                        label VARCHAR(100) NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_ams_label_serial UNIQUE (ams_serial_number)
                    )
                """)
                )
                await conn.execute(
                    text("""
                    INSERT INTO ams_labels_new (id, ams_serial_number, ams_id, label, created_at, updated_at)
                    SELECT id,
                           'p' || CAST(printer_id AS TEXT) || 'a' || CAST(ams_id AS TEXT),
                           ams_id,
                           label,
                           created_at,
                           updated_at
                    FROM ams_labels
                """)
                )
                await conn.execute(text("DROP TABLE ams_labels"))
                await conn.execute(text("ALTER TABLE ams_labels_new RENAME TO ams_labels"))
        except (OperationalError, ProgrammingError):
            pass  # Already migrated or table does not exist yet

    # Migration: Add auto_dispatch column to virtual_printers
    await _safe_execute(conn, "ALTER TABLE virtual_printers ADD COLUMN auto_dispatch BOOLEAN DEFAULT 1")

    # Migration: Fix VP model codes — convert legacy SSDP codes and display names to correct SSDP codes
    # Legacy codes (from multi-VP refactor) and display names (from proxy auto-inherit)
    vp_model_fixes = {
        "3DPrinter-X1-Carbon": "BL-P001",
        "3DPrinter-X1": "BL-P002",
        "X1C": "BL-P001",
        "X1": "BL-P002",
        "X1E": "C13",
        "X2D": "N6",
        "P1P": "C11",
        "P1S": "C12",
        "P2S": "N7",
        "A1": "N2S",
        "A1 Mini": "N1",
        "H2D": "O1D",
        "H2C": "O1C",
        "H2S": "O1S",
    }
    for old_val, new_val in vp_model_fixes.items():
        await conn.execute(
            text("UPDATE virtual_printers SET model = :new WHERE model = :old"),
            {"old": old_val, "new": new_val},
        )
        await conn.execute(
            text("UPDATE settings SET value = :new WHERE key = 'virtual_printer_model' AND value = :old"),
            {"old": old_val, "new": new_val},
        )

    # Migration: Add per-user Bambu Cloud credential columns
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN cloud_token VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN cloud_email VARCHAR(255)")
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN cloud_region VARCHAR(10)")

    # Cleanup: Remove obsolete settings keys that are no longer used
    obsolete_keys = ["slicer_binary_path"]
    for key in obsolete_keys:
        await conn.execute(text("DELETE FROM settings WHERE key = :key"), {"key": key})

    # Migration: Create user_email_preferences table for user-specific email notification settings
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS user_email_preferences (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    notify_print_start BOOLEAN NOT NULL DEFAULT 1,
                    notify_print_complete BOOLEAN NOT NULL DEFAULT 1,
                    notify_print_failed BOOLEAN NOT NULL DEFAULT 1,
                    notify_print_stopped BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            )
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_user_email_preferences_user_id ON user_email_preferences(user_id)")
            )
    except (OperationalError, ProgrammingError):
        pass  # Already applied

    # Legacy migration: Add notify_print_stopped column (for any existing partial tables)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("ALTER TABLE user_email_preferences ADD COLUMN notify_print_stopped BOOLEAN NOT NULL DEFAULT 1")
            )
    except (OperationalError, ProgrammingError):
        pass  # Column already exists or table created with full schema

    # Migration: Add camera_rotation column to printers
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN camera_rotation INTEGER DEFAULT 0")

    # Migration: Add awaiting_plate_clear column to printers (#961)
    await _safe_execute(conn, "ALTER TABLE printers ADD COLUMN awaiting_plate_clear BOOLEAN DEFAULT FALSE NOT NULL")

    # Migration: Add REST/Webhook smart plug fields
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_on_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_on_body TEXT")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_off_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_off_body TEXT")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_method VARCHAR(10)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_headers TEXT")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_status_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_status_path VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_status_on_value VARCHAR(50)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_power_path VARCHAR(200)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_energy_path VARCHAR(200)")

    # Migration: Add separate REST power/energy URLs and multipliers
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_power_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_power_multiplier REAL DEFAULT 1.0")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_energy_url VARCHAR(500)")
    await _safe_execute(conn, "ALTER TABLE smart_plugs ADD COLUMN rest_energy_multiplier REAL DEFAULT 1.0")

    # Migration: Add batch_id column to print_queue for batch grouping
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "ALTER TABLE print_queue ADD COLUMN batch_id INTEGER REFERENCES print_batches(id) ON DELETE SET NULL"
                )
            )
    except (OperationalError, ProgrammingError):
        pass

    # Migration: Shortest-job-first scheduling columns on print_queue
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN print_time_seconds INTEGER")
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN been_jumped BOOLEAN DEFAULT FALSE NOT NULL")

    # Migration: Auto-print G-code injection (#422)
    await _safe_execute(conn, "ALTER TABLE print_queue ADD COLUMN gcode_injection BOOLEAN DEFAULT FALSE NOT NULL")

    # Migration: Add backup_spools and backup_archives columns to github_backup_config
    await _safe_execute(conn, "ALTER TABLE github_backup_config ADD COLUMN backup_spools BOOLEAN DEFAULT 0")
    await _safe_execute(conn, "ALTER TABLE github_backup_config ADD COLUMN backup_archives BOOLEAN DEFAULT 0")

    # Migration: Widen columns where SQLite allowed data beyond the declared VARCHAR limit
    if not is_sqlite():
        await _safe_execute(conn, "ALTER TABLE api_keys ALTER COLUMN key_hash TYPE VARCHAR(255)")
        await _safe_execute(conn, "ALTER TABLE api_keys ALTER COLUMN key_prefix TYPE VARCHAR(20)")
        await _safe_execute(conn, "ALTER TABLE print_archives ALTER COLUMN filament_color TYPE VARCHAR(200)")

    # Migration: Create GIN index for full-text search on PostgreSQL
    # (SQLite uses FTS5 virtual table instead, set up above)
    if not is_sqlite():
        try:
            await conn.execute(
                text("""
                CREATE INDEX IF NOT EXISTS idx_archives_fulltext
                ON print_archives
                USING GIN (to_tsvector('simple',
                    COALESCE(print_name, '') || ' ' ||
                    COALESCE(filename, '') || ' ' ||
                    COALESCE(tags, '') || ' ' ||
                    COALESCE(notes, '') || ' ' ||
                    COALESCE(designer, '') || ' ' ||
                    COALESCE(filament_type, '')
                ))
            """)
            )
        except (OperationalError, ProgrammingError):
            pass  # Already applied

    # Migration: Normalize empty printer_ids [] to NULL (global access) on API keys
    # Previously both None and [] meant "all printers"; now [] means "no printers"
    # PostgreSQL stores printer_ids as JSONB; comparing JSONB to a string literal fails
    # with "operator does not exist: jsonb = unknown" — cast the literal to jsonb explicitly.
    async with conn.begin_nested():
        if is_sqlite():
            await conn.execute(text("UPDATE api_keys SET printer_ids = NULL WHERE printer_ids = '[]'"))
        else:
            await conn.execute(text("UPDATE api_keys SET printer_ids = NULL WHERE printer_ids = '[]'::jsonb"))

    # Migration: Add auth_source column to users for LDAP support (#794)
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN auth_source VARCHAR(20) DEFAULT 'local' NOT NULL")

    # Migration: Make password_hash nullable for LDAP users (#794)
    # LDAP users have no local password — the column must allow NULL so auto-provisioning
    # doesn't hit a NOT NULL constraint failure on upgraded installs whose users table was
    # originally created before LDAP support landed.
    if is_sqlite():
        # SQLite can't ALTER COLUMN; patch sqlite_master directly via writable_schema.
        # Bump schema_version afterwards so SQLite reloads the table definition from disk —
        # without that bump, the current connection keeps enforcing the old NOT NULL from
        # its cached schema. Safe because row data is untouched and the replace() is a
        # no-op if the constraint has already been removed.
        try:
            result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"))
            users_sql = result.scalar()
            if users_sql and "password_hash VARCHAR(255) NOT NULL" in users_sql:
                version_result = await conn.execute(text("PRAGMA schema_version"))
                schema_version = version_result.scalar() or 0
                await conn.execute(text("PRAGMA writable_schema = ON"))
                await conn.execute(
                    text(
                        "UPDATE sqlite_master "
                        "SET sql = replace(sql, 'password_hash VARCHAR(255) NOT NULL', 'password_hash VARCHAR(255)') "
                        "WHERE type = 'table' AND name = 'users'"
                    )
                )
                await conn.execute(text(f"PRAGMA schema_version = {schema_version + 1}"))
                await conn.execute(text("PRAGMA writable_schema = OFF"))
        except (OperationalError, ProgrammingError) as exc:
            logger.error(
                "Failed to remove NOT NULL from users.password_hash via writable_schema — "
                "OIDC/LDAP user creation will fail on this install: %s",
                exc,
                exc_info=True,
            )
    else:
        await _safe_execute(conn, "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL")

    # Migration: Add energy_start_kwh to print_archives (#941)
    # Persists the smart plug lifetime counter captured at print start, so per-print
    # energy tracking survives a backend restart mid-print.
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN energy_start_kwh REAL")

    # Migration: Add subtask_id to print_archives (#972)
    # MQTT-provided task identifier used to resume the same archive row across a
    # backend restart mid-print. Without it, a long print (e.g. 13h) triggers
    # stale-cancel + new-archive, losing started_at continuity.
    await _safe_execute(conn, "ALTER TABLE print_archives ADD COLUMN subtask_id VARCHAR(64)")

    # Migration: Create smart_plug_energy_snapshots table (#941)
    # Hourly snapshots of each plug's lifetime counter, so date-range queries in
    # "total consumption" energy mode can compute (last - first) deltas.
    await _safe_execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS smart_plug_energy_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plug_id INTEGER NOT NULL REFERENCES smart_plugs(id) ON DELETE CASCADE,
            recorded_at DATETIME NOT NULL,
            lifetime_kwh REAL NOT NULL
        )
        """
        if is_sqlite()
        else """
        CREATE TABLE IF NOT EXISTS smart_plug_energy_snapshots (
            id SERIAL PRIMARY KEY,
            plug_id INTEGER NOT NULL REFERENCES smart_plugs(id) ON DELETE CASCADE,
            recorded_at TIMESTAMP NOT NULL,
            lifetime_kwh REAL NOT NULL
        )
        """,
    )
    await _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_plug_energy_snapshots_plug_time "
        "ON smart_plug_energy_snapshots(plug_id, recorded_at)",
    )

    # Migration: Add PKCE code_verifier column to auth_ephemeral_tokens
    await _safe_execute(conn, "ALTER TABLE auth_ephemeral_tokens ADD COLUMN code_verifier VARCHAR(128)")

    # Migration: Add TOTP replay-protection counter to user_totp
    await _safe_execute(conn, "ALTER TABLE user_totp ADD COLUMN last_totp_counter BIGINT")

    # Migration: Add challenge_id for pre-auth token client binding (HttpOnly cookie)
    await _safe_execute(conn, "ALTER TABLE auth_ephemeral_tokens ADD COLUMN challenge_id VARCHAR(128)")

    # Migration: Add auto_link_existing_accounts column to oidc_providers (M-4)
    # Postgres rejects `DEFAULT 0` for BOOLEAN columns.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN auto_link_existing_accounts BOOLEAN DEFAULT 0")
    else:
        await _safe_execute(
            conn, "ALTER TABLE oidc_providers ADD COLUMN auto_link_existing_accounts BOOLEAN DEFAULT false"
        )

    # Migration: Azure Entra ID support — configurable email claim and verification requirement
    await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN email_claim VARCHAR(64) DEFAULT 'email'")
    # Postgres rejects `DEFAULT 1` for BOOLEAN columns.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN require_email_verified BOOLEAN DEFAULT 1")
    else:
        await _safe_execute(conn, "ALTER TABLE oidc_providers ADD COLUMN require_email_verified BOOLEAN DEFAULT true")
    # SEC-1/SEC-6: Add DB-level CHECK constraint for existing PostgreSQL installs.
    # SQLite does not support ALTER TABLE ADD CONSTRAINT — handled by __table_args__ at creation.
    if not is_sqlite():
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE oidc_providers ADD CONSTRAINT ck_auto_link_requires_verified_email_claim "
                        "CHECK (auto_link_existing_accounts = FALSE OR (require_email_verified = TRUE AND email_claim = 'email'))"
                    )
                )
        except (OperationalError, ProgrammingError) as exc:
            msg = str(exc).lower()
            if "already exists" not in msg:
                logger.error(
                    "Security constraint migration FAILED — auto_link safety constraint may not be enforced: %s",
                    exc,
                    exc_info=True,
                )
                raise

    # SEC-1 backfill: reset auto_link on rows where the combined state is unsafe.
    # Guards existing installs that may have had auto_link=TRUE set before the
    # Combined-State-Guard was introduced.  On fresh installs the column defaults
    # guarantee this UPDATE always matches zero rows.  TRUE/FALSE literals are
    # accepted by both SQLite (≥ 3.23) and PostgreSQL — no dialect branch needed.
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "UPDATE oidc_providers SET auto_link_existing_accounts = FALSE "
                    "WHERE auto_link_existing_accounts = TRUE "
                    "AND (require_email_verified = FALSE OR email_claim != 'email')"
                )
            )
    except Exception as exc:
        logger.error(
            "SEC-1 safety backfill FAILED — auto_link_existing_accounts may remain enabled "
            "on providers with unsafe email settings: %s",
            exc,
            exc_info=True,
        )
        raise

    # Migration: Add password_changed_at to users (M-R7-B)
    # Tracks the last time a user's password was changed/reset.  JWTs whose iat
    # predates this timestamp are rejected in all six auth validation paths.
    # R4 fix: TIMESTAMP is accepted by both SQLite and PostgreSQL; DATETIME
    # is rejected by Postgres ("type 'datetime' does not exist"), which made
    # _safe_execute swallow the error and leave existing Postgres installs
    # without the column — causing UndefinedColumnError on every User query.
    await _safe_execute(conn, "ALTER TABLE users ADD COLUMN password_changed_at TIMESTAMP")

    # Migration: Back-fill password_changed_at = created_at for existing users (I2).
    # Users who never changed their password would have NULL here, meaning old
    # tokens could never be invalidated via the freshness check.  Setting it to
    # created_at is conservative: any token issued before the account was created
    # is always invalid, so this is a safe lower bound.
    async with conn.begin_nested():
        await conn.execute(text("UPDATE users SET password_changed_at = created_at WHERE password_changed_at IS NULL"))

    # Migration: Provenance columns on library_files for MakerWorld imports.
    # source_url is indexed so "already imported" dedupe lookups stay O(log N)
    # as the library grows.
    await _safe_execute(conn, "ALTER TABLE library_files ADD COLUMN source_type VARCHAR(32)")
    await _safe_execute(conn, "ALTER TABLE library_files ADD COLUMN source_url VARCHAR(512)")
    await _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_library_files_source_url ON library_files(source_url)",
    )

    # Migration: Soft-delete column for trash bin (Issue #1008). Indexed so the
    # sweeper's "SELECT ... WHERE deleted_at < cutoff" and the trash list's
    # "WHERE deleted_at IS NOT NULL" stay cheap as the table grows.
    #
    # ``DATETIME`` is a SQLite-only type alias — PostgreSQL rejects it as
    # invalid syntax, _safe_execute swallows the error, and the column is
    # never added (breaking every query that references it). Emit
    # dialect-appropriate SQL so both backends get the column.
    if is_sqlite():
        await _safe_execute(conn, "ALTER TABLE library_files ADD COLUMN deleted_at DATETIME")
    else:
        await _safe_execute(conn, "ALTER TABLE library_files ADD COLUMN deleted_at TIMESTAMP")
    await _safe_execute(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_library_files_deleted_at ON library_files(deleted_at)",
    )

    # Legacy SQLite installs created `settings` without a UNIQUE constraint on `key`,
    # so `INSERT OR IGNORE` below silently degrades to a plain INSERT and dupes rows on
    # every restart. Dedupe (keep lowest id per key) and add the missing unique index
    # before seeding. Safe/idempotent on both dialects — fresh installs already have
    # no dupes and `create_all` already emits the index.
    async with conn.begin_nested():
        await conn.execute(text("DELETE FROM settings WHERE id NOT IN (SELECT MIN(id) FROM settings GROUP BY key)"))
    await _safe_execute(conn, "CREATE UNIQUE INDEX IF NOT EXISTS ix_settings_key ON settings(key)")

    # Migration: Normalise provider_email to lowercase (SEC-3).
    # Required for Entra ID where UPN/email claims may arrive in mixed case.
    # LOWER() is supported by both SQLite and PostgreSQL; the UPDATE is idempotent.
    # Executed directly (not via _safe_execute) so any column-reference failure
    # is always fatal and never silently swallowed.
    async with conn.begin_nested():
        await conn.execute(
            text(
                "UPDATE user_oidc_links SET provider_email = LOWER(provider_email) "
                "WHERE provider_email IS NOT NULL AND provider_email != LOWER(provider_email)"
            )
        )

    # Seed default settings keys that must exist on fresh install
    default_settings = [
        ("advanced_auth_enabled", "false"),
        ("smtp_auth_enabled", "true"),
    ]
    for key, value in default_settings:
        try:
            if is_sqlite():
                await conn.execute(
                    text("INSERT OR IGNORE INTO settings (key, value) VALUES (:key, :value)"),
                    {"key": key, "value": value},
                )
            else:
                await conn.execute(
                    text("INSERT INTO settings (key, value) VALUES (:key, :value) ON CONFLICT (key) DO NOTHING"),
                    {"key": key, "value": value},
                )
        except (OperationalError, ProgrammingError):
            pass


async def seed_notification_templates():
    """Seed default notification templates if they don't exist."""
    from sqlalchemy import select

    from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate

    async with async_session() as session:
        # Get existing template event types
        result = await session.execute(select(NotificationTemplate.event_type))
        existing_types = {row[0] for row in result.fetchall()}

        if not existing_types:
            # No templates exist - insert all defaults
            for template_data in DEFAULT_TEMPLATES:
                template = NotificationTemplate(
                    event_type=template_data["event_type"],
                    name=template_data["name"],
                    title_template=template_data["title_template"],
                    body_template=template_data["body_template"],
                    is_default=True,
                )
                session.add(template)
        else:
            # Templates exist - only add missing ones
            for template_data in DEFAULT_TEMPLATES:
                if template_data["event_type"] not in existing_types:
                    template = NotificationTemplate(
                        event_type=template_data["event_type"],
                        name=template_data["name"],
                        title_template=template_data["title_template"],
                        body_template=template_data["body_template"],
                        is_default=True,
                    )
                    session.add(template)

        await session.commit()


async def seed_default_groups():
    """Seed default groups and migrate existing users to appropriate groups.

    Creates the default system groups (Administrators, Operators, Viewers) if they
    don't exist, then migrates existing users:
    - Users with role='admin' -> Administrators group
    - Users with role='user' -> Operators group

    Also migrates old permissions to new ownership-based permissions (Issue #205).
    """
    import logging

    from sqlalchemy import select

    from backend.app.core.permissions import DEFAULT_GROUPS
    from backend.app.models.group import Group
    from backend.app.models.user import User

    logger = logging.getLogger(__name__)

    # Map old permissions to new ones for migration
    # Administrators get *_all permissions, Operators get *_own permissions
    PERMISSION_MIGRATION_ALL = {
        "queue:update": "queue:update_all",
        "queue:delete": "queue:delete_all",
        "archives:update": "archives:update_all",
        "archives:delete": "archives:delete_all",
        "archives:reprint": "archives:reprint_all",
        "library:update": "library:update_all",
        "library:delete": "library:delete_all",
    }

    PERMISSION_MIGRATION_OWN = {
        "queue:update": "queue:update_own",
        "queue:delete": "queue:delete_own",
        "archives:update": "archives:update_own",
        "archives:delete": "archives:delete_own",
        "archives:reprint": "archives:reprint_own",
        "library:update": "library:update_own",
        "library:delete": "library:delete_own",
    }

    async with async_session() as session:
        # Get existing groups
        result = await session.execute(select(Group))
        existing_groups = {group.name: group for group in result.scalars().all()}

        # Create default groups if they don't exist
        groups_created = []
        for group_name, group_config in DEFAULT_GROUPS.items():
            if group_name not in existing_groups:
                group = Group(
                    name=group_name,
                    description=group_config["description"],
                    permissions=group_config["permissions"],
                    is_system=group_config["is_system"],
                )
                session.add(group)
                groups_created.append(group_name)
                logger.info("Created default group: %s", group_name)
            else:
                # Migrate existing group's permissions from old to new format
                group = existing_groups[group_name]
                if group.permissions:
                    updated = False
                    new_permissions = list(group.permissions)

                    # Determine which migration map to use based on group
                    migration_map = (
                        PERMISSION_MIGRATION_ALL if group_name == "Administrators" else PERMISSION_MIGRATION_OWN
                    )

                    for old_perm, new_perm in migration_map.items():
                        if old_perm in new_permissions:
                            new_permissions.remove(old_perm)
                            if new_perm not in new_permissions:
                                new_permissions.append(new_perm)
                            updated = True
                            logger.info(
                                "Migrated permission '%s' to '%s' in group '%s'", old_perm, new_perm, group_name
                            )

                    # For Administrators, also ensure they get *_all permissions if they have any new *_own
                    if group_name == "Administrators":
                        for _own_perm, all_perm in [
                            ("queue:update_own", "queue:update_all"),
                            ("queue:delete_own", "queue:delete_all"),
                            ("archives:update_own", "archives:update_all"),
                            ("archives:delete_own", "archives:delete_all"),
                            ("archives:reprint_own", "archives:reprint_all"),
                            ("library:update_own", "library:update_all"),
                            ("library:delete_own", "library:delete_all"),
                        ]:
                            # Add *_all if not present
                            if all_perm not in new_permissions:
                                new_permissions.append(all_perm)
                                updated = True

                    if updated:
                        group.permissions = new_permissions

        await session.commit()

        # Migrate new permissions: grant printers:clear_plate to all groups with printers:control
        result = await session.execute(select(Group))
        all_groups = result.scalars().all()
        for group in all_groups:
            if (
                group.permissions
                and "printers:control" in group.permissions
                and "printers:clear_plate" not in group.permissions
            ):
                group.permissions = [*group.permissions, "printers:clear_plate"]
                logger.info("Added printers:clear_plate to group '%s' (has printers:control)", group.name)
        await session.commit()

        # Migrate new permissions for MakerWorld integration: groups that
        # already have library:upload (i.e. can write to the library) are
        # the correct audience for makerworld:view + makerworld:import, and
        # groups that only have library:read get makerworld:view (browse
        # only). Matches the intent of DEFAULT_GROUPS without clobbering
        # any user-customised permission lists.
        result = await session.execute(select(Group))
        for group in result.scalars().all():
            if not group.permissions:
                continue
            perms = list(group.permissions)
            changed = False
            if "library:upload" in perms:
                for new_perm in ("makerworld:view", "makerworld:import"):
                    if new_perm not in perms:
                        perms.append(new_perm)
                        changed = True
                        logger.info("Added %s to group '%s' (has library:upload)", new_perm, group.name)
            elif "library:read" in perms and "makerworld:view" not in perms:
                perms.append("makerworld:view")
                changed = True
                logger.info("Added makerworld:view to group '%s' (has library:read)", group.name)
            if changed:
                group.permissions = perms
        await session.commit()

        # Backfill library:purge + archives:purge for the Administrators group
        # on existing installs. Both permissions were added after Administrators
        # was first seeded, so upgrading users miss them even though the default
        # config (ALL_PERMISSIONS) includes them for fresh installs.
        result = await session.execute(select(Group).where(Group.name == "Administrators"))
        admin_group = result.scalar_one_or_none()
        if admin_group and admin_group.permissions is not None:
            perms = list(admin_group.permissions)
            added = False
            for new_perm in ("library:purge", "archives:purge"):
                if new_perm not in perms:
                    perms.append(new_perm)
                    added = True
                    logger.info("Added %s to Administrators group (backfill)", new_perm)
            if added:
                admin_group.permissions = perms
        await session.commit()

        # Migrate existing users to groups if they're not already in any group
        if groups_created:
            # Refresh to get newly created groups
            admin_result = await session.execute(select(Group).where(Group.name == "Administrators"))
            admin_group = admin_result.scalar_one_or_none()

            operators_result = await session.execute(select(Group).where(Group.name == "Operators"))
            operators_group = operators_result.scalar_one_or_none()

            # Get all users
            users_result = await session.execute(select(User))
            users = users_result.scalars().all()

            for user in users:
                # Skip if user already has groups
                if user.groups:
                    continue

                if user.role == "admin" and admin_group:
                    user.groups.append(admin_group)
                    logger.info("Migrated admin user '%s' to Administrators group", user.username)
                elif operators_group:
                    user.groups.append(operators_group)
                    logger.info("Migrated user '%s' to Operators group", user.username)

            await session.commit()


async def seed_spool_catalog():
    """Seed the spool catalog with default entries if empty."""
    import logging

    from sqlalchemy import func, select

    from backend.app.core.catalog_defaults import DEFAULT_SPOOL_CATALOG
    from backend.app.models.spool_catalog import SpoolCatalogEntry

    logger = logging.getLogger(__name__)

    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(SpoolCatalogEntry))
        count = result.scalar() or 0
        if count > 0:
            return  # Already seeded

        for name, weight in DEFAULT_SPOOL_CATALOG:
            session.add(SpoolCatalogEntry(name=name, weight=weight, is_default=True))
        await session.commit()
        logger.info("Seeded %d default spool catalog entries", len(DEFAULT_SPOOL_CATALOG))


async def seed_color_catalog():
    """Seed the color catalog with default entries if empty."""
    import logging

    from sqlalchemy import func, select

    from backend.app.core.catalog_defaults import DEFAULT_COLOR_CATALOG
    from backend.app.models.color_catalog import ColorCatalogEntry

    logger = logging.getLogger(__name__)

    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(ColorCatalogEntry))
        count = result.scalar() or 0
        if count > 0:
            return  # Already seeded

        for manufacturer, color_name, hex_color, material in DEFAULT_COLOR_CATALOG:
            session.add(
                ColorCatalogEntry(
                    manufacturer=manufacturer,
                    color_name=color_name,
                    hex_color=hex_color,
                    material=material,
                    is_default=True,
                )
            )
        await session.commit()
        logger.info("Seeded %d default color catalog entries", len(DEFAULT_COLOR_CATALOG))
