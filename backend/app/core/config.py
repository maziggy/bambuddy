from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "BambuTrack"
    debug: bool = True

    # Paths
    base_dir: Path = Path(__file__).resolve().parent.parent.parent.parent
    archive_dir: Path = base_dir / "archive"
    static_dir: Path = base_dir / "static"
    database_url: str = f"sqlite+aiosqlite:///{base_dir}/bambutrack.db"

    # API
    api_prefix: str = "/api/v1"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Ensure directories exist
settings.archive_dir.mkdir(exist_ok=True)
settings.static_dir.mkdir(exist_ok=True)
