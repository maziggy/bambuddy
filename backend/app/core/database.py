from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
)

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
        api_key,
        archive,
        external_link,
        filament,
        kprofile_note,
        library,
        maintenance,
        notification,
        notification_template,
        print_queue,
        printer,
        project,
        project_bom,
        settings,
        smart_plug,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Run migrations for new columns (SQLite doesn't auto-add columns)
        await run_migrations(conn)

    # Seed default notification templates
    await seed_notification_templates()


async def run_migrations(conn):
    """Add new columns to existing tables if they don't exist."""
    from sqlalchemy import text

    # Migration: Add is_favorite column to print_archives
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN is_favorite BOOLEAN DEFAULT 0"))
    except Exception:
        # Column already exists
        pass

    # Migration: Add content_hash column to print_archives for duplicate detection
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN content_hash VARCHAR(64)"))
    except Exception:
        # Column already exists
        pass

    # Migration: Add auto_off_executed column to smart_plugs
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN auto_off_executed BOOLEAN DEFAULT 0"))
    except Exception:
        # Column already exists
        pass

    # Migration: Add on_print_stopped column to notification_providers
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_print_stopped BOOLEAN DEFAULT 1"))
    except Exception:
        # Column already exists
        pass

    # Migration: Add source_3mf_path column to print_archives
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN source_3mf_path VARCHAR(500)"))
    except Exception:
        # Column already exists
        pass

    # Migration: Add f3d_path column to print_archives for Fusion 360 design files
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN f3d_path VARCHAR(500)"))
    except Exception:
        # Column already exists
        pass

    # Migration: Add on_maintenance_due column to notification_providers
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_maintenance_due BOOLEAN DEFAULT 0"))
    except Exception:
        # Column already exists
        pass

    # Migration: Add location column to printers for grouping
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN location VARCHAR(100)"))
    except Exception:
        # Column already exists
        pass

    # Migration: Add interval_type column to maintenance_types
    try:
        await conn.execute(text("ALTER TABLE maintenance_types ADD COLUMN interval_type VARCHAR(20) DEFAULT 'hours'"))
    except Exception:
        # Column already exists
        pass

    # Migration: Add custom_interval_type column to printer_maintenance
    try:
        await conn.execute(text("ALTER TABLE printer_maintenance ADD COLUMN custom_interval_type VARCHAR(20)"))
    except Exception:
        # Column already exists
        pass

    # Migration: Add power alert columns to smart_plugs
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN power_alert_enabled BOOLEAN DEFAULT 0"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN power_alert_high REAL"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN power_alert_low REAL"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN power_alert_last_triggered DATETIME"))
    except Exception:
        pass

    # Migration: Add schedule columns to smart_plugs
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN schedule_enabled BOOLEAN DEFAULT 0"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN schedule_on_time VARCHAR(5)"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN schedule_off_time VARCHAR(5)"))
    except Exception:
        pass

    # Migration: Add daily digest columns to notification_providers
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN daily_digest_enabled BOOLEAN DEFAULT 0"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN daily_digest_time VARCHAR(5)"))
    except Exception:
        pass

    # Migration: Add project_id column to print_archives
    try:
        await conn.execute(
            text("ALTER TABLE print_archives ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        )
    except Exception:
        pass

    # Migration: Add project_id column to print_queue
    try:
        await conn.execute(
            text("ALTER TABLE print_queue ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        )
    except Exception:
        pass

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
    except Exception:
        pass

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
    except Exception:
        pass

    try:
        await conn.execute(
            text("""
            CREATE TRIGGER IF NOT EXISTS archive_fts_delete AFTER DELETE ON print_archives BEGIN
                INSERT INTO archive_fts(archive_fts, rowid, print_name, filename, tags, notes, designer, filament_type)
                VALUES ('delete', old.id, old.print_name, old.filename, old.tags, old.notes, old.designer, old.filament_type);
            END
        """)
        )
    except Exception:
        pass

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
    except Exception:
        pass

    # Migration: Add auto_off_pending columns to smart_plugs (for restart recovery)
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN auto_off_pending BOOLEAN DEFAULT 0"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN auto_off_pending_since DATETIME"))
    except Exception:
        pass

    # Migration: Add AMS alarm notification columns to notification_providers
    try:
        await conn.execute(text("ALTER TABLE notification_providers ADD COLUMN on_ams_humidity_high BOOLEAN DEFAULT 0"))
    except Exception:
        pass
    try:
        await conn.execute(
            text("ALTER TABLE notification_providers ADD COLUMN on_ams_temperature_high BOOLEAN DEFAULT 0")
        )
    except Exception:
        pass

    # Migration: Add AMS-HT alarm notification columns to notification_providers
    try:
        await conn.execute(
            text("ALTER TABLE notification_providers ADD COLUMN on_ams_ht_humidity_high BOOLEAN DEFAULT 0")
        )
    except Exception:
        pass
    try:
        await conn.execute(
            text("ALTER TABLE notification_providers ADD COLUMN on_ams_ht_temperature_high BOOLEAN DEFAULT 0")
        )
    except Exception:
        pass

    # Migration: Add notes column to projects (Phase 2)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN notes TEXT"))
    except Exception:
        pass

    # Migration: Add attachments column to projects (Phase 3)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN attachments JSON"))
    except Exception:
        pass

    # Migration: Add tags column to projects (Phase 4)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN tags TEXT"))
    except Exception:
        pass

    # Migration: Add due_date column to projects (Phase 5)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN due_date DATETIME"))
    except Exception:
        pass

    # Migration: Add priority column to projects (Phase 5)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN priority VARCHAR(20) DEFAULT 'normal'"))
    except Exception:
        pass

    # Migration: Add budget column to projects (Phase 6)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN budget REAL"))
    except Exception:
        pass

    # Migration: Add is_template column to projects (Phase 8)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN is_template BOOLEAN DEFAULT 0"))
    except Exception:
        pass

    # Migration: Add template_source_id column to projects (Phase 8)
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN template_source_id INTEGER"))
    except Exception:
        pass

    # Migration: Add parent_id column to projects (Phase 10)
    try:
        await conn.execute(
            text("ALTER TABLE projects ADD COLUMN parent_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        )
    except Exception:
        pass

    # Migration: Rename quantity_printed to quantity_acquired in project_bom_items
    try:
        await conn.execute(text("ALTER TABLE project_bom_items RENAME COLUMN quantity_printed TO quantity_acquired"))
    except Exception:
        pass

    # Migration: Add unit_price column to project_bom_items
    try:
        await conn.execute(text("ALTER TABLE project_bom_items ADD COLUMN unit_price REAL"))
    except Exception:
        pass

    # Migration: Add sourcing_url column to project_bom_items
    try:
        await conn.execute(text("ALTER TABLE project_bom_items ADD COLUMN sourcing_url VARCHAR(512)"))
    except Exception:
        pass

    # Migration: Rename notes to remarks in project_bom_items
    try:
        await conn.execute(text("ALTER TABLE project_bom_items RENAME COLUMN notes TO remarks"))
    except Exception:
        pass

    # Migration: Add show_in_switchbar column to smart_plugs
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN show_in_switchbar BOOLEAN DEFAULT 0"))
    except Exception:
        pass

    # Migration: Add runtime tracking columns to printers
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN runtime_seconds INTEGER DEFAULT 0"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN last_runtime_update DATETIME"))
    except Exception:
        pass

    # Migration: Add quantity column to print_archives for tracking item count
    try:
        await conn.execute(text("ALTER TABLE print_archives ADD COLUMN quantity INTEGER DEFAULT 1"))
    except Exception:
        pass

    # Migration: Add manual_start column to print_queue for staged prints
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN manual_start BOOLEAN DEFAULT 0"))
    except Exception:
        pass

    # Migration: Add wiki_url column to maintenance_types for documentation links
    try:
        await conn.execute(text("ALTER TABLE maintenance_types ADD COLUMN wiki_url VARCHAR(500)"))
    except Exception:
        pass

    # Migration: Add ams_mapping column to print_queue for storing filament slot assignments
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN ams_mapping TEXT"))
    except Exception:
        pass

    # Migration: Add target_parts_count column to projects for tracking total parts needed
    try:
        await conn.execute(text("ALTER TABLE projects ADD COLUMN target_parts_count INTEGER"))
    except Exception:
        pass

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
    except Exception:
        pass

    # Migration: Add plug_type column to smart_plugs for HA integration
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN plug_type VARCHAR(20) DEFAULT 'tasmota'"))
    except Exception:
        pass

    # Migration: Add ha_entity_id column to smart_plugs for HA integration
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN ha_entity_id VARCHAR(100)"))
    except Exception:
        pass

    # Migration: Add project_id column to library_folders for linking folders to projects
    try:
        await conn.execute(
            text("ALTER TABLE library_folders ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        )
    except Exception:
        pass

    # Migration: Add archive_id column to library_folders for linking folders to archives
    try:
        await conn.execute(
            text(
                "ALTER TABLE library_folders ADD COLUMN archive_id INTEGER REFERENCES print_archives(id) ON DELETE SET NULL"
            )
        )
    except Exception:
        pass

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
    except Exception:
        pass

    # Migration: Add plate_id column to print_queue for multi-plate 3MF support
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN plate_id INTEGER"))
    except Exception:
        pass

    # Migration: Add print options columns to print_queue
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN bed_levelling BOOLEAN DEFAULT 1"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN flow_cali BOOLEAN DEFAULT 0"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN vibration_cali BOOLEAN DEFAULT 1"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN layer_inspect BOOLEAN DEFAULT 0"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN timelapse BOOLEAN DEFAULT 0"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE print_queue ADD COLUMN use_ams BOOLEAN DEFAULT 1"))
    except Exception:
        pass

    # Migration: Add library_file_id column to print_queue and make archive_id nullable
    # This allows queue items to reference library files directly (archive created at print start)
    try:
        await conn.execute(
            text(
                "ALTER TABLE print_queue ADD COLUMN library_file_id INTEGER REFERENCES library_files(id) ON DELETE CASCADE"
            )
        )
    except Exception:
        pass

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
    except Exception:
        pass

    # Migration: Add HA energy sensor entity columns to smart_plugs
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN ha_power_entity VARCHAR(100)"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN ha_energy_today_entity VARCHAR(100)"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE smart_plugs ADD COLUMN ha_energy_total_entity VARCHAR(100)"))
    except Exception:
        pass

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
    except Exception:
        pass


async def seed_notification_templates():
    """Seed default notification templates if they don't exist."""
    from sqlalchemy import select

    from backend.app.models.notification_template import DEFAULT_TEMPLATES, NotificationTemplate

    async with async_session() as session:
        # Check if templates already exist
        result = await session.execute(select(NotificationTemplate).limit(1))
        if result.scalar_one_or_none() is not None:
            # Templates already seeded
            return

        # Insert default templates
        for template_data in DEFAULT_TEMPLATES:
            template = NotificationTemplate(
                event_type=template_data["event_type"],
                name=template_data["name"],
                title_template=template_data["title_template"],
                body_template=template_data["body_template"],
                is_default=True,
            )
            session.add(template)

        await session.commit()
