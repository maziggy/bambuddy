"""Pydantic schemas for the MakerWorld integration routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MakerWorldResolveRequest(BaseModel):
    """Body for POST /makerworld/resolve."""

    url: str = Field(..., description="Any MakerWorld model URL (scheme optional)")


class MakerWorldResolvedModel(BaseModel):
    """Structured result of URL resolution.

    ``design`` and ``instances`` are passed through verbatim from MakerWorld's
    API — we don't re-shape them because the frontend needs access to fields
    MakerWorld may add over time (badges, license variants, etc.). Keeping
    them as opaque dicts avoids brittle coupling.
    """

    model_id: int
    profile_id: int | None = Field(
        default=None,
        description="Specific profile from the URL's #profileId- fragment, if any",
    )
    design: dict[str, Any]
    instances: list[dict[str, Any]]
    already_imported_library_ids: list[int] = Field(
        default_factory=list,
        description="LibraryFile IDs that were previously imported from this model URL",
    )


class MakerWorldImportRequest(BaseModel):
    """Body for POST /makerworld/import."""

    instance_id: int = Field(
        ..., description="The profile instance ID to download (MakerWorld's 'id' field on an instance)"
    )
    folder_id: int | None = Field(default=None, description="Target library folder; null = root")


class MakerWorldImportResponse(BaseModel):
    """Result of a MakerWorld import."""

    library_file_id: int
    filename: str
    was_existing: bool = Field(
        description="True if a prior import from the same source URL was reused (no re-download)"
    )


class MakerWorldStatus(BaseModel):
    """Integration health + auth status surfaced to the frontend."""

    has_cloud_token: bool = Field(description="Whether the caller's account has a stored Bambu Cloud token")
    can_download: bool = Field(description="Shortcut: has_cloud_token AND it looks valid. Downloads require it.")
