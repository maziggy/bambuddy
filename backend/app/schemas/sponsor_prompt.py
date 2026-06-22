"""Pydantic schemas for the sponsor-prompt API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SponsorPromptCheckResponse(BaseModel):
    """Result of GET /sponsor-prompt/check."""

    show: bool
    milestone: str | None = None
    family: str | None = None
    threshold: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SponsorPromptDismissRequest(BaseModel):
    """Body of POST /sponsor-prompt/dismiss."""

    milestone: str
