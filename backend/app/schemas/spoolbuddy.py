from datetime import datetime

from pydantic import BaseModel, Field

# --- Device schemas ---


class DeviceRegisterRequest(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=50)
    hostname: str = Field(..., min_length=1, max_length=100)
    ip_address: str = Field(..., min_length=1, max_length=45)
    firmware_version: str | None = None
    has_nfc: bool = True
    has_scale: bool = True
    tare_offset: int = 0
    calibration_factor: float = 1.0


class DeviceResponse(BaseModel):
    id: int
    device_id: str
    hostname: str
    ip_address: str
    firmware_version: str | None = None
    has_nfc: bool
    has_scale: bool
    tare_offset: int
    calibration_factor: float
    last_seen: datetime | None = None
    pending_command: str | None = None
    nfc_ok: bool
    scale_ok: bool
    uptime_s: int
    online: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class HeartbeatRequest(BaseModel):
    nfc_ok: bool = False
    scale_ok: bool = False
    uptime_s: int = 0
    firmware_version: str | None = None
    ip_address: str | None = None


class HeartbeatResponse(BaseModel):
    pending_command: str | None = None
    tare_offset: int
    calibration_factor: float


# --- NFC schemas ---


class TagScannedRequest(BaseModel):
    device_id: str
    tag_uid: str
    tray_uuid: str | None = None
    sak: int | None = None
    tag_type: str | None = None
    raw_blocks: dict | None = None


class TagRemovedRequest(BaseModel):
    device_id: str
    tag_uid: str


# --- Scale schemas ---


class ScaleReadingRequest(BaseModel):
    device_id: str
    weight_grams: float
    stable: bool = False
    raw_adc: int | None = None


class UpdateSpoolWeightRequest(BaseModel):
    spool_id: int
    weight_grams: float


# --- Calibration schemas ---


class TareRequest(BaseModel):
    pass


class SetCalibrationFactorRequest(BaseModel):
    known_weight_grams: float = Field(..., gt=0)
    raw_adc: int


class CalibrationResponse(BaseModel):
    tare_offset: int
    calibration_factor: float
