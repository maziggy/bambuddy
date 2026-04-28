from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class SpoolBase(BaseModel):
    material: str = Field(..., min_length=1, max_length=50)
    subtype: str | None = None
    color_name: str | None = None
    rgba: str | None = Field(None, pattern=r"^[0-9A-Fa-f]{8}$")
    brand: str | None = None
    label_weight: int = 1000
    core_weight: int = 250
    core_weight_catalog_id: int | None = None
    weight_used: float = 0
    slicer_filament: str | None = None
    slicer_filament_name: str | None = None
    nozzle_temp_min: int | None = None
    nozzle_temp_max: int | None = None
    note: str | None = None
    tag_uid: str | None = Field(default=None, max_length=32)
    tray_uuid: str | None = None
    data_origin: str | None = None
    tag_type: str | None = None
    cost_per_kg: float | None = Field(default=None, ge=0)
    weight_locked: bool = False
    last_scale_weight: int | None = None
    last_weighed_at: datetime | None = None
    # User-defined category + per-spool low-stock threshold override (#729).
    category: str | None = Field(default=None, max_length=50)
    low_stock_threshold_pct: int | None = Field(default=None, ge=1, le=99)
    storage_location: str | None = Field(default=None, max_length=255)


class SpoolCreate(SpoolBase):
    pass


class SpoolBulkCreate(BaseModel):
    spool: SpoolCreate
    quantity: int = Field(default=1, ge=1, le=100)


class SpoolUpdate(BaseModel):
    material: str | None = None
    subtype: str | None = None
    color_name: str | None = None
    rgba: str | None = Field(None, pattern=r"^[0-9A-Fa-f]{8}$")
    brand: str | None = None
    label_weight: int | None = None
    core_weight: int | None = None
    core_weight_catalog_id: int | None = None
    weight_used: float | None = None
    slicer_filament: str | None = None
    slicer_filament_name: str | None = None
    nozzle_temp_min: int | None = None
    nozzle_temp_max: int | None = None
    note: str | None = None
    tag_uid: str | None = Field(default=None, max_length=32)
    tray_uuid: str | None = None
    data_origin: str | None = None
    tag_type: str | None = None
    cost_per_kg: float | None = Field(default=None, ge=0)
    weight_locked: bool | None = None
    # User-defined category + per-spool low-stock threshold override (#729).
    category: str | None = Field(default=None, max_length=50)
    low_stock_threshold_pct: int | None = Field(default=None, ge=1, le=99)
    storage_location: str | None = Field(default=None, max_length=255)


class SpoolKProfileBase(BaseModel):
    printer_id: int
    extruder: int = 0
    nozzle_diameter: str = "0.4"
    nozzle_type: str | None = None
    k_value: float
    name: str | None = None
    cali_idx: int | None = None
    setting_id: str | None = None

    @field_validator("nozzle_diameter")
    @classmethod
    def normalize_nozzle_diameter(cls, v: str) -> str:
        try:
            return str(float(v))
        except (ValueError, TypeError):
            return v


class SpoolKProfileResponse(SpoolKProfileBase):
    id: int
    spool_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class SpoolResponse(SpoolBase):
    id: int
    # rgba is intentionally unconstrained on the response side: the write paths
    # (SpoolCreate, SpoolUpdate) enforce the 8-char hex pattern, but legacy rows
    # or data sourced from AMS firmware / backups may carry malformed values.
    # A single bad row must not 500 the entire inventory list endpoint (#1055).
    rgba: str | None = None
    added_full: bool | None = None
    last_used: datetime | None = None
    encode_time: datetime | None = None
    tag_uid: str | None = Field(default=None, max_length=32)
    tray_uuid: str | None = None
    data_origin: str | None = None
    tag_type: str | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    k_profiles: list[SpoolKProfileResponse] = []

    class Config:
        from_attributes = True


class SpoolAssignmentCreate(BaseModel):
    spool_id: int
    printer_id: int
    ams_id: int
    tray_id: int


class SpoolAssignmentResponse(BaseModel):
    id: int
    spool_id: int
    printer_id: int
    printer_name: str | None = None
    ams_id: int
    tray_id: int
    fingerprint_color: str | None = None
    fingerprint_type: str | None = None
    created_at: datetime
    spool: SpoolResponse | None = None
    configured: bool = False
    ams_label: str | None = None  # User-defined friendly name for the AMS unit

    class Config:
        from_attributes = True
