from datetime import datetime

from pydantic import BaseModel, Field


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
    tag_uid: str | None = None
    tray_uuid: str | None = None
    data_origin: str | None = None
    tag_type: str | None = None
    cost_per_kg: float | None = Field(default=None, ge=0)


class SpoolCreate(SpoolBase):
    pass


class SpoolUpdate(BaseModel):
    material: str | None = None
    subtype: str | None = None
    color_name: str | None = None
    rgba: str | None = None
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
    tag_uid: str | None = None
    tray_uuid: str | None = None
    data_origin: str | None = None
    tag_type: str | None = None
    cost_per_kg: float | None = Field(default=None, ge=0)


class SpoolKProfileBase(BaseModel):
    printer_id: int
    extruder: int = 0
    nozzle_diameter: str = "0.4"
    nozzle_type: str | None = None
    k_value: float
    name: str | None = None
    cali_idx: int | None = None
    setting_id: str | None = None


class SpoolKProfileResponse(SpoolKProfileBase):
    id: int
    spool_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class SpoolResponse(SpoolBase):
    id: int
    added_full: bool | None = None
    last_used: datetime | None = None
    encode_time: datetime | None = None
    tag_uid: str | None = None
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

    class Config:
        from_attributes = True
