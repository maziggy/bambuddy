from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.app.core.config import settings


def _set_sqlite_pragmas(dbapi_conn, connection_record):
    """Set SQLite pragmas on each new connection for concurrency and performance."""
    cursor = dbapi_conn.cursor()
    # WAL mode allows concurrent readers + one writer (vs default DELETE mode which locks entirely)
    cursor.execute("PRAGMA journal_mode = WAL")
    # Wait up to 5 seconds when the database is locked instead of failing immediately
    cursor.execute("PRAGMA busy_timeout = 5000")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.close()


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
)

# Register the pragma listener on the underlying sync engine
event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def close_all_connections():
    """Close all database connections for backup/restore operations."""
    global engine
    await engine.dispose()


async def reinitialize_database():
    """Reinitialize database connection after restore."""
    global engine, async_session
    engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
    )
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
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
        api_key,
        archive,
        color_catalog,
        external_link,
        filament,
        github_backup,
        group,
        kprofile_note,
        library,
        local_preset,
        maintenance,
        notification,
        notification_template,
        orca_base_cache,
        pending_upload,
        print_log,
        print_queue,
        printer,
        project,
        project_bom,
        settings,
        slot_preset,
        smart_plug,
        spool,
        spool_assignment,
        spool_catalog,
        spool_k_profile,
        spool_usage_history,
        user,
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


