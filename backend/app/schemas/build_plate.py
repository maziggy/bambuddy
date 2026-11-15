"""Schemas for build plate tracking (#1306)."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from backend.app.schemas.not_null import IgnoresNullForRequiredColumns
from backend.app.utils.bed_types import BED_TYPES


def _check_base_type(v: str | None) -> str | None:
    if v is not None and v not in BED_TYPES:
        raise ValueError(f"base_type must be one of: {', '.join(BED_TYPES)}")
    return v


def _check_image(v: str | None) -> str | None:
    """Only site-relative paths and https URLs — never javascript:/data: URIs."""
    if v is None:
        return v
    v = v.strip()
    if not v:
        return None
    if v.startswith("/") and not v.startswith("//"):
        return v
    if v.lower().startswith("https://"):
        return v
    raise ValueError("image must be a site path (/img/...) or an https:// URL")


class BuildPlateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    base_type: str
    pattern: str | None = Field(default=None, max_length=100)
    image: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    enabled: bool = True
    sort_order: int = 200

    @field_validator("base_type")
    @classmethod
    def _validate_base_type(cls, v):
        return _check_base_type(v)

    @field_validator("image")
    @classmethod
    def _validate_image(cls, v):
        return _check_image(v)


class BuildPlateUpdate(IgnoresNullForRequiredColumns):
    __orm_table__ = "build_plates"

    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_type: str | None = None
    pattern: str | None = Field(default=None, max_length=100)
    image: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    enabled: bool | None = None
    sort_order: int | None = None

    @field_validator("base_type")
    @classmethod
    def _validate_base_type(cls, v):
        return _check_base_type(v)

    @field_validator("image")
    @classmethod
    def _validate_image(cls, v):
        return _check_image(v)


class BuildPlateResponse(BaseModel):
    id: int
    builtin_key: str | None = None
    is_builtin: bool
    name: str
    base_type: str
    base_type_label: str
    pattern: str | None = None
    image: str | None = None
    notes: str | None = None
    enabled: bool
    sort_order: int
    # Printers that currently have this plate installed.
    installed_on: list[int] = []
    # Pending queue jobs waiting for this specific plate.
    required_by_pending: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BedTypeOption(BaseModel):
    key: str
    label: str


class InstalledPlateUpdate(BaseModel):
    """Body for swapping the plate on a printer; null = stop tracking it."""

    plate_id: int | None = None
