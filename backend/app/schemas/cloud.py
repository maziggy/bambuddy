from pydantic import BaseModel, Field
from typing import Optional


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
    email: Optional[str] = None


class CloudTokenRequest(BaseModel):
    """Request to set access token directly."""
    access_token: str = Field(..., description="Bambu Lab access token")


class SlicerSetting(BaseModel):
    """A slicer setting/preset."""
    setting_id: str
    name: str
    type: str  # filament, printer, process
    version: Optional[str] = None
    user_id: Optional[str] = None
    updated_time: Optional[str] = None


class SlicerSettingsResponse(BaseModel):
    """Response containing slicer settings."""
    filament: list[SlicerSetting] = []
    printer: list[SlicerSetting] = []
    process: list[SlicerSetting] = []


class CloudDevice(BaseModel):
    """A bound printer device."""
    dev_id: str
    name: str
    dev_model_name: Optional[str] = None
    dev_product_name: Optional[str] = None
    online: bool = False
