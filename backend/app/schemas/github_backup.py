"""Pydantic schemas for GitHub backup configuration."""

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class ScheduleType(str, Enum):
    """Backup schedule types."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


class GitHubBackupConfigCreate(BaseModel):
    """Schema for creating/updating GitHub backup config."""

    repository_url: str = Field(..., min_length=1, max_length=500, description="GitHub repository URL")
    access_token: str = Field(..., min_length=1, description="Personal Access Token")
    branch: str = Field(default="main", max_length=100, description="Branch to push to")

    schedule_enabled: bool = Field(default=False, description="Enable scheduled backups")
    schedule_type: ScheduleType = Field(default=ScheduleType.DAILY, description="Schedule frequency")

    backup_kprofiles: bool = Field(default=True, description="Backup K-profiles")
    backup_cloud_profiles: bool = Field(default=True, description="Backup Bambu Cloud profiles")
    backup_settings: bool = Field(default=False, description="Backup app settings")

    enabled: bool = Field(default=True, description="Enable backup feature")

    @field_validator("repository_url")
    @classmethod
    def validate_repo_url(cls, v: str) -> str:
        """Validate GitHub repository URL format."""
        # Accept various GitHub URL formats
        patterns = [
            r"^https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?$",
            r"^git@github\.com:[\w.-]+/[\w.-]+(?:\.git)?$",
        ]
        v = v.strip().rstrip("/")
        if not any(re.match(p, v) for p in patterns):
            raise ValueError("Invalid GitHub repository URL. Expected format: https://github.com/owner/repo")
        return v


class GitHubBackupConfigUpdate(BaseModel):
    """Schema for updating GitHub backup config (all fields optional)."""

    repository_url: str | None = Field(default=None, max_length=500)
    access_token: str | None = Field(default=None)
    branch: str | None = Field(default=None, max_length=100)

    schedule_enabled: bool | None = None
    schedule_type: ScheduleType | None = None

    backup_kprofiles: bool | None = None
    backup_cloud_profiles: bool | None = None
    backup_settings: bool | None = None

    enabled: bool | None = None

    @field_validator("repository_url")
    @classmethod
    def validate_repo_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        patterns = [
            r"^https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?$",
            r"^git@github\.com:[\w.-]+/[\w.-]+(?:\.git)?$",
        ]
        v = v.strip().rstrip("/")
        if not any(re.match(p, v) for p in patterns):
            raise ValueError("Invalid GitHub repository URL")
        return v


class GitHubBackupConfigResponse(BaseModel):
    """Schema for GitHub backup config API response."""

    id: int
    repository_url: str
    has_token: bool = Field(description="Whether an access token is configured")
    branch: str

    schedule_enabled: bool
    schedule_type: str

    backup_kprofiles: bool
    backup_cloud_profiles: bool
    backup_settings: bool

    enabled: bool
    last_backup_at: datetime | None
    last_backup_status: str | None
    last_backup_message: str | None
    last_backup_commit_sha: str | None
    next_scheduled_run: datetime | None

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GitHubBackupLogResponse(BaseModel):
    """Schema for backup log API response."""

    id: int
    config_id: int
    started_at: datetime
    completed_at: datetime | None
    status: str
    trigger: str
    commit_sha: str | None
    files_changed: int
    error_message: str | None

    class Config:
        from_attributes = True


class GitHubBackupStatus(BaseModel):
    """Schema for current backup status."""

    configured: bool = Field(description="Whether backup is configured")
    enabled: bool = Field(description="Whether backup is enabled")
    is_running: bool = Field(description="Whether a backup is currently running")
    progress: str | None = Field(default=None, description="Current backup progress message")
    last_backup_at: datetime | None
    last_backup_status: str | None
    next_scheduled_run: datetime | None


class GitHubTestConnectionResponse(BaseModel):
    """Schema for test connection response."""

    success: bool
    message: str
    repo_name: str | None = None
    permissions: dict | None = None


class GitHubBackupTriggerResponse(BaseModel):
    """Schema for manual backup trigger response."""

    success: bool
    message: str
    log_id: int | None = None
    commit_sha: str | None = None
    files_changed: int = 0
