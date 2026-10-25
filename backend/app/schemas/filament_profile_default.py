from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from backend.app.schemas.spool import SpoolFilamentPresetBase, SpoolKProfileBase


class FilamentProfileDefaultKey(BaseModel):
    """The product a default is for (#1326). ``color_name`` "" = every colour."""

    brand: str = Field(default="", max_length=100)
    material: str = Field(..., min_length=1, max_length=50)
    subtype: str = Field(default="", max_length=50)
    color_name: str = Field(default="", max_length=100)

    @field_validator("brand", "material", "subtype", "color_name", mode="before")
    @classmethod
    def _strip(cls, value):
        return (value or "").strip() if value is None or isinstance(value, str) else value


class FilamentProfileDefaultWrite(FilamentProfileDefaultKey):
    """Everything a spool form's Printers tab holds, plus the spool's own preset."""

    slicer_filament: str | None = Field(default=None, max_length=128)
    slicer_filament_name: str | None = Field(default=None, max_length=255)
    presets: list[SpoolFilamentPresetBase] = Field(default_factory=list)
    k_profiles: list[SpoolKProfileBase] = Field(default_factory=list)


class FilamentProfileDefaultResponse(FilamentProfileDefaultWrite):
    id: int
    updated_at: datetime


class ResolvedProfileDefault(BaseModel):
    """What a new spool of a product gets: the all-colours default with the
    colour's own one laid over it."""

    slicer_filament: str | None = None
    slicer_filament_name: str | None = None
    presets: list[SpoolFilamentPresetBase] = Field(default_factory=list)
    k_profiles: list[SpoolKProfileBase] = Field(default_factory=list)
    default_ids: list[int] = Field(default_factory=list)


class ProfileDefaultApplyResult(BaseModel):
    """How many spools ``Apply to existing spools`` filled something in on."""

    updated: int
