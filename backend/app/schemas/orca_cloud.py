"""Schemas for Orca Cloud device-pairing auth + profile sync endpoints."""

from typing import Literal

from pydantic import BaseModel, Field


class OrcaDeviceStartResponse(BaseModel):
    """Returned by ``POST /orca-cloud/device/start``. The frontend shows
    ``user_code`` and a clickable/QR ``verification_uri_complete``; the user
    approves in their Orca Cloud settings. The ``device_code`` itself is a
    secret and stays server-side — it is deliberately NOT in this response."""

    user_code: str = Field(..., description="Short code the user confirms on the approval page")
    verification_uri: str = Field(..., description="Approval page URL")
    verification_uri_complete: str = Field(..., description="Approval page URL with the code pre-filled")
    interval: int = Field(..., description="Seconds the frontend should wait between poll calls")
    expires_in: int = Field(..., description="Seconds until this pairing attempt expires")


# Poll outcomes surfaced to the frontend. ``authorization_pending`` /
# ``slow_down`` mean keep polling; ``access_denied`` / ``expired_token`` are
# terminal (restart the flow); ``complete`` means paired.
OrcaDevicePollStatus = Literal[
    "authorization_pending",
    "slow_down",
    "access_denied",
    "expired_token",
    "complete",
]


class OrcaDevicePollResponse(BaseModel):
    """Returned by ``POST /orca-cloud/device/poll`` — one poll attempt."""

    status: OrcaDevicePollStatus
    connected: bool = False
    email: str | None = None
    user_id: str | None = None


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
