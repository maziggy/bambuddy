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
    nfc_reader_type: str | None = None
    nfc_connection: str | None = None
    backend_url: str | None = None
    has_backlight: bool = False


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
    nfc_reader_type: str | None = None
    nfc_connection: str | None = None
    backend_url: str | None = None
    display_brightness: int = 100
    display_blank_timeout: int = 0
    has_backlight: bool = False
    last_calibrated_at: datetime | None = None
    last_seen: datetime | None = None
    pending_command: str | None = None
    nfc_ok: bool
    scale_ok: bool
    uptime_s: int
    update_status: str | None = None
    update_message: str | None = None
    system_stats: dict | None = None
    online: bool = False
    ssh_public_key: str | None = None
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
    nfc_reader_type: str | None = None
    nfc_connection: str | None = None
    backend_url: str | None = None
    system_stats: dict | None = None


class HeartbeatResponse(BaseModel):
    pending_command: str | None = None
    pending_write_payload: dict | None = None
    pending_system_payload: dict | None = None
    tare_offset: int
    calibration_factor: float
    display_brightness: int = 100
    display_blank_timeout: int = 0


# --- NFC schemas ---


class TagScannedRequest(BaseModel):
    device_id: str
    tag_uid: str = Field(..., max_length=32)
    tray_uuid: str | None = None
    sak: int | None = None
    tag_type: str | None = None
    raw_blocks: dict | None = None


class TagRemovedRequest(BaseModel):
    device_id: str
    tag_uid: str = Field(..., max_length=32)


# --- Scale schemas ---


class ScaleReadingRequest(BaseModel):
    device_id: str
    weight_grams: float
    stable: bool = False
    raw_adc: int | None = None


class UpdateSpoolWeightRequest(BaseModel):
    spool_id: int = Field(..., gt=0)
    weight_grams: float


# --- Calibration schemas ---


class SetTareRequest(BaseModel):
    tare_offset: int


class SetCalibrationFactorRequest(BaseModel):
    known_weight_grams: float = Field(..., gt=0)
    raw_adc: int
    tare_raw_adc: int | None = None


class CalibrationResponse(BaseModel):
    tare_offset: int
    calibration_factor: float


# --- Display schemas ---


class WriteTagRequest(BaseModel):
    device_id: str
    spool_id: int = Field(..., gt=0)


class WriteTagResultRequest(BaseModel):
    device_id: str
    spool_id: int = Field(..., gt=0)
    tag_uid: str = Field(..., max_length=32)
    success: bool
    message: str | None = None


class DisplaySettingsRequest(BaseModel):
    brightness: int = Field(ge=0, le=100)
    blank_timeout: int = Field(ge=0)


class SystemConfigRequest(BaseModel):
    backend_url: str = Field(..., min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=255)


class SystemCommandRequest(BaseModel):
    command: str = Field(..., description="System command: reboot, shutdown, restart_daemon, restart_browser")


class SystemCommandResultRequest(BaseModel):
    command: str
    success: bool
    message: str | None = None


# --- Diagnostics schemas ---


class DiagnosticResultRequest(BaseModel):
    diagnostic: str  # 'nfc', 'scale', or 'read_tag'
    success: bool
    output: str
    exit_code: int
