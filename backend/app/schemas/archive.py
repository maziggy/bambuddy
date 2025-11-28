from datetime import datetime
from pydantic import BaseModel


class ArchiveBase(BaseModel):
    print_name: str | None = None
    is_favorite: bool | None = None
    tags: str | None = None
    notes: str | None = None
    cost: float | None = None
    failure_reason: str | None = None


class ArchiveUpdate(ArchiveBase):
    printer_id: int | None = None


class ArchiveResponse(BaseModel):
    id: int
    printer_id: int | None
    filename: str
    file_path: str
    file_size: int
    thumbnail_path: str | None
    timelapse_path: str | None

    print_name: str | None
    print_time_seconds: int | None
    filament_used_grams: float | None
    filament_type: str | None
    filament_color: str | None
    layer_height: float | None
    nozzle_diameter: float | None
    bed_temperature: int | None
    nozzle_temperature: int | None

    status: str
    started_at: datetime | None
    completed_at: datetime | None

    extra_data: dict | None

    makerworld_url: str | None
    designer: str | None

    is_favorite: bool
    tags: str | None
    notes: str | None
    cost: float | None
    photos: list | None
    failure_reason: str | None

    created_at: datetime

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
