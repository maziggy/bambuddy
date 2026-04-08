from pydantic import BaseModel, field_validator


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

    code: str


class BackupCodesResponse(BaseModel):
    backup_codes: list[str]
    message: str


class EmailOTPEnableRequest(BaseModel):
    """No body required — email is taken from the authenticated user's profile."""

    pass


class TwoFAVerifyRequest(BaseModel):
    pre_auth_token: str
    code: str
    method: str = "totp"  # "totp" | "email" | "backup"


class TwoFAVerifyResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class EmailOTPSendRequest(BaseModel):
    pre_auth_token: str


# ---------------------------------------------------------------------------
# OIDC schemas
# ---------------------------------------------------------------------------


class OIDCProviderCreate(BaseModel):
    name: str
    issuer_url: str
    client_id: str
    client_secret: str
    scopes: str = "openid email profile"
    is_enabled: bool = True
    auto_create_users: bool = False
    icon_url: str | None = None


class OIDCProviderUpdate(BaseModel):
    name: str | None = None
    issuer_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scopes: str | None = None
    is_enabled: bool | None = None
    auto_create_users: bool | None = None
    icon_url: str | None = None


class OIDCProviderResponse(BaseModel):
    id: int
    name: str
    issuer_url: str
    client_id: str
    scopes: str
    is_enabled: bool
    auto_create_users: bool
    icon_url: str | None = None

    class Config:
        from_attributes = True


class OIDCAuthorizeResponse(BaseModel):
    auth_url: str


class OIDCExchangeRequest(BaseModel):
    oidc_token: str


class OIDCLinkResponse(BaseModel):
    id: int
    provider_id: int
    provider_name: str
    provider_email: str | None = None
    created_at: str
