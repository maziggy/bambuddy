"""Schemas for the 3D Filament Profiles connection, lookup and spool links (#2981)."""

from typing import Literal

from pydantic import BaseModel, Field


class ThreeDfpDeviceStartResponse(BaseModel):
    """The code the user approves on 3dfilamentprofiles.com. The device code
    itself stays on the server."""

    user_code: str
    verification_uri: str
    verification_uri_complete: str
    interval: int
    expires_in: int


ThreeDfpDevicePollStatus = Literal["authorization_pending", "slow_down", "access_denied", "expired_token", "complete"]


class ThreeDfpDevicePollResponse(BaseModel):
    status: ThreeDfpDevicePollStatus
    connected: bool = False


class ThreeDfpStatusResponse(BaseModel):
    """What Bambuddy has stored, without asking 3DFP."""

    connected: bool
    email: str | None = None


class ThreeDfpQuota(BaseModel):
    limit: int | None = None
    remaining: int | None = None
    resets_at: str | None = None


class ThreeDfpAccountResponse(BaseModel):
    """The live account state from 3DFP's ``/me``."""

    email: str | None = None
    plan: str | None = None
    active_scopes: list[str] = Field(default_factory=list)
    quota: ThreeDfpQuota | None = None
    upgrade_url: str | None = None


class ThreeDfpStore(BaseModel):
    region: str | None = None
    price: float | None = None
    currency: str | None = None
    availability: str | None = None
    updated_at: str | None = None
    # A 3dfp.to redirect that 3DFP keeps pointed at the product; used as given.
    url: str


class ThreeDfpFilament(BaseModel):
    """A 3DFP filament as the spool form needs it. A barcode lookup returns
    summaries, without the weights, temperatures and stores."""

    id: int
    brand_name: str | None = None
    material: str | None = None
    material_type: str | None = None
    color: str | None = None
    rgb: str | None = None
    bambu_filament_id: str | None = None
    discontinued: bool = False
    url: str | None = None
    nominal_weight: float | None = None
    spool_weight: float | None = None
    temp_min: float | None = None
    temp_max: float | None = None
    stores: list[ThreeDfpStore] = Field(default_factory=list)


class ThreeDfpLookupResponse(BaseModel):
    results: list[ThreeDfpFilament]


class ThreeDfpSpoolLinkBody(BaseModel):
    """The 3DFP filament a spool is linked to; null removes the link."""

    filament_id: int | None = Field(default=None, gt=0)
