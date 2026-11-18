"""Schemas for build plate tracking (#1306)."""

from pydantic import BaseModel, Field, field_validator

from backend.app.services.build_plate_config import PATTERN_MAX_LENGTH
from backend.app.utils.bed_types import BED_TYPES


def _check_base_type(v: str | None) -> str | None:
    if v is not None and v not in BED_TYPES:
        raise ValueError(f"base_type must be one of: {', '.join(BED_TYPES)}")
    return v


def _clean_name(v: str | None) -> str | None:
    if v is None:
        return None
    v = " ".join(v.split())
    return v or None


class PlatePatternIn(BaseModel):
    base_type: str
    name: str = Field(..., max_length=PATTERN_MAX_LENGTH)

    @field_validator("base_type")
    @classmethod
    def _validate_base_type(cls, v):
        return _check_base_type(v)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v):
        v = _clean_name(v)
        if not v:
            raise ValueError("pattern name must not be empty")
        return v


class BuildPlateConfigUpdate(BaseModel):
    """The plates the user owns: the ticked base types and the patterns under them."""

    types: list[str]
    patterns: list[PlatePatternIn] = []

    @field_validator("types")
    @classmethod
    def _validate_types(cls, v):
        for key in v:
            _check_base_type(key)
        return v


class PlateTypeInfo(BaseModel):
    key: str
    label: str
    owned: bool
    installed_on: list[int] = []  # printer ids with a plate of this type fitted
    waiting_jobs: int = 0  # pending jobs whose file needs this type


class PlatePatternInfo(BaseModel):
    base_type: str
    name: str
    label: str  # "Smooth PEI Plate · Carbon Fiber"
    installed_on: list[int] = []
    waiting_jobs: int = 0  # pending jobs that asked for this pattern


class BuildPlateConfigResponse(BaseModel):
    types: list[PlateTypeInfo]
    patterns: list[PlatePatternInfo]
    pattern_suggestions: list[str]


class InstalledPlateUpdate(BaseModel):
    """Body for recording the plate on a printer; a null ``plate_type`` stops tracking it."""

    plate_type: str | None = None
    pattern: str | None = Field(default=None, max_length=PATTERN_MAX_LENGTH)

    @field_validator("plate_type")
    @classmethod
    def _validate_plate_type(cls, v):
        return _check_base_type(v)

    @field_validator("pattern")
    @classmethod
    def _validate_pattern(cls, v):
        return _clean_name(v)
