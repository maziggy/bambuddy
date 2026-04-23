"""Schemas for archive auto-purge (#1008 follow-up)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArchivePurgePreviewResponse(BaseModel):
    count: int
    total_bytes: int
    sample_filenames: list[str]
    older_than_days: int


class ArchivePurgeRequest(BaseModel):
    older_than_days: int = Field(ge=1, le=3650)


class ArchivePurgeResponse(BaseModel):
    deleted: int


class ArchivePurgeSettings(BaseModel):
    enabled: bool = False
    days: int = Field(default=365, ge=7, le=3650)
