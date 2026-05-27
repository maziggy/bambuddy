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

    model_id: int = Field(
        ...,
        description="The MakerWorld design ID (the number in /models/{id}).",
    )
    profile_id: int | None = Field(
        default=None,
        description=(
            "The profileId of the selected instance (plate configuration). Each "
            "instance in `/design/{id}/instances` carries a `profileId` field — "
            "the frontend forwards the picked one here. If omitted, the backend "
            "falls back to the first available instance of the model."
        ),
    )
    instance_id: int | None = Field(
        default=None,
        description="Retained for backwards compatibility; no longer used by the download flow.",
    )
    folder_id: int | None = Field(default=None, description="Target library folder; null = root")


class MakerWorldRecentImport(BaseModel):
    """One row in the 'recent MakerWorld imports' list."""

    library_file_id: int
    filename: str
    folder_id: int | None
    thumbnail_path: str | None = Field(
        default=None,
        description="Relative path under /api/v1/library/files/{id}/thumbnail — "
        "the frontend wraps it with a stream token to render.",
    )
    source_url: str | None = Field(
        default=None,
        description="Canonical MakerWorld URL (``https://makerworld.com/models/{id}"
        "#profileId-{pid}``). The frontend uses it to build an 'Open on MakerWorld' "
        "link and to extract model/profile ids without a second API round-trip.",
    )
    created_at: str


class MakerWorldImportResponse(BaseModel):
    """Result of a MakerWorld import."""

    library_file_id: int
    filename: str
    folder_id: int | None = Field(
        default=None,
        description=(
            "Folder the file was saved to — the auto-created 'MakerWorld' folder "
            "by default, or whichever folder the caller specified. Surfaced so the "
            "frontend can deep-link to File Manager → that folder after import."
        ),
    )
    profile_id: int | None = Field(
        default=None,
        description=(
            "The MakerWorld profile (plate) id that was imported. Surfaced so the "
            "frontend can match the response back to the plate row in the UI and "
            "render inline 'view in library' / 'open in slicer' controls there."
        ),
    )
    was_existing: bool = Field(
        description="True if a prior import from the same source URL was reused (no re-download)"
    )


class MakerWorldStatus(BaseModel):
    """Integration health + auth status surfaced to the frontend."""

    has_cloud_token: bool = Field(description="Whether the caller's account has a stored Bambu Cloud token")
    can_download: bool = Field(description="Shortcut: has_cloud_token AND it looks valid. Downloads require it.")
