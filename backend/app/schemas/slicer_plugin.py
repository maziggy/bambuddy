"""Schemas for the slicer plugin's upload API."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SlicerPluginPresets(BaseModel):
    """The preset names the slicer had selected when it sliced."""

    printer: str | None = Field(default=None, max_length=255)
    process: str | None = Field(default=None, max_length=255)
    filaments: list[str] = Field(default_factory=list, max_length=64)


class SlicerPluginSource(BaseModel):
    """Where the file came from -- what the file itself cannot tell us."""

    slicer: str | None = Field(default=None, max_length=50)
    slicer_version: str | None = Field(default=None, max_length=50)
    plugin_version: str | None = Field(default=None, max_length=50)
    project_name: str | None = Field(default=None, max_length=255)
    presets: SlicerPluginPresets | None = None
    # A hash of the model's geometry, so a later slice of the same model can
    # find this one however its file is named.
    model_fingerprint: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")


class SlicerPluginIntent(BaseModel):
    """What the user asked for when they sent the file."""

    action: Literal["library", "queue"] = "library"
    folder_id: int | None = None
    # Queue only. 1-based, as in the 3MF (Metadata/plate_<n>.gcode).
    plate_id: int | None = Field(default=None, ge=1)
    copies: int = Field(default=1, ge=1, le=100)
    printer_id: int | None = None
    target_model: str | None = Field(default=None, max_length=50)
    manual_start: bool = False
    # Required by POST /queue/ when billing is enabled; ignored otherwise.
    cost_center_id: int | None = None

    @model_validator(mode="after")
    def _one_destination(self) -> "SlicerPluginIntent":
        if self.printer_id is not None and self.target_model:
            raise ValueError("Cannot specify both printer_id and target_model")
        return self


class SlicerPluginManifest(BaseModel):
    """The JSON part sent alongside the file.

    Unknown keys are ignored so an older Bambuddy accepts a newer plugin's
    additions; a change it must understand bumps ``schema_version`` instead.
    """

    schema_version: int
    # Chosen by the plugin, one per send; a retry reuses it.
    upload_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    source: SlicerPluginSource = Field(default_factory=SlicerPluginSource)
    intent: SlicerPluginIntent = Field(default_factory=SlicerPluginIntent)


class SlicerPluginLibraryFile(BaseModel):
    id: int
    filename: str
    file_type: str
    file_size: int
    thumbnail_path: str | None
    duplicate_of: int | None = None  # an active library file with the same bytes


class SlicerPluginQueueResult(BaseModel):
    queue_item_ids: list[int]
    batch_id: int | None = None  # set when copies > 1


class SlicerPluginUploadResponse(BaseModel):
    upload_id: str
    library_file: SlicerPluginLibraryFile
    # With action "queue": what was queued, or why nothing was. The file is in
    # the library either way, so the user can still queue it from there.
    queue: SlicerPluginQueueResult | None = None
    queue_error: str | None = None
    warnings: list[str] = []
    # True when this is the stored answer to an earlier request with the same
    # upload_id, not a new upload.
    replayed: bool = False


class SlicerPluginInfoResponse(BaseModel):
    bambuddy_version: str
    manifest_versions: list[int]
    can_upload: bool
    can_queue: bool
