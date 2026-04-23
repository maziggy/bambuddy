"""Schemas for the library trash bin + bulk purge (#1008)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PurgePreviewRequest(BaseModel):
    older_than_days: int = Field(ge=1, le=3650, description="Age threshold in days.")
    include_never_printed: bool = True


class PurgePreviewResponse(BaseModel):
    count: int
    total_bytes: int
    sample_filenames: list[str]
    older_than_days: int
    include_never_printed: bool


class PurgeRequest(BaseModel):
    older_than_days: int = Field(ge=1, le=3650)
    include_never_printed: bool = True


class PurgeResponse(BaseModel):
    moved_to_trash: int


class TrashFile(BaseModel):
    id: int
    filename: str
    file_size: int
    thumbnail_path: str | None = None
    folder_id: int | None = None
    folder_name: str | None = None
    created_by_id: int | None = None
    created_by_username: str | None = None
    deleted_at: datetime
    auto_purge_at: datetime


class TrashListResponse(BaseModel):
    items: list[TrashFile]
    total: int
    retention_days: int


class TrashSettings(BaseModel):
    retention_days: int = Field(ge=1, le=365)
    auto_purge_enabled: bool = False
    auto_purge_days: int = Field(default=90, ge=7, le=3650)
    auto_purge_include_never_printed: bool = True


class EmptyTrashResponse(BaseModel):
    deleted: int
