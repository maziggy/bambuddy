from pydantic import BaseModel, Field


class CloudLoginRequest(BaseModel):
    """Request to initiate cloud login."""

    email: str = Field(..., description="Bambu Lab account email")
    password: str = Field(..., description="Account password")
    region: str = Field(default="global", description="Region: 'global' or 'china'")


class CloudVerifyRequest(BaseModel):
    """Request to verify login with 2FA code."""

    email: str = Field(..., description="Bambu Lab account email")
    code: str = Field(..., description="6-digit verification code from email")


class CloudLoginResponse(BaseModel):
    """Response from login attempt."""

    success: bool
    needs_verification: bool = False
    message: str


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
