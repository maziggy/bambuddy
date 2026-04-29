import io
import logging
import os
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled, caller_is_api_key
from backend.app.core.config import settings as app_settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.schemas.settings import AppSettings, AppSettingsUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

DEFAULT_SETTINGS = AppSettings()

# Sensitive credential fields blanked for API-key callers (B1)
_SENSITIVE_FIELDS_FOR_API_KEY = (
    "mqtt_password",
    "ha_token",
    "prometheus_token",
    "virtual_printer_access_code",
    "ldap_bind_password",
)


async def get_setting(db: AsyncSession, key: str) -> str | None:
    """Get a single setting value by key."""
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def get_external_login_url(db: AsyncSession) -> str:
    """Get the external URL for the login page.

    Uses external_url from settings if available, otherwise falls back to APP_URL env var.

    Args:
        db: Database session

    Returns:
        Full URL to the login page
    """
    import os

    external_url = await get_setting(db, "external_url")
    if external_url:
        external_url = external_url.rstrip("/")
    else:
        external_url = os.environ.get("APP_URL", "http://localhost:5173")
    return external_url + "/login"


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    """Set a single setting value."""
    from backend.app.core.db_dialect import upsert_setting

    await upsert_setting(db, Settings, key, value)


async def _build_settings_response(db: AsyncSession, is_api_key: bool = False) -> AppSettings:
    """Build the full settings response, scrubbing secrets for API-key callers."""
    settings_dict = DEFAULT_SETTINGS.model_dump()

    result = await db.execute(select(Settings))
    for setting in result.scalars().all():
        if setting.key not in settings_dict:
            continue
        if setting.key in [
            "auto_archive",
            "save_thumbnails",
            "capture_finish_photo",
            "spoolman_enabled",
            "spoolman_disable_weight_sync",
            "spoolman_report_partial_usage",
            "disable_filament_warnings",
            "prefer_lowest_filament",
            "check_updates",
            "check_printer_firmware",
            "include_beta_updates",
            "virtual_printer_enabled",
            "ftp_retry_enabled",
            "mqtt_enabled",
            "mqtt_use_tls",
            "ha_enabled",
            "per_printer_mapping_expanded",
            "prometheus_enabled",
            "user_notifications_enabled",
            "queue_drying_enabled",
            "queue_drying_block",
            "ambient_drying_enabled",
            "require_plate_clear",
            "queue_shortest_first",
            "default_bed_levelling",
            "default_flow_cali",
            "default_vibration_cali",
            "default_layer_inspect",
            "default_timelapse",
            "ldap_enabled",
            "ldap_auto_provision",
        ]:
            settings_dict[setting.key] = setting.value.lower() == "true"
        elif setting.key in [
            "default_filament_cost",
            "energy_cost_per_kwh",
            "ams_temp_good",
            "ams_temp_fair",
            "library_disk_warning_gb",
            "low_stock_threshold",
        ]:
            settings_dict[setting.key] = float(setting.value)
        elif setting.key in [
            "ams_humidity_good",
            "ams_humidity_fair",
            "ams_history_retention_days",
            "ftp_retry_count",
            "ftp_retry_delay",
            "ftp_timeout",
            "mqtt_port",
            "stagger_group_size",
            "stagger_interval_minutes",
        ]:
            settings_dict[setting.key] = int(setting.value)
        elif setting.key == "default_printer_id":
            settings_dict[setting.key] = int(setting.value) if setting.value and setting.value != "None" else None
        else:
            settings_dict[setting.key] = setting.value

    ha_settings = await get_homeassistant_settings(db)
    settings_dict.update(ha_settings)

    # ldap_bind_password is never returned to any caller
    settings_dict["ldap_bind_password"] = ""

    if is_api_key:
        for field in _SENSITIVE_FIELDS_FOR_API_KEY:
            if field in settings_dict:
                settings_dict[field] = ""

    return AppSettings(**settings_dict)


@router.get("", response_model=AppSettings)
@router.get("/", response_model=AppSettings)
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
    _is_api_key: bool = Depends(caller_is_api_key),
):
    """Get all application settings."""
    return await _build_settings_response(db, is_api_key=_is_api_key)


