"""Request and response shapes for the Manyfold routes (#1471)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ManyfoldConfigResponse(BaseModel):
    url: str
    client_id: str
    # The secret itself is never sent back, only whether one is stored.
    has_client_secret: bool
    configured: bool


class ManyfoldConfigUpdate(BaseModel):
    url: str = Field(..., max_length=500)
    client_id: str = Field(..., min_length=1, max_length=200)
    # Left empty, the stored secret is kept.
    client_secret: str | None = Field(default=None, max_length=500)


class ManyfoldTestRequest(BaseModel):
    url: str = Field(..., max_length=500)
    client_id: str = Field(..., min_length=1, max_length=200)
    # Left empty, the stored secret is tested.
    client_secret: str | None = Field(default=None, max_length=500)


class ManyfoldTestResponse(BaseModel):
    model_count: int


class ManyfoldStatus(BaseModel):
    configured: bool
    # For "Open in Manyfold" links; empty until configured.
    url: str


class ManyfoldModelSummary(BaseModel):
    id: str
    name: str


class ManyfoldModelList(BaseModel):
    total: int
    page: int
    has_next: bool
    has_previous: bool
    models: list[ManyfoldModelSummary]


class ManyfoldLibraryRef(BaseModel):
    """The library file an earlier import of a Manyfold file created."""

    id: int
    filename: str
    folder_id: int | None


class ManyfoldFile(BaseModel):
    id: str
    name: str
    mime: str
    importable: bool
    # Set while the file imported earlier is still in the library.
    library_file: ManyfoldLibraryRef | None = None


class ManyfoldModel(BaseModel):
    id: str
    name: str
    caption: str | None = None
    description: str | None = None
    license: str | None = None
    tags: list[str]
    url: str
    has_preview: bool
    files: list[ManyfoldFile]


class ManyfoldImportRequest(BaseModel):
    model_id: str = Field(..., min_length=1, max_length=64)
    file_id: str = Field(..., min_length=1, max_length=64)
    # None imports into the "Manyfold" folder, created on first use.
    folder_id: int | None = None


class ManyfoldImportResponse(BaseModel):
    library_file_id: int
    filename: str
    folder_id: int | None
    was_existing: bool
