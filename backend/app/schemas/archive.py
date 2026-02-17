from datetime import datetime

from pydantic import BaseModel, model_validator


class ArchiveBase(BaseModel):
    print_name: str | None = None
    is_favorite: bool | None = None
    tags: str | None = None
    notes: str | None = None
    cost: float | None = None
    failure_reason: str | None = None
    quantity: int | None = None  # Number of items printed
    # User-defined link (Printables, Thingiverse, etc.)
    external_url: str | None = None


class ArchiveUpdate(ArchiveBase):
    printer_id: int | None = None
    project_id: int | None = None
    # Allow changing status (e.g., clearing failed flag)
    status: str | None = None


class ArchiveDuplicate(BaseModel):
    """Reference to a duplicate archive."""

    id: int
    print_name: str | None
    created_at: datetime
    match_type: str  # "exact" (hash match) or "similar" (name match)


class ArchiveResponse(BaseModel):
    id: int
    printer_id: int | None
    project_id: int | None = None
    project_name: str | None = None  # Included for convenience
    filename: str
    file_path: str
    file_size: int
    content_hash: str | None
    thumbnail_path: str | None
    timelapse_path: str | None
    source_3mf_path: str | None = None  # Original project 3MF from slicer
    f3d_path: str | None = None  # Fusion 360 design file

    # Duplicate detection
    duplicates: list[ArchiveDuplicate] | None = None
    duplicate_count: int = 0  # Quick count for list views

    # Object count (computed from extra_data.printable_objects)
    object_count: int | None = None

    print_name: str | None
    print_time_seconds: int | None  # Estimated time from slicer
    actual_time_seconds: int | None = None  # Computed from started_at/completed_at
    # Percentage: 100 = perfect, >100 = faster than estimated
    time_accuracy: float | None = None
    filament_used_grams: float | None
    filament_type: str | None
    filament_color: str | None
    layer_height: float | None
    total_layers: int | None = None
    nozzle_diameter: float | None
    bed_temperature: int | None
    nozzle_temperature: int | None

    sliced_for_model: str | None = None  # Printer model this file was sliced for

    status: str
    started_at: datetime | None
    completed_at: datetime | None

    extra_data: dict | None

    makerworld_url: str | None
    designer: str | None
    # User-defined link (Printables, Thingiverse, etc.)
    external_url: str | None = None

    is_favorite: bool
    tags: str | None
    notes: str | None
    cost: float | None
    photos: list | None
    failure_reason: str | None
    quantity: int = 1  # Number of items printed

    # Energy tracking
    energy_kwh: float | None = None
    energy_cost: float | None = None

    created_at: datetime

    # User tracking (Issue #206)
    created_by_id: int | None = None
    created_by_username: str | None = None

    @model_validator(mode="after")
    def compute_object_count(self) -> "ArchiveResponse":
        """Compute object_count from extra_data.printable_objects if not set."""
        if self.object_count is None and self.extra_data:
            printable_objects = self.extra_data.get("printable_objects")
            if printable_objects and isinstance(printable_objects, dict):
                self.object_count = len(printable_objects)
        return self

    class Config:
        from_attributes = True


class ArchiveStats(BaseModel):
    total_prints: int
    successful_prints: int
    failed_prints: int
    total_print_time_hours: float
    total_filament_grams: float
    total_cost: float
    prints_by_filament_type: dict
    prints_by_printer: dict
    # Time accuracy stats
    # Average across all prints with data
    average_time_accuracy: float | None = None
    time_accuracy_by_printer: dict | None = None  # Per-printer accuracy
    # Energy stats
    total_energy_kwh: float = 0.0
    total_energy_cost: float = 0.0


class ProjectPageImage(BaseModel):
    """Image embedded in 3MF project page."""

    name: str
    path: str  # Path within 3MF
    url: str  # API URL to fetch image


class ProjectPageResponse(BaseModel):
    """Project page data extracted from 3MF file."""

    # Model info
    title: str | None = None
    description: str | None = None  # HTML content
    designer: str | None = None
    designer_user_id: str | None = None
    license: str | None = None
    copyright: str | None = None
    creation_date: str | None = None
    modification_date: str | None = None
    origin: str | None = None  # "original" or "remix"

    # Profile info
    profile_title: str | None = None
    profile_description: str | None = None
    profile_cover: str | None = None
    profile_user_id: str | None = None
    profile_user_name: str | None = None

    # MakerWorld info
    design_model_id: str | None = None
    design_profile_id: str | None = None
    design_region: str | None = None

    # Images
    model_pictures: list[ProjectPageImage] = []
    profile_pictures: list[ProjectPageImage] = []
    thumbnails: list[ProjectPageImage] = []


class ProjectPageUpdate(BaseModel):
    """Update project page data in 3MF file."""

    title: str | None = None
    description: str | None = None
    designer: str | None = None
    license: str | None = None
    copyright: str | None = None
    profile_title: str | None = None
    profile_description: str | None = None


class ReprintRequest(BaseModel):
    """Request body for reprinting an archive."""

    # Plate selection for multi-plate 3MF files
    # If not specified, auto-detects from file (legacy behavior for single-plate files)
    plate_id: int | None = None
    plate_name: str | None = None

    # AMS slot mapping: list of tray IDs for each filament slot in the 3MF
    # Global tray ID = (ams_id * 4) + slot_id, external = 254
    ams_mapping: list[int] | None = None

    # Print options
    bed_levelling: bool = True
    flow_cali: bool = False
    vibration_cali: bool = True
    layer_inspect: bool = False
    timelapse: bool = False
    use_ams: bool = True  # Not exposed in UI, but needed for API
