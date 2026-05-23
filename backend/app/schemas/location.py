from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from backend.app.services.location_service import normalize_location_name


class LocationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    identifier: str | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return normalize_location_name(v)


class LocationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    identifier: str | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_location_name(v)


class LocationResponse(BaseModel):
    id: int
    name: str
    identifier: str | None = None
    spool_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
