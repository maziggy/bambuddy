import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Ensure project root is on sys.path so 'backend' package is importable
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from backend.app.core.config import settings  # noqa: E402
from backend.app.core.database import Base  # noqa: E402

# Import all models to register them with Base.metadata
from backend.app.models import (  # noqa: E402, F401
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
    settings as settings_model,
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

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url with the app's database URL
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
