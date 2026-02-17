import logging
import os
from pathlib import Path

from pydantic_settings import BaseSettings

# Application version - single source of truth
APP_VERSION = "0.2.0"
GITHUB_REPO = "maziggy/bambuddy"

# App directory - where the application is installed (for static files)
_app_dir = Path(__file__).resolve().parent.parent.parent.parent

# Data directory - for persistent data (database, archives)
# Use DATA_DIR env var if set (Docker), otherwise use project root (local dev)
_data_dir_env = os.environ.get("DATA_DIR")
_data_dir = Path(_data_dir_env) if _data_dir_env else _app_dir

# Plate calibration directory - special handling to maintain backwards compatibility
# Docker: DATA_DIR/plate_calibration (e.g., /data/plate_calibration)
# Local dev: project_root/data/plate_calibration (original location)
_plate_cal_dir = Path(_data_dir_env) / "plate_calibration" if _data_dir_env else _app_dir / "data" / "plate_calibration"

# Log directory - use LOG_DIR env var if set, otherwise use app_dir/logs
_log_dir_env = os.environ.get("LOG_DIR")
_log_dir = Path(_log_dir_env) if _log_dir_env else _app_dir / "logs"


def _migrate_database() -> Path:
    """Migrate database from old name to new name if needed."""
    old_db = _data_dir / "bambutrack.db"
    new_db = _data_dir / "bambuddy.db"

    # If old database exists and new one doesn't, rename it
    if old_db.exists() and not new_db.exists():
        try:
            old_db.rename(new_db)
            logging.info("Migrated database: %s -> %s", old_db, new_db)
        except Exception as e:
            logging.warning("Could not migrate database: %s. Using old location.", e)
            return old_db

    # If old database exists (and new one now exists too), it was migrated
    # If only new exists, use new
    # If neither exists, use new (will be created)
    return new_db if new_db.exists() or not old_db.exists() else old_db


# Determine database path (handles migration)
_db_path = _migrate_database()


class Settings(BaseSettings):
    app_name: str = "Bambuddy"
    debug: bool = False  # Default to production mode

    # Paths
    base_dir: Path = _data_dir  # For backwards compatibility
    archive_dir: Path = _data_dir / "archive"
    plate_calibration_dir: Path = _plate_cal_dir  # Plate detection references
    static_dir: Path = _app_dir / "static"  # Static files are part of app, not data
    log_dir: Path = _log_dir
    database_url: str = f"sqlite+aiosqlite:///{_db_path}"

    # Logging
    log_level: str = "INFO"  # Override with LOG_LEVEL env var or DEBUG=true
    log_to_file: bool = True  # Set to false to disable file logging

    # API
    api_prefix: str = "/api/v1"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Ensure directories exist
settings.archive_dir.mkdir(parents=True, exist_ok=True)
settings.plate_calibration_dir.mkdir(parents=True, exist_ok=True)
settings.static_dir.mkdir(exist_ok=True)
if settings.log_to_file:
    settings.log_dir.mkdir(exist_ok=True)