async def run_migrations(conn):
    """Add new columns to existing tables if they don't exist."""
    from sqlalchemy import text

    # Migration: Add is_favorite column to print_archives
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN is_favorite BOOLEAN DEFAULT 0"))
    except OperationalError:
        # Column already exists
        pass

    # Migration: Add content_hash column to print_archives for duplicate detection
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN content_hash VARCHAR(64)"))
    except OperationalError:
        # Column already exists
        pass

    # Migration: Add auto_off_executed column to smart_plugs
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN auto_off_executed BOOLEAN DEFAULT 0"))
    except OperationalError:
        # Column already exists
        pass

    # Migration: Add on_print_stopped column to notification_providers
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_print_stopped BOOLEAN DEFAULT 1"))
    except OperationalError:
        # Column already exists
        pass

    # Migration: Add source_3mf_path column to print_archives
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN source_3mf_path VARCHAR(500)"))
    except OperationalError:
        # Column already exists
        pass

    # Migration: Add f3d_path column to print_archives for Fusion 360 design files
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN f3d_path VARCHAR(500)"))
    except OperationalError:
        # Column already exists
        pass

    # Migration: Add on_maintenance_due column to notification_providers
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_maintenance_due BOOLEAN DEFAULT 0"))
    except OperationalError:
        # Column already exists
        pass

    # Migration: Add location column to printers for grouping
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN location VARCHAR(100)"))
    except OperationalError:
        # Column already exists
        pass

    # Migration: Add interval_type column to maintenance_types
    try:
        await conn.execute(text("ALTER TABLE maintenance_types ADD COLUMN interval_type VARCHAR(20) DEFAULT 'hours'"))
    except OperationalError:
        # Column already exists
        pass

    # Migration: Add is_deleted column to maintenance_types for soft-deletes
    try:
        await conn.execute(text("ALTER TABLE maintenance_types ADD COLUMN is_deleted BOOLEAN DEFAULT 0"))
    except OperationalError:
        # Column already exists
        pass

    # Migration: Add custom_interval_type column to printer_maintenance
    try:
        await conn.execute(text("ALTER TABLE printer_maintenance ADD COLUMN custom_interval_type VARCHAR(20)"))
    except OperationalError:
        # Column already exists
        pass

    # Migration: Add power alert columns to smart_plugs
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN power_alert_enabled BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN power_alert_high REAL"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN power_alert_low REAL"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN power_alert_last_triggered DATETIME"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add schedule columns to smart_plugs
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN schedule_enabled BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN schedule_on_time VARCHAR(5)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN schedule_off_time VARCHAR(5)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add daily digest columns to notification_providers
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN daily_digest_enabled BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN daily_digest_time VARCHAR(5)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add project_id column to print_archives
    try:
        await conn.execute(
            text("ALTER TABLE print_archives ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Add project_id column to print_queue
    try:
        await conn.execute(
            text("ALTER TABLE print_queue ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Create FTS5 virtual table for archive full-text search
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
    except OperationalError:
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
    except OperationalError:
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
    except OperationalError:
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
    except OperationalError:
        pass  # Already applied

    # Migration: Add auto_off_pending columns to smart_plugs (for restart recovery)
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN auto_off_pending BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN auto_off_pending_since DATETIME"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add AMS alarm notification columns to notification_providers
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_ams_humidity_high BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(
            text("ALTER TABLE notification_providers ADD COLUMN on_ams_temperature_high BOOLEAN DEFAULT 0")
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Add AMS-HT alarm notification columns to notification_providers
    try:
        await conn.execute(
            text("ALTER TABLE notification_providers ADD COLUMN on_ams_ht_humidity_high BOOLEAN DEFAULT 0")
        )
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(
            text("ALTER TABLE notification_providers ADD COLUMN on_ams_ht_temperature_high BOOLEAN DEFAULT 0")
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Add plate not empty notification column to notification_providers
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_plate_not_empty BOOLEAN DEFAULT 1"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add notes column to projects (Phase 2)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN notes TEXT"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add attachments column to projects (Phase 3)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN attachments JSON"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add tags column to projects (Phase 4)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN tags TEXT"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add due_date column to projects (Phase 5)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN due_date DATETIME"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add priority column to projects (Phase 5)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN priority VARCHAR(20) DEFAULT 'normal'"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add budget column to projects (Phase 6)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN budget REAL"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add is_template column to projects (Phase 8)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN is_template BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add template_source_id column to projects (Phase 8)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN template_source_id INTEGER"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add parent_id column to projects (Phase 10)
    try:
        await conn.execute(
            text("ALTER TABLE projects ADD COLUMN parent_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Rename quantity_printed to quantity_acquired in project_bom_items
    try:
        await conn.execute(text("ALTER TABLE project_bom_items RENAME COLUMN quantity_printed TO quantity_acquired"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add unit_price column to project_bom_items
    try:
        await conn.execute(text("ALTER TABLE project_bom_items ADD COLUMN unit_price REAL"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add sourcing_url column to project_bom_items
    try:
        await conn.execute(text("ALTER TABLE project_bom_items ADD COLUMN sourcing_url VARCHAR(512)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Rename notes to remarks in project_bom_items
    try:
        await conn.execute(text("ALTER TABLE project_bom_items RENAME COLUMN notes TO remarks"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add show_in_switchbar column to smart_plugs
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN show_in_switchbar BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add runtime tracking columns to printers
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN runtime_seconds INTEGER DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN last_runtime_update DATETIME"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add quantity column to print_archives for tracking item count
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN quantity INTEGER DEFAULT 1"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add manual_start column to print_queue for staged prints
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN manual_start BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add wiki_url column to maintenance_types for documentation links
    try:
        await conn.execute(text("ALTER TABLE maintenance_types ADD COLUMN wiki_url VARCHAR(500)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add ams_mapping column to print_queue for storing filament slot assignments
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN ams_mapping TEXT"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add target_parts_count column to projects for tracking total parts needed
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN target_parts_count INTEGER"))
    except OperationalError:
        pass  # Already applied

    # Migration: Make printer_id nullable in print_queue for unassigned queue items
    # SQLite doesn't support ALTER COLUMN, so we need to recreate the table
    try:
        # Check if printer_id is already nullable by trying to insert NULL
        # This is a safe check that won't affect existing data
        result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='print_queue'"))
        row = result.fetchone()
        if row and "printer_id INTEGER NOT NULL" in (row[0] or ""):
            # Need to migrate - printer_id is currently NOT NULL
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
    except OperationalError:
        pass  # Already applied

    # Migration: Add plug_type column to smart_plugs for HA integration
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN plug_type VARCHAR(20) DEFAULT 'tasmota'"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add ha_entity_id column to smart_plugs for HA integration
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN ha_entity_id VARCHAR(100)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add project_id column to library_folders for linking folders to projects
    try:
        await conn.execute(
            text("ALTER TABLE library_folders ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Add archive_id column to library_folders for linking folders to archives
    try:
        await conn.execute(
            text(
                "ALTER TABLE library_folders ADD COLUMN archive_id INTEGER REFERENCES print_archives(id) ON DELETE SET NULL"
            )
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Make ip_address nullable for HA plugs (SQLite requires table recreation)
    try:
        # Check if ip_address is currently NOT NULL
        result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='smart_plugs'"))
        row = result.fetchone()
        if row and "ip_address VARCHAR(45) NOT NULL" in (row[0] or ""):
            # Need to migrate - ip_address is currently NOT NULL
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
                       enabled, auto_on, auto_off, off_delay_mode, off_delay_minutes, off_temp_threshold,
                       username, password, power_alert_enabled, power_alert_high, power_alert_low,
                       power_alert_last_triggered, schedule_enabled, schedule_on_time, schedule_off_time,
                       COALESCE(show_in_switchbar, 0), last_state, last_checked, auto_off_executed,
                       COALESCE(auto_off_pending, 0), auto_off_pending_since, created_at, updated_at
                FROM smart_plugs
            """)
            )
            await conn.execute(text("DROP TABLE smart_plugs"))
            await conn.execute(text("ALTER TABLE smart_plugs_new RENAME TO smart_plugs"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add plate_id column to print_queue for multi-plate 3MF support
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN plate_id INTEGER"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add print options columns to print_queue
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN bed_levelling BOOLEAN DEFAULT 1"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN flow_cali BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN vibration_cali BOOLEAN DEFAULT 1"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN layer_inspect BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN timelapse BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN use_ams BOOLEAN DEFAULT 1"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add library_file_id column to print_queue and make archive_id nullable
    # This allows queue items to reference library files directly (archive created at print start)
    try:
        await conn.execute(
            text(
                "ALTER TABLE print_queue ADD COLUMN library_file_id INTEGER REFERENCES library_files(id) ON DELETE CASCADE"
            )
        )
    except OperationalError:
        pass  # Already applied

    # Check if archive_id needs to be made nullable (requires table recreation in SQLite)
    try:
        result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='print_queue'"))
        row = result.fetchone()
        if row and "archive_id INTEGER NOT NULL" in (row[0] or ""):
            # Need to migrate - archive_id is currently NOT NULL
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
    except OperationalError:
        pass  # Already applied

    # Migration: Add HA energy sensor entity columns to smart_plugs
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN ha_power_entity VARCHAR(100)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN ha_energy_today_entity VARCHAR(100)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN ha_energy_total_entity VARCHAR(100)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Create users table for authentication
    try:
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
    except OperationalError:
        pass  # Already applied

    # Migration: Add external camera columns to printers
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN external_camera_url VARCHAR(500)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN external_camera_type VARCHAR(20)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN external_camera_enabled BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add external_url column to print_archives for user-defined links (Printables, etc.)
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN external_url VARCHAR(500)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add sliced_for_model column to print_archives for model-based queue assignment
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN sliced_for_model VARCHAR(50)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add is_external column to library_files for external cloud files
    try:
        await conn.execute(text("ALTER TABLE library_files ADD COLUMN is_external BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add project_id column to library_files
    try:
        await conn.execute(
            text("ALTER TABLE library_files ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Add is_external column to library_folders for external cloud folders
    try:
        await conn.execute(text("ALTER TABLE library_folders ADD COLUMN is_external BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add external folder settings columns to library_folders
    try:
        await conn.execute(text("ALTER TABLE library_folders ADD COLUMN external_readonly BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE library_folders ADD COLUMN external_show_hidden BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE library_folders ADD COLUMN external_path VARCHAR(500)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add plate_detection_enabled column to printers
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN plate_detection_enabled BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add plate detection ROI columns to printers
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN plate_detection_roi_x REAL"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN plate_detection_roi_y REAL"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN plate_detection_roi_w REAL"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN plate_detection_roi_h REAL"))
    except OperationalError:
        pass  # Already applied

    # Migration: Remove UNIQUE constraint from smart_plugs.printer_id
    # This allows HA scripts to coexist with regular plugs (scripts are for multi-device control)
    # SQLite requires table recreation to drop constraints
    try:
        # Check if we need to migrate (if UNIQUE constraint exists)
        result = await conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='smart_plugs'"))
        row = result.fetchone()
        if row and "printer_id INTEGER UNIQUE" in (row[0] or ""):
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
                       auto_on, auto_off, off_delay_mode, off_delay_minutes, off_temp_threshold,
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
    except OperationalError:
        pass  # Already applied

    # Migration: Add show_on_printer_card column to smart_plugs
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN show_on_printer_card BOOLEAN DEFAULT 1"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add MQTT smart plug fields (legacy)
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN mqtt_topic VARCHAR(200)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN mqtt_power_path VARCHAR(100)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN mqtt_energy_path VARCHAR(100)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN mqtt_state_path VARCHAR(100)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN mqtt_multiplier REAL DEFAULT 1.0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add enhanced MQTT smart plug fields (separate topics and multipliers)
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN mqtt_power_topic VARCHAR(200)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN mqtt_power_multiplier REAL DEFAULT 1.0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN mqtt_energy_topic VARCHAR(200)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN mqtt_energy_multiplier REAL DEFAULT 1.0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN mqtt_state_topic VARCHAR(200)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN mqtt_state_on_value VARCHAR(50)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Copy existing mqtt_topic to mqtt_power_topic for backward compatibility
    try:
        await conn.execute(
            text("""
            UPDATE smart_plugs
            SET mqtt_power_topic = mqtt_topic,
                mqtt_power_multiplier = mqtt_multiplier
            WHERE mqtt_topic IS NOT NULL AND mqtt_power_topic IS NULL
        """)
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Create groups table for permission-based access control
    try:
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
    except OperationalError:
        pass  # Already applied

    # Migration: Create user_groups association table
    try:
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
    except OperationalError:
        pass  # Already applied

    # Migration: Add model-based queue assignment columns to print_queue
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN target_model VARCHAR(50)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN required_filament_types TEXT"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN waiting_reason TEXT"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add nozzle_count column to printers (for dual-extruder detection)
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN nozzle_count INTEGER DEFAULT 1"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add print_hours_offset column to printers (baseline hours adjustment)
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN print_hours_offset REAL DEFAULT 0.0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add queue notification event columns to notification_providers
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_queue_job_added BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(
            text("ALTER TABLE notification_providers ADD COLUMN on_queue_job_assigned BOOLEAN DEFAULT 0")
        )
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_queue_job_started BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_queue_job_waiting BOOLEAN DEFAULT 1"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_queue_job_skipped BOOLEAN DEFAULT 1"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_queue_job_failed BOOLEAN DEFAULT 1"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_queue_completed BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add created_by_id column to print_archives for user tracking (Issue #206)
    try:
        await conn.execute(
            text("ALTER TABLE print_archives ADD COLUMN created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Add created_by_id column to print_queue for user tracking (Issue #206)
    try:
        await conn.execute(
            text("ALTER TABLE print_queue ADD COLUMN created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Add created_by_id column to library_files for user tracking (Issue #206)
    try:
        await conn.execute(
            text("ALTER TABLE library_files ADD COLUMN created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Add target_location column to print_queue for location-based filtering (Issue #220)
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN target_location VARCHAR(100)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Convert absolute paths to relative paths in library_files table
    # This ensures backup/restore portability across different installations
    try:
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
    except OperationalError:
        pass  # Already applied

    # Create active_print_spoolman table for Spoolman per-filament tracking
    try:
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
    except OperationalError:
        pass  # Already applied

    # Migration: Add preset_source column to slot_preset_mappings for local preset support
    try:
        await conn.execute(
            text("ALTER TABLE slot_preset_mappings ADD COLUMN preset_source VARCHAR(20) DEFAULT 'cloud'")
        )
    except OperationalError:
        pass  # Already applied

    # Migration: Add email column to users for Advanced Auth (PR #322)
    try:
        await conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(255)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add inventory spool tracking columns
    try:
        await conn.execute(text("ALTER TABLE spool ADD COLUMN added_full BOOLEAN"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE spool ADD COLUMN last_used DATETIME"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE spool ADD COLUMN encode_time DATETIME"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add RFID tag matching columns to spool
    try:
        await conn.execute(text("ALTER TABLE spool ADD COLUMN tag_uid VARCHAR(16)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE spool ADD COLUMN tray_uuid VARCHAR(32)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE spool ADD COLUMN data_origin VARCHAR(20)"))
    except OperationalError:
        pass  # Already applied
    try:
        await conn.execute(text("ALTER TABLE spool ADD COLUMN tag_type VARCHAR(20)"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add core_weight_catalog_id to track which catalog entry was used for empty spool weight
    try:
        await conn.execute(text("ALTER TABLE spool ADD COLUMN core_weight_catalog_id INTEGER"))
    except OperationalError:
        pass  # Already applied

    # Migration: Create spool_usage_history table for filament consumption tracking
    try:
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
    except OperationalError:
        pass  # Already applied

    # Migration: Add open_in_new_tab column to external_links
    try:
        await conn.execute(text("ALTER TABLE external_links ADD COLUMN open_in_new_tab BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Add bed cooled notification column to notification_providers
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_bed_cooled BOOLEAN DEFAULT 0"))
    except OperationalError:
        pass  # Already applied

    # Migration: Migrate single virtual printer key-value settings to virtual_printers table
    try:
        # Check if virtual_printers table has any rows
        result = await conn.execute(text("SELECT COUNT(*) FROM virtual_printers"))
        count = result.scalar() or 0

        if count == 0:
            # Check if old key-value settings exist
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
                old_model = row[0] if row else "3DPrinter-X1-Carbon"

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
    except OperationalError:
        pass  # Table may not exist yet on first run


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
