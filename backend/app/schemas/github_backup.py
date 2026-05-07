"""Pydantic schemas for GitHub backup configuration."""

import re
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from backend.app.core.compat import StrEnum


class ScheduleType(StrEnum):
    """Backup schedule types."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


class ProviderType(StrEnum):
    """Git hosting provider types."""

    GITHUB = "github"
    GITLAB = "gitlab"
    GITEA = "gitea"
    FORGEJO = "forgejo"


class GitHubBackupConfigCreate(BaseModel):
    """Schema for creating/updating GitHub backup config."""

    repository_url: str = Field(..., min_length=1, max_length=500, description="Git repository URL")
    access_token: str = Field(..., min_length=1, description="Personal Access Token")
    branch: str = Field(default="main", max_length=100, description="Branch to push to")
    provider: ProviderType = Field(default=ProviderType.GITHUB, description="Git hosting provider")

    schedule_enabled: bool = Field(default=False, description="Enable scheduled backups")
    schedule_type: ScheduleType = Field(default=ScheduleType.DAILY, description="Schedule frequency")

    backup_kprofiles: bool = Field(default=True, description="Backup K-profiles")
    backup_cloud_profiles: bool = Field(default=True, description="Backup Bambu Cloud profiles")
    backup_settings: bool = Field(default=False, description="Backup app settings")
    backup_spools: bool = Field(default=False, description="Backup spool inventory")
    backup_archives: bool = Field(default=False, description="Backup print archive history")

    allow_insecure_http: bool = Field(default=False, description="Allow HTTP (non-TLS) repository URLs")
    enabled: bool = Field(default=True, description="Enable backup feature")

    @model_validator(mode="after")
    def validate_repo_url(self) -> "GitHubBackupConfigCreate":
        url = self.repository_url.strip().rstrip("/")
        self.repository_url = url
        https_or_ssh = [
            r"^https://[\w.-]+(:\d+)?/[\w.-]+(\/[\w.-]+)+(?:\.git)?/?$",
            r"^git@[\w.-]+:[\w.-]+(\/[\w.-]+)+(?:\.git)?$",
        ]
        http_pattern = r"^http://[\w.-]+(:\d+)?/[\w.-]+(\/[\w.-]+)+(?:\.git)?/?$"
        if any(re.match(p, url) for p in https_or_ssh):
            return self
        if re.match(http_pattern, url):
            if not self.allow_insecure_http:
                raise ValueError(
                    "This URL uses HTTP instead of HTTPS. "
                    "Enable 'Allow insecure HTTP' if your instance does not use TLS."
                )
            return self
        raise ValueError(
            "Invalid Git repository URL. Expected: https://host/owner/repo, "
            "http://host/owner/repo (with 'Allow insecure HTTP' enabled), or git@host:owner/repo"
        )


class GitHubBackupConfigUpdate(BaseModel):
    """Schema for updating GitHub backup config (all fields optional)."""

    repository_url: str | None = Field(default=None, max_length=500)
    access_token: str | None = Field(default=None)
    branch: str | None = Field(default=None, max_length=100)
    provider: ProviderType | None = None

    schedule_enabled: bool | None = None
    schedule_type: ScheduleType | None = None

    backup_kprofiles: bool | None = None
    backup_cloud_profiles: bool | None = None
    backup_settings: bool | None = None
    backup_spools: bool | None = None
    backup_archives: bool | None = None

    allow_insecure_http: bool | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_repo_url(self) -> "GitHubBackupConfigUpdate":
        if self.repository_url is None:
            return self
        url = self.repository_url.strip().rstrip("/")
        self.repository_url = url
        valid_patterns = [
            r"^https?://[\w.-]+(:\d+)?/[\w.-]+(\/[\w.-]+)+(?:\.git)?/?$",
            r"^git@[\w.-]+:[\w.-]+(\/[\w.-]+)+(?:\.git)?$",
        ]
        if not any(re.match(p, url) for p in valid_patterns):
            raise ValueError(
                "Invalid repository URL. Expected: https://host/owner/repo, "
                "http://host/owner/repo, or git@host:owner/repo"
            )
        return self


class GitHubBackupConfigResponse(BaseModel):
    """Schema for GitHub backup config API response."""

    id: int
    repository_url: str
    has_token: bool = Field(description="Whether an access token is configured")
    branch: str
    provider: str
    allow_insecure_http: bool

    schedule_enabled: bool
    schedule_type: str

    backup_kprofiles: bool
    backup_cloud_profiles: bool
    backup_settings: bool
    backup_spools: bool
    backup_archives: bool

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
