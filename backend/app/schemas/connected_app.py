from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from backend.app.schemas.print_queue import UTCDatetime

# RFC 7636: 43-128 chars from the unreserved set. The challenge is the
# base64url SHA-256 of the verifier, so it is always exactly 43 chars.
_PKCE_VERIFIER_PATTERN = r"^[A-Za-z0-9\-._~]{43,128}$"
_PKCE_CHALLENGE_PATTERN = r"^[A-Za-z0-9\-_]{43}$"


def _validate_redirect_uri(value: str) -> str:
    """Accept only an absolute http(s) URL without a fragment.

    Plain http is allowed: connected apps typically run on the same LAN as
    Bambuddy, often without TLS. What matters is that codes can only ever be
    delivered to the one URL an admin registered, and that is enforced by an
    exact match at authorize and token time.
    """
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("Callback URL must be an absolute http:// or https:// URL")
    if parts.fragment:
        raise ValueError("Callback URL must not contain a #fragment")
    if parts.username or parts.password:
        raise ValueError("Callback URL must not contain credentials")
    return value


class ConnectedAppCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    redirect_uri: str = Field(min_length=1, max_length=500)

    _check_redirect = field_validator("redirect_uri")(_validate_redirect_uri)


class ConnectedAppUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    redirect_uri: str | None = Field(default=None, min_length=1, max_length=500)
    enabled: bool | None = None

    @field_validator("redirect_uri")
    @classmethod
    def _check_redirect(cls, value: str | None) -> str | None:
        return None if value is None else _validate_redirect_uri(value)


class ConnectedAppResponse(BaseModel):
    id: int
    name: str
    client_id: str
    redirect_uri: str
    enabled: bool
    created_at: UTCDatetime
    last_used_at: UTCDatetime | None = None

    class Config:
        from_attributes = True


class ConnectedAppSecretResponse(ConnectedAppResponse):
    """Returned by create and rotate only: the one time the secret is shown."""

    client_secret: str


class ConnectAuthorizeInfo(BaseModel):
    app_name: str
    username: str
    already_granted: bool


class ConnectAuthorizeRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    redirect_uri: str = Field(min_length=1, max_length=500)
    code_challenge: str = Field(pattern=_PKCE_CHALLENGE_PATTERN)
    code_challenge_method: Literal["S256"]


class ConnectAuthorizeResponse(BaseModel):
    code: str
    redirect_uri: str


class ConnectTokenRequest(BaseModel):
    grant_type: Literal["authorization_code"]
    code: str = Field(min_length=1, max_length=128)
    redirect_uri: str = Field(min_length=1, max_length=500)
    client_id: str = Field(min_length=1, max_length=64)
    client_secret: str = Field(min_length=1, max_length=128)
    code_verifier: str = Field(pattern=_PKCE_VERIFIER_PATTERN)


class ConnectedUser(BaseModel):
    id: int
    username: str
    email: str | None = None
    is_admin: bool
    groups: list[str]
    permissions: list[str]


class ConnectTokenResponse(BaseModel):
    user: ConnectedUser
    issued_at: datetime
