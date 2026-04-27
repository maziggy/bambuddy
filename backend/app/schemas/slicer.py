"""Pydantic schemas for slice requests."""

from pydantic import BaseModel, Field


class SliceRequest(BaseModel):
    """Body for `POST /library/files/{file_id}/slice`."""

    printer_preset_id: int = Field(..., description="LocalPreset id with preset_type='printer'")
    process_preset_id: int = Field(..., description="LocalPreset id with preset_type='process'")
    filament_preset_id: int = Field(..., description="LocalPreset id with preset_type='filament'")
    plate: int | None = Field(
        default=None,
        ge=1,
        description="Plate number to slice (1-indexed). Defaults to plate 1 on the sidecar.",
    )
    export_3mf: bool = Field(
        default=False,
        description="If true, request a 3MF response with embedded G-code instead of raw G-code.",
    )


class SliceResponse(BaseModel):
    """Response from `POST /library/files/{file_id}/slice`. The result lands
    in the user's library as a new ``LibraryFile`` (in the same folder as
    the source)."""

    library_file_id: int
    name: str
    print_time_seconds: int
    filament_used_g: float
    filament_used_mm: float
    used_embedded_settings: bool = False


class SliceArchiveResponse(BaseModel):
    """Response from `POST /archives/{archive_id}/slice`. The result lands
    in the user's archives as a new ``PrintArchive`` row, inheriting
    printer / project metadata from the source archive."""

    archive_id: int
    name: str
    print_time_seconds: int
    filament_used_g: float
    filament_used_mm: float
    used_embedded_settings: bool = False
