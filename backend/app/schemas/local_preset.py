"""Pydantic schemas for local preset API."""

from datetime import datetime

from pydantic import BaseModel

from backend.app.schemas.not_null import IgnoresNullForRequiredColumns


class LocalPresetResponse(BaseModel):
    """Local preset summary (without full setting blob)."""

    id: int
    name: str
    preset_type: str
    source: str
    filament_type: str | None = None
    filament_vendor: str | None = None
    nozzle_temp_min: int | None = None
    nozzle_temp_max: int | None = None
    pressure_advance: str | None = None
    default_filament_colour: str | None = None
    filament_cost: str | None = None
    filament_density: str | None = None
    compatible_printers: str | None = None
    inherits: str | None = None
    version: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LocalPresetDetail(LocalPresetResponse):
    """Full preset detail including the resolved setting JSON."""

    setting: dict


class LocalPresetCreate(BaseModel):
    """Schema for manually creating a local preset."""

    name: str
    preset_type: str  # filament, printer, process
    setting: dict


class LocalPresetUpdate(IgnoresNullForRequiredColumns):
    """Schema for updating a local preset."""

    __orm_table__ = "local_presets"

    name: str | None = None
    setting: dict | None = None


class LocalPresetsResponse(BaseModel):
    """Grouped local presets by type."""

    filament: list[LocalPresetResponse] = []
    printer: list[LocalPresetResponse] = []
    process: list[LocalPresetResponse] = []


class ImportResponse(BaseModel):
    """Result of an import operation."""

    success: bool
    imported: int
    skipped: int
    errors: list[str] = []
