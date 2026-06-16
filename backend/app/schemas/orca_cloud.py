"""Schemas for Orca Cloud auth + profile sync endpoints."""

from typing import Literal

from pydantic import BaseModel, Field

# The three OAuth providers Orca's sign-in surface offers. Supabase
# accepts the bare lowercase provider name in the authorize query string.
OrcaOAuthProvider = Literal["google", "apple", "github"]


class OrcaAuthStartRequest(BaseModel):
    """Body for ``POST /orca-cloud/auth/start``. Provider defaults to
    ``google`` so existing clients that send an empty body keep working."""

    provider: OrcaOAuthProvider = Field(default="google", description="OAuth provider to use for sign-in")


class OrcaAuthStartResponse(BaseModel):
    """Returned by ``POST /orca-cloud/auth/start``. The frontend opens
    ``auth_url`` in a new tab. After the user signs in to Orca, they copy the
    redirected URL from their address bar and POST it to
    ``/orca-cloud/auth/finish`` to complete the handshake."""

    auth_url: str = Field(..., description="URL to open for Orca Cloud sign-in")


class OrcaAuthFinishRequest(BaseModel):
    """Submitted by the frontend after the user pastes the callback URL from
    their browser. The URL contains a Supabase ``code`` (and our ``state``)
    that we exchange for tokens."""

    callback_url: str = Field(..., description="The full URL the browser was redirected to after sign-in")


class OrcaAuthPasswordRequest(BaseModel):
    """Body for ``POST /orca-cloud/auth/password``. Whether this succeeds
    depends on Orca's Supabase project — their desktop client refuses
    password payloads, but the web sign-in offers email+password as one
    option. We forward the credentials and surface the server's response.
    ``email`` is plain ``str`` rather than Pydantic's ``EmailStr`` to avoid
    pulling in the optional ``email-validator`` dependency — Supabase will
    reject malformed addresses with a clear error itself, and the existing
    Bambu Cloud login schema uses the same approach."""

    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class OrcaAuthStatusResponse(BaseModel):
    """Connection status for the Orca Cloud tab."""

    connected: bool
    email: str | None = None
    user_id: str | None = None


class OrcaProfileMeta(BaseModel):
    """A single profile, shaped to match the Bambu Cloud ``SlicerSetting``
    schema so the frontend can render Orca profiles with the existing
    Bambu Cloud visual components (cards, filter bar, grouping). Per-source
    differences (Orca's IDs are UUIDs not Bambu's ``PFU...`` prefix; Orca
    types are ``machine`` / ``process`` / ``filament`` whereas Bambu uses
    ``printer`` / ``process`` / ``filament``) are normalized at the route
    layer before the response leaves the backend."""

    setting_id: str
    name: str
    type: str
    version: str | None = None
    user_id: str | None = None
    updated_time: str | None = None
    is_custom: bool = True


class OrcaProfileListResponse(BaseModel):
    """Groups Orca profiles by type, matching ``SlicerSettingsResponse``."""

    filament: list[OrcaProfileMeta] = []
    printer: list[OrcaProfileMeta] = []
    process: list[OrcaProfileMeta] = []


class OrcaProfileDetail(BaseModel):
    """Single profile's full content, shaped to match ``SlicerSettingDetail``
    so the frontend's detail modal can render it without translation."""

    setting_id: str
    name: str
    type: str
    version: str | None = None
    base_id: str | None = None
    update_time: str | None = None
    setting: dict
