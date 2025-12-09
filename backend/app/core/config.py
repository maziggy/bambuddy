from pathlib import Path
from pydantic_settings import BaseSettings
import logging

# Application version - single source of truth
APP_VERSION = "0.1.5b"
GITHUB_REPO = "maziggy/bambuddy"

# Base directory for path calculations
_base_dir = Path(__file__).resolve().parent.parent.parent.parent


def _migrate_database() -> Path:
    """Migrate database from old name to new name if needed."""
    old_db = _base_dir / "bambutrack.db"
    new_db = _base_dir / "bambuddy.db"

    # If old database exists and new one doesn't, rename it
    if old_db.exists() and not new_db.exists():
        try:
            old_db.rename(new_db)
            logging.info(f"Migrated database: {old_db} -> {new_db}")
        except Exception as e:
            logging.warning(f"Could not migrate database: {e}. Using old location.")
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
    base_dir: Path = _base_dir
    archive_dir: Path = base_dir / "archive"
    static_dir: Path = base_dir / "static"
    log_dir: Path = base_dir / "logs"
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
settings.archive_dir.mkdir(exist_ok=True)
settings.static_dir.mkdir(exist_ok=True)
if settings.log_to_file:
    settings.log_dir.mkdir(exist_ok=True)
