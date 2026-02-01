"""GitHub backup configuration and log models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class GitHubBackupConfig(Base):
    """Configuration for GitHub profile backup."""

    __tablename__ = "github_backup_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_url: Mapped[str] = mapped_column(String(500))  # Full GitHub URL
    access_token: Mapped[str] = mapped_column(Text)  # Personal Access Token
    branch: Mapped[str] = mapped_column(String(100), default="main")

    # Schedule configuration
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_type: Mapped[str] = mapped_column(String(20), default="daily")  # hourly/daily/weekly
    schedule_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)  # For future cron support

    # What to backup
    backup_kprofiles: Mapped[bool] = mapped_column(Boolean, default=True)
    backup_cloud_profiles: Mapped[bool] = mapped_column(Boolean, default=True)
    backup_settings: Mapped[bool] = mapped_column(Boolean, default=False)

    # Status tracking
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_backup_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # success/failed/skipped
    last_backup_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_backup_commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    next_scheduled_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    logs: Mapped[list["GitHubBackupLog"]] = relationship(back_populates="config", cascade="all, delete-orphan")


class GitHubBackupLog(Base):
    """Log entry for GitHub backup runs."""

    __tablename__ = "github_backup_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("github_backup_config.id", ondelete="CASCADE"))

    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # running/success/failed/skipped
    trigger: Mapped[str] = mapped_column(String(20))  # manual/scheduled

    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    files_changed: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    config: Mapped["GitHubBackupConfig"] = relationship(back_populates="logs")
