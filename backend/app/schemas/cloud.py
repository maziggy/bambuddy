from pydantic import BaseModel, Field


class CloudLoginRequest(BaseModel):
    """Request to initiate cloud login."""

    email: str = Field(..., description="Bambu Lab account email")
    password: str = Field(..., description="Account password")
    region: str = Field(default="global", description="Region: 'global' or 'china'")


class CloudVerifyRequest(BaseModel):
    """Request to verify login with 2FA code (email or TOTP)."""

    email: str = Field(..., description="Bambu Lab account email")
    code: str = Field(..., description="6-digit verification code")
    tfa_key: str | None = Field(None, description="TFA key for TOTP verification (from login response)")


class CloudLoginResponse(BaseModel):
    """Response from login attempt."""

    success: bool
    needs_verification: bool = False
    message: str
    verification_type: str | None = None  # "email" or "totp"
    tfa_key: str | None = None  # Key needed for TOTP verification


class CloudAuthStatus(BaseModel):
    """Current authentication status."""

    is_authenticated: bool
    email: str | None = None


class CloudTokenRequest(BaseModel):
    """Request to set access token directly."""

    access_token: str = Field(..., description="Bambu Lab access token")


class SlicerSetting(BaseModel):
    """A slicer setting/preset."""

    setting_id: str
    name: str
    type: str  # filament, printer, process
    version: str | None = None
    user_id: str | None = None
    updated_time: str | None = None
    is_custom: bool = False


class SlicerSettingsResponse(BaseModel):
    """Response containing slicer settings."""

    filament: list[SlicerSetting] = []
    printer: list[SlicerSetting] = []
    process: list[SlicerSetting] = []


class CloudDevice(BaseModel):
    """A bound printer device."""

    dev_id: str
    name: str
    dev_model_name: str | None = None
    dev_product_name: str | None = None
    online: bool = False


class SlicerSettingCreate(BaseModel):
    """Request to create a new slicer preset."""

    type: str = Field(..., description="Preset type: 'filament', 'print', or 'printer'")
    name: str = Field(..., description="Display name for the preset")
    base_id: str = Field(..., description="Base preset ID to inherit from")
    version: str = Field(default="2.0.0.0", description="Version string for the preset")
    setting: dict = Field(default_factory=dict, description="Setting key-value pairs (delta from base)")


class SlicerSettingUpdate(BaseModel):
    """Request to update an existing slicer preset."""

    name: str | None = Field(None, description="New display name")
    setting: dict | None = Field(None, description="Setting key-value pairs to update")


class SlicerSettingDetail(BaseModel):
    """Detailed slicer setting/preset response."""

    message: str | None = None
    code: str | None = None
    error: str | None = None
    public: bool = False
    version: str | None = None
    type: str
    name: str
    update_time: str | None = None
    nickname: str | None = None
    base_id: str | None = None
    setting: dict = Field(default_factory=dict)
    filament_id: str | None = None
    setting_id: str | None = None  # For response after create


class SlicerSettingDeleteResponse(BaseModel):
    """Response from deleting a preset."""

    success: bool
    message: str


class FirmwareUpdateInfo(BaseModel):
    """Firmware update information for a device."""

    device_id: str = Field(..., description="Device ID")
    device_name: str = Field(..., description="Device name")
    current_version: str | None = Field(None, description="Currently installed firmware version")
    latest_version: str | None = Field(None, description="Latest available firmware version")
    update_available: bool = Field(False, description="Whether an update is available")
    release_notes: str | None = Field(None, description="Release notes for the latest version")


class FirmwareUpdatesResponse(BaseModel):
    """Response containing firmware updates for all devices."""

    updates: list[FirmwareUpdateInfo] = Field(default_factory=list)
    updates_available: int = Field(0, description="Total number of devices with updates available")
