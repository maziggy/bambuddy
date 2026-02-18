import logging
import os
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings

# Application version - single source of truth
APP_VERSION = "0.2.1b"
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


# Determine database path (only relevant for SQLite)
_db_type_env = os.environ.get("DB_TYPE", "sqlite").lower()
_db_path = _migrate_database() if _db_type_env == "sqlite" else None


class Settings(BaseSettings):
    app_name: str = "Bambuddy"
    debug: bool = False  # Default to production mode

    # Paths
    base_dir: Path = _data_dir  # For backwards compatibility
    archive_dir: Path = _data_dir / "archive"
    plate_calibration_dir: Path = _plate_cal_dir  # Plate detection references
    static_dir: Path = _app_dir / "static"  # Static files are part of app, not data
    log_dir: Path = _log_dir

    # Database configuration
    db_type: Literal["sqlite", "mysql"] = "sqlite"
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "bambuddy"
    db_user: str = ""
    db_password: str = ""
    database_url: str = ""  # Computed from db_type; can be overridden directly

    # Logging
    log_level: str = "INFO"  # Override with LOG_LEVEL env var or DEBUG=true
    log_to_file: bool = True  # Set to false to disable file logging

    # API
    api_prefix: str = "/api/v1"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @model_validator(mode="after")
    def _build_database_url(self) -> "Settings":
        if self.database_url:
            return self
        if self.db_type == "mysql":
            if not self.db_user:
                raise ValueError("DB_USER is required when DB_TYPE=mysql")
            if not self.db_password:
                raise ValueError("DB_PASSWORD is required when DB_TYPE=mysql")
            self.database_url = (
                f"mysql+aiomysql://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
                f"?charset=utf8mb4"
            )
        else:
            self.database_url = f"sqlite+aiosqlite:///{_db_path}"
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.db_type == "sqlite"

    @property
    def is_mysql(self) -> bool:
        return self.db_type == "mysql"


settings = Settings()

# Ensure directories exist
settings.archive_dir.mkdir(parents=True, exist_ok=True)
settings.plate_calibration_dir.mkdir(parents=True, exist_ok=True)
settings.static_dir.mkdir(exist_ok=True)
if settings.log_to_file:
    settings.log_dir.mkdir(exist_ok=True)
