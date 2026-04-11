from typing import Literal

from pydantic import BaseModel, Field, field_validator


class GroupBrief(BaseModel):
    """Brief group info for embedding in user responses."""

    id: int
    name: str

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"
    user: "UserResponse | None" = None
    # Set when 2FA is required; the frontend must call /auth/2fa/verify
    requires_2fa: bool = False
    pre_auth_token: str | None = None
    two_fa_methods: list[str] = []


class UserCreate(BaseModel):
    username: str
    password: str | None = None  # Optional when advanced auth is enabled
    email: str | None = None
    role: str = "user"
    group_ids: list[int] | None = None


class UserUpdate(BaseModel):
    username: str | None = None
    password: str | None = None
    email: str | None = None
    role: str | None = None
    is_active: bool | None = None
    group_ids: list[int] | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: str | None = None
    role: str  # Deprecated, kept for backward compatibility
    is_active: bool
    is_admin: bool  # Computed from role and group membership
    auth_source: str = "local"  # "local" or "ldap"
    groups: list[GroupBrief] = []
    permissions: list[str] = []  # All permissions from groups
    created_at: str

    class Config:
        from_attributes = True


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class SetupRequest(BaseModel):
    auth_enabled: bool
    admin_username: str | None = None
    admin_password: str | None = None


class SetupResponse(BaseModel):
    auth_enabled: bool
    admin_created: bool | None = None


class ForgotPasswordRequest(BaseModel):
    email: str


class ForgotPasswordConfirmRequest(BaseModel):
    token: str = Field(..., max_length=128)
    new_password: str = Field(..., min_length=8, max_length=256)


class ForgotPasswordResponse(BaseModel):
    message: str


class ResetPasswordRequest(BaseModel):
    user_id: int


class ResetPasswordResponse(BaseModel):
    message: str


class SMTPSettings(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_username: str | None = None  # Optional when auth is disabled
    smtp_password: str | None = None  # Optional for read operations or when auth is disabled
    smtp_security: str = "starttls"  # 'starttls', 'ssl', 'none'
    smtp_auth_enabled: bool = True
    smtp_from_email: str
    smtp_from_name: str = "BamBuddy"
    # Deprecated field for backward compatibility
    smtp_use_tls: bool | None = None


class TestSMTPRequest(BaseModel):
    test_recipient: str


class TestSMTPResponse(BaseModel):
    success: bool
    message: str


# ---------------------------------------------------------------------------
# 2FA / MFA schemas
# ---------------------------------------------------------------------------


class TwoFAStatusResponse(BaseModel):
    totp_enabled: bool
    email_otp_enabled: bool
    backup_codes_remaining: int


class TOTPSetupResponse(BaseModel):
    """Returned when a user initiates TOTP setup.  The frontend should display
    the QR code image (base64 PNG) and ask the user to scan it, then call
    /auth/2fa/totp/enable with a valid code to confirm."""

    secret: str  # base32 secret (shown as fallback text)
    qr_code_b64: str  # base64-encoded PNG of the QR code
    issuer: str


class TOTPEnableRequest(BaseModel):
    code: str  # 6-digit TOTP code from the authenticator app

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or len(v) != 6:
            raise ValueError("TOTP code must be exactly 6 digits")
        return v


class TOTPEnableResponse(BaseModel):
    message: str
    backup_codes: list[str]  # plain-text codes shown once; user must save them


class TOTPDisableRequest(BaseModel):
    """Requires a valid TOTP code OR a backup code to disable TOTP."""

    code: str = Field(..., max_length=128)


class BackupCodesResponse(BaseModel):
    backup_codes: list[str]
    message: str


class EmailOTPEnableRequest(BaseModel):
    """No body required — email is taken from the authenticated user's profile."""

    pass


class TwoFAVerifyRequest(BaseModel):
    pre_auth_token: str = Field(..., max_length=128)
    code: str = Field(..., max_length=128)
    method: Literal["totp", "email", "backup"] = "totp"


class TwoFAVerifyResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class EmailOTPSendRequest(BaseModel):
    pre_auth_token: str = Field(..., max_length=128)


class EmailOTPEnableConfirmRequest(BaseModel):
    """Body for the second step of email OTP enable: verify the proof-of-possession code."""

    setup_token: str = Field(..., max_length=128)
    code: str = Field(..., max_length=8, min_length=6)


class EmailOTPDisableRequest(BaseModel):
    """Requires the account password to disable email OTP."""

    password: str = Field(..., max_length=256)


# ---------------------------------------------------------------------------
# OIDC schemas
# ---------------------------------------------------------------------------


def _validate_icon_url(v: str | None) -> str | None:
    """Reject non-HTTPS icon URLs to prevent SSRF / mixed-content issues."""
    if v is None:
        return v
    if not v.startswith("https://"):
        raise ValueError("icon_url must start with https://")
    return v


def _validate_issuer_url(v: str | None) -> str | None:
    """Reject non-HTTP(S) issuer URLs to prevent SSRF via internal scheme abuse."""
    if v is None:
        return v
    if not v.startswith(("https://", "http://")):
        raise ValueError("issuer_url must start with https:// or http://")
    return v


class OIDCProviderCreate(BaseModel):
    name: str
    issuer_url: str
    client_id: str
    client_secret: str
    scopes: str = "openid email profile"
    is_enabled: bool = True
    auto_create_users: bool = False
    auto_link_existing_accounts: bool = True
    icon_url: str | None = None

    @field_validator("issuer_url")
    @classmethod
    def validate_issuer_url(cls, v: str) -> str:
        result = _validate_issuer_url(v)
        assert result is not None
        return result

    @field_validator("icon_url")
    @classmethod
    def validate_icon_url(cls, v: str | None) -> str | None:
        return _validate_icon_url(v)


class OIDCProviderUpdate(BaseModel):
    name: str | None = None
    issuer_url: str | None = None

    @field_validator("issuer_url")
    @classmethod
    def validate_issuer_url(cls, v: str | None) -> str | None:
        return _validate_issuer_url(v)

    client_id: str | None = None
    client_secret: str | None = None
    scopes: str | None = None
    is_enabled: bool | None = None
    auto_create_users: bool | None = None
    auto_link_existing_accounts: bool | None = None
    icon_url: str | None = None

    @field_validator("icon_url")
    @classmethod
    def validate_icon_url(cls, v: str | None) -> str | None:
        return _validate_icon_url(v)


class OIDCProviderResponse(BaseModel):
    id: int
    name: str
    issuer_url: str
    client_id: str
    scopes: str
    is_enabled: bool
    auto_create_users: bool
    auto_link_existing_accounts: bool = True
    icon_url: str | None = None

    class Config:
        from_attributes = True


class OIDCAuthorizeResponse(BaseModel):
    auth_url: str


class OIDCExchangeRequest(BaseModel):
    oidc_token: str = Field(..., max_length=128)


class OIDCLinkResponse(BaseModel):
    id: int
    provider_id: int
    provider_name: str
    provider_email: str | None = None
    created_at: str
