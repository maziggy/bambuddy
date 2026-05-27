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
    # #1390: parity with single-archive delete. False (default) soft-deletes
    # — files off disk, archive row hidden, Quick Stats preserved. True
    # also drops PrintLogEntry rows so the contribution leaves /stats.
    purge_stats: bool = False


class ArchivePurgeResponse(BaseModel):
    deleted: int
    purge_stats: bool = False


class ArchivePurgeSettings(BaseModel):
    enabled: bool = False
    days: int = Field(default=365, ge=7, le=3650)
    # #1390: scheduled-purge equivalent of the single-delete checkbox.
    # Default False — preserves Quick Stats; flip to True to also drop
    # the contribution from /stats every time the sweeper runs.
    purge_stats: bool = False