@router.put("/", response_model=AppSettings)
async def update_settings(
    settings_update: AppSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Update application settings."""
    update_data = settings_update.model_dump(exclude_unset=True)

    # Check if any MQTT settings are being updated
    mqtt_keys = {
        "mqtt_enabled",
        "mqtt_broker",
        "mqtt_port",
        "mqtt_username",
        "mqtt_password",
        "mqtt_topic_prefix",
        "mqtt_use_tls",
    }
    mqtt_updated = bool(mqtt_keys & set(update_data.keys()))

    for key, value in update_data.items():
        # Convert value to string for storage
        if isinstance(value, bool):
            str_value = "true" if value else "false"
        elif value is None:
            str_value = "None"
        else:
            str_value = str(value)
        await set_setting(db, key, str_value)

    await db.commit()
    # Expire all objects to ensure fresh reads after commit
    db.expire_all()

    # Reconfigure MQTT relay if any MQTT settings changed
    if mqtt_updated:
        try:
            from backend.app.services.mqtt_relay import mqtt_relay

            mqtt_settings = {
                "mqtt_enabled": (await get_setting(db, "mqtt_enabled") or "false") == "true",
                "mqtt_broker": await get_setting(db, "mqtt_broker") or "",
                "mqtt_port": int(await get_setting(db, "mqtt_port") or "1883"),
                "mqtt_username": await get_setting(db, "mqtt_username") or "",
                "mqtt_password": await get_setting(db, "mqtt_password") or "",
                "mqtt_topic_prefix": await get_setting(db, "mqtt_topic_prefix") or "bambuddy",
                "mqtt_use_tls": (await get_setting(db, "mqtt_use_tls") or "false") == "true",
            }
            await mqtt_relay.configure(mqtt_settings)
        except Exception:
            pass  # Don't fail the settings update if MQTT reconfiguration fails

    # Return updated settings (never scrub secrets on PUT — caller has SETTINGS_UPDATE permission)
    return await _build_settings_response(db, is_api_key=False)


@router.patch("/", response_model=AppSettings)
@router.patch("", response_model=AppSettings)
async def patch_settings(
    settings_update: AppSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Partially update application settings (same as PUT, for REST compatibility)."""
    return await update_settings(settings_update, db, _)


@router.post("/reset", response_model=AppSettings)
async def reset_settings(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Reset all settings to defaults."""
    # Delete all settings
    result = await db.execute(select(Settings))
    for setting in result.scalars().all():
        await db.delete(setting)

    await db.commit()

    return DEFAULT_SETTINGS


@router.get("/default-sidebar-order")
async def get_default_sidebar_order(
    db: AsyncSession = Depends(get_db),
):
    """Get the admin-set default sidebar order.

    Intentionally unauthenticated: non-admin users need to read this value to apply
    the default sidebar order, but may lack SETTINGS_READ permission.
    The value is non-sensitive (sidebar item IDs only).
    """
    value = await get_setting(db, "default_sidebar_order")
    return {"default_sidebar_order": value or ""}


@router.get("/check-ffmpeg")
async def check_ffmpeg():
    """Check if ffmpeg is installed and available."""
    from backend.app.services.camera import get_ffmpeg_path

    ffmpeg_path = get_ffmpeg_path()

    return {
        "installed": ffmpeg_path is not None,
        "path": ffmpeg_path,
    }


@router.get("/spoolman")
async def get_spoolman_settings(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get Spoolman integration settings."""
    spoolman_enabled = await get_setting(db, "spoolman_enabled") or "false"
    spoolman_url = await get_setting(db, "spoolman_url") or ""
    spoolman_sync_mode = await get_setting(db, "spoolman_sync_mode") or "auto"
    spoolman_disable_weight_sync = await get_setting(db, "spoolman_disable_weight_sync") or "false"
    spoolman_report_partial_usage = await get_setting(db, "spoolman_report_partial_usage") or "true"

    return {
        "spoolman_enabled": spoolman_enabled,
        "spoolman_url": spoolman_url,
        "spoolman_sync_mode": spoolman_sync_mode,
        "spoolman_disable_weight_sync": spoolman_disable_weight_sync,
        "spoolman_report_partial_usage": spoolman_report_partial_usage,
    }


@router.put("/spoolman")
async def update_spoolman_settings(
    settings: dict,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Update Spoolman integration settings."""
    if "spoolman_enabled" in settings:
        old_val = await get_setting(db, "spoolman_enabled") or "false"
        new_val = settings["spoolman_enabled"]
        await set_setting(db, "spoolman_enabled", new_val)

        # Switching to Spoolman: clear built-in inventory slot assignments
        if old_val.lower() != "true" and new_val.lower() == "true":
            from backend.app.models.spool_assignment import SpoolAssignment

            result = await db.execute(delete(SpoolAssignment))
            logger.info("Cleared %d spool assignments on switch to Spoolman mode", result.rowcount)
    if "spoolman_url" in settings:
        await set_setting(db, "spoolman_url", settings["spoolman_url"])
    if "spoolman_sync_mode" in settings:
        await set_setting(db, "spoolman_sync_mode", settings["spoolman_sync_mode"])
    if "spoolman_disable_weight_sync" in settings:
        await set_setting(db, "spoolman_disable_weight_sync", settings["spoolman_disable_weight_sync"])
    if "spoolman_report_partial_usage" in settings:
        await set_setting(db, "spoolman_report_partial_usage", settings["spoolman_report_partial_usage"])

    await db.commit()
    db.expire_all()

    # Return updated settings
    return await get_spoolman_settings(db)


async def get_homeassistant_settings(db: AsyncSession) -> dict:
    """
    Get Home Assistant integration settings.
    Environment variables (HA_URL, HA_TOKEN) take precedence over database settings.
    """
    import os

    # Check environment variables first
    ha_url_env = os.environ.get("HA_URL")
    ha_token_env = os.environ.get("HA_TOKEN")

    # Fall back to database values
    ha_url = ha_url_env or await get_setting(db, "ha_url") or ""
    ha_token = ha_token_env or await get_setting(db, "ha_token") or ""
    ha_enabled_db = await get_setting(db, "ha_enabled") or "false"

    # Track which settings come from environment
    ha_url_from_env = bool(ha_url_env)
    ha_token_from_env = bool(ha_token_env)
    ha_env_managed = ha_url_from_env and ha_token_from_env

    # Auto-enable when both env vars are set, otherwise use database value
    if ha_url_env and ha_token_env:
        ha_enabled = True
    else:
        ha_enabled = ha_enabled_db.lower() == "true"

    return {
        "ha_enabled": ha_enabled,
        "ha_url": ha_url,
        "ha_token": ha_token,
        "ha_url_from_env": ha_url_from_env,
        "ha_token_from_env": ha_token_from_env,
        "ha_env_managed": ha_env_managed,
    }


async def create_backup_zip(output_path: Path | None = None) -> tuple[Path, str]:
    """Create a complete backup ZIP (database + all data directories).

    If output_path is given, the ZIP is written there.
    Otherwise a temporary file is created (caller must clean up).
    Returns (zip_path, filename).
    """
    import shutil
    import tempfile

    from backend.app.core.db_dialect import is_sqlite

    base_dir = app_settings.base_dir
    filename = f"bambuddy-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        if is_sqlite():
            from sqlalchemy import text

            from backend.app.core.database import engine

            db_path = Path(app_settings.database_url.replace("sqlite+aiosqlite:///", ""))

            # Checkpoint WAL to ensure all data is in main db file
            async with engine.begin() as conn:
                await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))

            # Copy database file
            shutil.copy2(db_path, temp_path / "bambuddy.db")
        else:
            # PostgreSQL: export to a portable SQLite file via SQLAlchemy.
            # This makes backups restorable on both SQLite and Postgres installs.
            import json
            import sqlite3

            from backend.app.core.database import Base, engine

            backup_db_path = temp_path / "bambuddy.db"
            dst = sqlite3.connect(str(backup_db_path))
            metadata = Base.metadata

            # Create tables in SQLite backup (simplified — just column names and types)
            for table in metadata.sorted_tables:
                cols = []
                pk_cols = [col.name for col in table.columns if col.primary_key]
                for col in table.columns:
                    col_type = "TEXT"  # Default
                    type_str = str(col.type).upper()
                    if "INT" in type_str:
                        col_type = "INTEGER"
                    elif "FLOAT" in type_str or "REAL" in type_str or "NUMERIC" in type_str:
                        col_type = "REAL"
                    elif "BOOL" in type_str:
                        col_type = "BOOLEAN"
                    # Only inline PRIMARY KEY for single-column PKs
                    pk = " PRIMARY KEY" if col.primary_key and len(pk_cols) == 1 else ""
                    cols.append(f"{col.name} {col_type}{pk}")
                # Add composite primary key constraint if needed
                if len(pk_cols) > 1:
                    cols.append(f"PRIMARY KEY ({', '.join(pk_cols)})")
                dst.execute(f"CREATE TABLE IF NOT EXISTS {table.name} ({', '.join(cols)})")  # noqa: S608

            # Export data from Postgres to SQLite
            async with engine.connect() as conn:
                for table in metadata.sorted_tables:
                    result = await conn.execute(table.select())
                    rows = result.fetchall()
                    if not rows:
                        continue
                    columns = list(result.keys())
                    placeholders = ", ".join(["?"] * len(columns))
                    col_list = ", ".join(columns)
                    insert_sql = f"INSERT INTO {table.name} ({col_list}) VALUES ({placeholders})"  # noqa: S608  # nosec B608 — table/column names from ORM metadata, not user input

                    def _serialize_row(row):
                        return tuple(json.dumps(v) if isinstance(v, (list, dict)) else v for v in row)

                    dst.executemany(insert_sql, [_serialize_row(row) for row in rows])

            dst.commit()
            dst.close()
            logger.info("PostgreSQL backup exported to portable SQLite format")

        # Copy data directories (if they exist)
        dirs_to_backup = [
            ("archive", base_dir / "archive"),
            ("virtual_printer", base_dir / "virtual_printer"),
            ("plate_calibration", app_settings.plate_calibration_dir),
            ("icons", base_dir / "icons"),
            ("projects", base_dir / "projects"),
        ]

        for name, src_dir in dirs_to_backup:
            if src_dir.exists() and any(src_dir.iterdir()):
                try:
                    shutil.copytree(src_dir, temp_path / name)
                except shutil.Error as e:
                    logger.warning("Some files in %s could not be copied: %s", name, e)
                except PermissionError as e:
                    logger.warning("Permission denied copying %s: %s", name, e)

        # Create ZIP
        if output_path is not None:
            zip_file = output_path / filename
        else:
            fd, tmp = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            zip_file = Path(tmp)

        with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in temp_path.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(temp_path)
                    zf.write(file_path, arcname)

    return zip_file, filename


@router.get("/backup")
async def create_backup(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_BACKUP),
):
    """Create a complete backup (database + all files) as a ZIP download."""
    from starlette.background import BackgroundTask

    try:
        zip_file, filename = await create_backup_zip()
        return FileResponse(
            path=zip_file,
            filename=filename,
            media_type="application/zip",
            background=BackgroundTask(lambda: zip_file.unlink(missing_ok=True)),
        )
    except Exception as e:
        logger.error("Backup failed: %s", e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Backup failed. Check server logs for details."},
        )


async def _import_sqlite_to_postgres(sqlite_path: Path, postgres_url: str):
    """Import data from a SQLite database file into the current PostgreSQL database.

    Used for cross-database restore (SQLite backup → PostgreSQL).
    Reads all tables from the SQLite file and bulk-inserts into Postgres.
    """
    import sqlite3

    from sqlalchemy import text

    from backend.app.core.database import Base, _create_engine

    # Create a temporary engine for the import (current engine was disposed)
    pg_engine = _create_engine()

    try:
        # Open SQLite file directly (sync — it's a local file read)
        src = sqlite3.connect(str(sqlite_path))
        src.row_factory = sqlite3.Row

        # Get list of tables from SQLite (skip internal/FTS tables)
        cursor = src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'archive_fts%'"
        )
        src_tables = {row["name"] for row in cursor.fetchall()}

        # Get Postgres tables from our ORM models
        metadata = Base.metadata
        pg_tables = set(metadata.tables.keys())

        # Only import tables that exist in both source and destination
        tables_to_import = src_tables & pg_tables
        sorted_tables = [t.name for t in metadata.sorted_tables if t.name in tables_to_import]

        # Phase 1: Drop all tables and recreate WITHOUT foreign keys.
        # This avoids all FK ordering/orphan issues during import.
        saved_fks = {}
        for table in metadata.sorted_tables:
            fks = list(table.foreign_key_constraints)
            if fks:
                saved_fks[table.name] = fks
                for fk in fks:
                    table.constraints.discard(fk)

        async with pg_engine.begin() as conn:
            # Drop every existing table in the public schema with CASCADE
            # rather than `metadata.drop_all`. Two reasons:
            #   1. The user's live DB may carry orphan tables from removed
            #      features (e.g. the legacy `spoolman_slot_assignments`,
            #      `spoolman_k_profile`) that hold FK constraints back to
            #      ORM tables. `drop_all` doesn't know they exist and emits
            #      `DROP TABLE printers` without CASCADE — Postgres refuses
            #      and the whole restore aborts (#XXXX).
            #   2. Even within the metadata, `drop_all` is FK-ordered and
            #      breaks if a future schema rename leaves old constraints
            #      around. CASCADE is the right tool for a destructive
            #      restore: the user is intentionally wiping state.
            await conn.execute(
                text(
                    "DO $$ DECLARE r RECORD; BEGIN "
                    "FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP "
                    "EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE'; "
                    "END LOOP; END $$;"
                )
            )
            await conn.run_sync(metadata.create_all)

        # Restore FK definitions in metadata (needed for re-adding later)
        for table_name, fks in saved_fks.items():
            table_obj = metadata.tables[table_name]
            for fk in fks:
                table_obj.constraints.add(fk)

        # Phase 2: Import data (no FKs to worry about)
        async with pg_engine.begin() as conn:
            # Import each table in dependency order (parents before children)
            for table_name in sorted_tables:
                rows = src.execute(f"SELECT * FROM {table_name}").fetchall()  # noqa: S608  # nosec B608
                if not rows:
                    continue

                # Filter to columns that exist in the Postgres table
                src_columns = rows[0].keys()
                pg_table = metadata.tables.get(table_name)
                pg_columns = {c.name for c in pg_table.columns} if pg_table is not None else set()
                columns = [c for c in src_columns if c in pg_columns]

                if not columns:
                    continue

                col_list = ", ".join(columns)
                param_list = ", ".join(f":{c}" for c in columns)
                # ON CONFLICT DO NOTHING handles duplicate rows from SQLite (which doesn't enforce unique constraints)
                insert_sql = text(f"INSERT INTO {table_name} ({col_list}) VALUES ({param_list}) ON CONFLICT DO NOTHING")  # noqa: S608  # nosec B608

                # Identify columns that need type conversion (SQLite stores booleans
                # as int and datetimes as str — asyncpg requires native Python types)
                from datetime import datetime as dt

                bool_columns = set()
                datetime_columns = set()
                not_null_defaults = {}  # col_name -> default value for NOT NULL columns
                if pg_table is not None:
                    for col in pg_table.columns:
                        if col.name not in columns:
                            continue
                        col_type = str(col.type)
                        if col_type == "BOOLEAN":
                            bool_columns.add(col.name)
                        elif col_type in ("DATETIME", "TIMESTAMP WITHOUT TIME ZONE", "TIMESTAMP WITH TIME ZONE"):
                            datetime_columns.add(col.name)
                        # Track NOT NULL columns with defaults — older backups may have NULL
                        # for columns added after the backup was created
                        if not col.nullable:
                            if col.default is not None:
                                default = col.default.arg
                                if callable(default):
                                    default = default(None)
                                not_null_defaults[col.name] = default
                            elif col.server_default is not None:
                                # server_default=func.now() → use current timestamp
                                if col.name in datetime_columns:
                                    not_null_defaults[col.name] = "__now__"
                                else:
                                    # Try to extract literal server default
                                    sd = str(col.server_default.arg) if hasattr(col.server_default, "arg") else None
                                    if sd is not None:
                                        not_null_defaults[col.name] = sd

                now = dt.now()

                def _convert_row(
                    row, cols=columns, bools=bool_columns, dts=datetime_columns, nn_defaults=not_null_defaults, _now=now
                ):
                    result = {}
                    for c in cols:
                        val = row[c]
                        if val is None and c in nn_defaults:
                            val = _now if nn_defaults[c] == "__now__" else nn_defaults[c]
                        if val is not None:
                            if c in bools:
                                val = bool(val)
                            elif c in dts and isinstance(val, str):
                                try:
                                    val = dt.fromisoformat(val)
                                except ValueError:
                                    pass
                        result[c] = val
                    return result

                batch = [_convert_row(row) for row in rows]
                await conn.execute(insert_sql, batch)
                logger.info("Imported %d rows into %s", len(batch), table_name)

            # Reset sequences to max(id) + 1 for each table with an id column
            for table_name in sorted_tables:
                try:
                    async with conn.begin_nested():
                        result = await conn.execute(text(f"SELECT MAX(id) FROM {table_name}"))  # noqa: S608  # nosec B608
                        max_id = result.scalar()
                        if max_id is not None:
                            seq_name = f"{table_name}_id_seq"
                            await conn.execute(text(f"SELECT setval('{seq_name}', {max_id})"))  # noqa: S608
                except Exception:
                    pass  # Table may not have an id column or sequence

        src.close()
        logger.info("Cross-database import complete: %d tables imported", len(tables_to_import))

        # Recreate FK constraints from ORM metadata (not from saved definitions).
        # Use individual transactions so orphaned SQLite data doesn't block valid FKs.
        from sqlalchemy.schema import AddConstraint

        failed_fks = []
        for table in metadata.sorted_tables:
            for fk in table.foreign_key_constraints:
                try:
                    async with pg_engine.begin() as fk_conn:
                        await fk_conn.execute(AddConstraint(fk))
                except Exception:
                    failed_fks.append(f"{table.name}.{fk.name}")
        if failed_fks:
            logger.warning(
                "Could not restore %d FK constraints (orphaned data in SQLite): %s",
                len(failed_fks),
                ", ".join(failed_fks),
            )

    finally:
        await pg_engine.dispose()


@router.post("/restore")
async def restore_backup(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_RESTORE),
):
    """Restore from a complete backup ZIP.

    Replaces the database and all data directories from the backup ZIP.
    Requires a restart after restore.
    """
    import shutil
    import tempfile

    from fastapi import HTTPException

    from backend.app.core.database import close_all_connections, init_db, reinitialize_database
    from backend.app.core.db_dialect import is_sqlite
    from backend.app.services.virtual_printer import virtual_printer_manager

    base_dir = app_settings.base_dir

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # 1. Read and extract ZIP
        content = await file.read()

        # Check if it's a valid ZIP
        if not file.filename or not file.filename.endswith(".zip"):
            raise HTTPException(400, "Invalid backup file: must be a .zip file")

        try:
            with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
                zf.extractall(temp_path)
        except zipfile.BadZipFile:
            raise HTTPException(400, "Invalid backup file: not a valid ZIP")

        # 2. Validate backup
        backup_db = temp_path / "bambuddy.db"
        if not backup_db.exists():
            raise HTTPException(400, "Invalid backup: missing bambuddy.db")

        try:
            import asyncio

            # 3. Stop virtual printer if running (releases file locks)
            try:
                if virtual_printer_manager.is_enabled:
                    logger.info("Stopping virtual printer for restore...")
                    await virtual_printer_manager.configure(enabled=False)
                    await asyncio.sleep(1)
            except Exception as e:
                logger.warning("Failed to stop virtual printer: %s", e)

            # 4. Close current database connections
            logger.info("Closing database connections...")
            await close_all_connections()

            # 5. Replace database
            logger.info("Restoring database from backup...")
            if is_sqlite():
                db_path = Path(app_settings.database_url.replace("sqlite+aiosqlite:///", ""))
                shutil.copy2(backup_db, db_path)
            else:
                # Import SQLite backup into PostgreSQL
                logger.info("Importing SQLite backup into PostgreSQL...")
                await _import_sqlite_to_postgres(backup_db, app_settings.database_url)

            # 6. Replace data directories
            # For Docker compatibility: clear contents then copy (don't delete mount points)
            dirs_to_restore = [
                ("archive", base_dir / "archive"),
                ("virtual_printer", base_dir / "virtual_printer"),
                ("plate_calibration", app_settings.plate_calibration_dir),
                ("icons", base_dir / "icons"),
                ("projects", base_dir / "projects"),
            ]

            skipped_dirs = []
            for name, dest_dir in dirs_to_restore:
                src_dir = temp_path / name
                if src_dir.exists():
                    logger.info("Restoring %s directory...", name)
                    try:
                        # Clear destination contents (not the dir itself - may be Docker mount)
                        if dest_dir.exists():
                            for item in dest_dir.iterdir():
                                try:
                                    if item.is_dir():
                                        shutil.rmtree(item)
                                    else:
                                        item.unlink()
                                except OSError as e:
                                    logger.warning("Could not delete %s: %s", item, e)
                        else:
                            dest_dir.mkdir(parents=True, exist_ok=True)
                        # Copy contents from backup
                        for item in src_dir.iterdir():
                            dest_item = dest_dir / item.name
                            if item.is_dir():
                                shutil.copytree(item, dest_item)
                            else:
                                shutil.copy2(item, dest_item)
                    except OSError as e:
                        logger.warning("Could not restore %s directory: %s", name, e)
                        skipped_dirs.append(name)

            # 7. Reinitialize the database engine and apply schema migrations so that
            # tables added after the backup was created (e.g. ams_labels) exist
            # immediately, without requiring a manual restart.
            await reinitialize_database()
            await init_db()

            logger.info("Restore complete - restart required")
            message = "Backup restored successfully. Please restart Bambuddy for changes to take effect."
            if skipped_dirs:
                message += f" Note: Some directories could not be restored ({', '.join(skipped_dirs)})."
            return {
                "success": True,
                "message": message,
            }

        except Exception as e:
            logger.error("Restore failed: %s", e, exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": "Restore failed. Check server logs for details."},
            )


@router.get("/network-interfaces")
async def get_network_interfaces(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get available network interfaces with all IPs (primary + aliases)."""
    from backend.app.services.network_utils import get_all_interface_ips

    interfaces = get_all_interface_ips()
    return {"interfaces": interfaces}


@router.get("/virtual-printer/models")
async def get_virtual_printer_models(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get available virtual printer models."""
    from backend.app.services.virtual_printer import (
        DEFAULT_VIRTUAL_PRINTER_MODEL,
        VIRTUAL_PRINTER_MODELS,
    )

    return {
        "models": VIRTUAL_PRINTER_MODELS,
        "default": DEFAULT_VIRTUAL_PRINTER_MODEL,
    }


@router.get("/virtual-printer")
async def get_virtual_printer_settings(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get virtual printer settings and status."""
    from backend.app.services.virtual_printer import (
        DEFAULT_VIRTUAL_PRINTER_MODEL,
        virtual_printer_manager,
    )

    enabled = await get_setting(db, "virtual_printer_enabled")
    access_code = await get_setting(db, "virtual_printer_access_code")
    mode = await get_setting(db, "virtual_printer_mode")
    model = await get_setting(db, "virtual_printer_model")
    target_printer_id = await get_setting(db, "virtual_printer_target_printer_id")
    remote_interface_ip = await get_setting(db, "virtual_printer_remote_interface_ip")
    tailscale_disabled_raw = await get_setting(db, "virtual_printer_tailscale_disabled")
    archive_name_source = await get_setting(db, "virtual_printer_archive_name_source")

    return {
        "enabled": enabled == "true" if enabled else False,
        "access_code_set": bool(access_code),
        "mode": mode or "immediate",
        "model": model or DEFAULT_VIRTUAL_PRINTER_MODEL,
        "target_printer_id": int(target_printer_id) if target_printer_id else None,
        "remote_interface_ip": remote_interface_ip or "",
        "tailscale_disabled": tailscale_disabled_raw == "true" if tailscale_disabled_raw else True,
        "archive_name_source": archive_name_source if archive_name_source in ("metadata", "filename") else "metadata",
        "status": virtual_printer_manager.get_status(),
    }


@router.put("/virtual-printer")
async def update_virtual_printer_settings(
    enabled: bool = None,
    access_code: str = None,
    mode: str = None,
    model: str = None,
    target_printer_id: int = None,
    remote_interface_ip: str = None,
    tailscale_disabled: bool = None,
    archive_name_source: str = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Update virtual printer settings and restart services if needed.

    For proxy mode with SSDP proxy (dual-homed setup):
    - remote_interface_ip: IP of interface on slicer's network (LAN B)
    - Local interface is auto-detected based on target printer IP
    """
    from sqlalchemy import select

    from backend.app.models.printer import Printer
    from backend.app.services.virtual_printer import (
        DEFAULT_VIRTUAL_PRINTER_MODEL,
        VIRTUAL_PRINTER_MODELS,
        virtual_printer_manager,
    )

    # Get current values
    current_enabled = await get_setting(db, "virtual_printer_enabled") == "true"
    current_access_code = await get_setting(db, "virtual_printer_access_code") or ""
    current_mode = await get_setting(db, "virtual_printer_mode") or "immediate"
    current_model = await get_setting(db, "virtual_printer_model") or DEFAULT_VIRTUAL_PRINTER_MODEL
    current_target_id_str = await get_setting(db, "virtual_printer_target_printer_id")
    current_target_id = int(current_target_id_str) if current_target_id_str else None
    current_remote_iface = await get_setting(db, "virtual_printer_remote_interface_ip") or ""
    current_ts_disabled_raw = await get_setting(db, "virtual_printer_tailscale_disabled")
    # Default True (opt-in) when the setting has never been saved — matches the model default.
    current_ts_disabled = current_ts_disabled_raw == "true" if current_ts_disabled_raw else True

    # Apply updates
    new_enabled = enabled if enabled is not None else current_enabled
    new_access_code = access_code if access_code is not None else current_access_code
    new_mode = mode if mode is not None else current_mode
    new_model = model if model is not None else current_model
    new_target_id = target_printer_id if target_printer_id is not None else current_target_id
    new_remote_iface = remote_interface_ip if remote_interface_ip is not None else current_remote_iface
    new_ts_disabled = tailscale_disabled if tailscale_disabled is not None else current_ts_disabled

    # Guard: enabling Tailscale (disabled=False) requires the binary to be present. Otherwise
    # the toggle looks like it worked but the service will silently fall back to self-signed.
    if tailscale_disabled is False and current_ts_disabled is True:
        from backend.app.services.virtual_printer.tailscale import tailscale_service

        ts_status = await tailscale_service.get_status()
        if not ts_status.available:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "tailscale_not_available",
                    "reason": ts_status.error or "tailscale binary not found",
                },
            )

    # Validate mode
    # "review" is the new name for "queue" (pending review before archiving)
    # "print_queue" archives and adds to print queue (unassigned)
    # "proxy" is transparent TCP proxy to a real printer
    if new_mode not in ("immediate", "queue", "review", "print_queue", "proxy"):
        return JSONResponse(
            status_code=400,
            content={"detail": "Mode must be 'immediate', 'review', 'print_queue', or 'proxy'"},
        )

    # Validate archive_name_source
    if archive_name_source is not None and archive_name_source not in ("metadata", "filename"):
        return JSONResponse(
            status_code=400,
            content={"detail": "archive_name_source must be 'metadata' or 'filename'"},
        )
    # Normalize legacy "queue" to "review" for storage
    if new_mode == "queue":
        new_mode = "review"

    # Validate model
    if model is not None and model not in VIRTUAL_PRINTER_MODELS:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid model. Must be one of: {', '.join(VIRTUAL_PRINTER_MODELS.keys())}"},
        )

    # Mode-specific validation and printer lookup
    target_printer_ip = ""
    target_printer_serial = ""
    if new_mode == "proxy":
        # Proxy mode requires target printer when enabling
        if new_enabled and not new_target_id:
            # If just switching to proxy mode (not explicitly enabling), auto-disable
            if enabled is None:
                new_enabled = False
            else:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Target printer is required for proxy mode"},
                )

        # Look up printer IP and serial if we have a target
        if new_target_id:
            result = await db.execute(select(Printer).where(Printer.id == new_target_id))
            printer = result.scalar_one_or_none()
            if not printer:
                return JSONResponse(
                    status_code=400,
                    content={"detail": f"Printer with ID {new_target_id} not found"},
                )
            target_printer_ip = printer.ip_address
            target_printer_serial = printer.serial_number
        # Access code not required for proxy mode
    else:
        # Non-proxy modes require access code when enabling
        if new_enabled and not new_access_code:
            # If just switching modes (not explicitly enabling), auto-disable
            if enabled is None:
                new_enabled = False
            else:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Access code is required when enabling virtual printer"},
                )

        # Validate access code length (Bambu Studio requires exactly 8 characters)
        if access_code is not None and access_code and len(access_code) != 8:
            return JSONResponse(
                status_code=400,
                content={"detail": "Access code must be exactly 8 characters"},
            )

    # Save settings
    await set_setting(db, "virtual_printer_enabled", "true" if new_enabled else "false")
    if access_code is not None:
        await set_setting(db, "virtual_printer_access_code", access_code)
    await set_setting(db, "virtual_printer_mode", new_mode)
    if model is not None:
        await set_setting(db, "virtual_printer_model", model)
    if target_printer_id is not None:
        await set_setting(db, "virtual_printer_target_printer_id", str(target_printer_id))
    if remote_interface_ip is not None:
        await set_setting(db, "virtual_printer_remote_interface_ip", remote_interface_ip)
    if tailscale_disabled is not None:
        await set_setting(db, "virtual_printer_tailscale_disabled", "true" if tailscale_disabled else "false")
    if archive_name_source is not None:
        await set_setting(db, "virtual_printer_archive_name_source", archive_name_source)

    # Propagate tailscale_disabled to the first VirtualPrinter row so sync_from_db() picks it up
    if tailscale_disabled is not None:
        from backend.app.models.virtual_printer import VirtualPrinter as VPModel

        vp_result = await db.execute(select(VPModel).order_by(VPModel.position).limit(1))
        first_vp = vp_result.scalar_one_or_none()
        if first_vp is not None:
            first_vp.tailscale_disabled = new_ts_disabled

    await db.commit()
    db.expire_all()

    # Reconfigure virtual printer
    try:
        await virtual_printer_manager.configure(
            enabled=new_enabled,
            access_code=new_access_code,
            mode=new_mode,
            model=new_model,
            target_printer_ip=target_printer_ip,
            target_printer_serial=target_printer_serial,
            remote_interface_ip=new_remote_iface,
        )
    except ValueError as e:
        logger.warning("Virtual printer configuration validation error: %s", e)
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid virtual printer configuration. Check the provided values."},
        )
    except Exception as e:
        logger.error("Failed to configure virtual printer: %s", e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to configure virtual printer. Check server logs for details."},
        )

    return await get_virtual_printer_settings(db)


# =============================================================================
# MQTT Relay Settings
# =============================================================================


@router.get("/mqtt/status")
async def get_mqtt_status(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get MQTT relay connection status."""
    from backend.app.services.mqtt_relay import mqtt_relay

    return mqtt_relay.get_status()
