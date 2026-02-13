from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ExternalLinkBase(BaseModel):
    """Base schema for external links."""

    name: str = Field(..., min_length=1, max_length=50, description="Display name for the link")
    url: str = Field(..., min_length=1, max_length=500, description="External URL")
    icon: str = Field(default="link", max_length=50, description="Lucide icon name")
    open_in_new_tab: bool = False

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL format."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class ExternalLinkCreate(ExternalLinkBase):
    """Schema for creating an external link."""

    pass


class ExternalLinkUpdate(BaseModel):
    """Schema for updating an external link (all fields optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    url: str | None = Field(default=None, min_length=1, max_length=500)
    icon: str | None = Field(default=None, max_length=50)
    open_in_new_tab: bool | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str | None) -> str | None:
        """Validate URL format."""
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class ExternalLinkResponse(ExternalLinkBase):
    """Response schema for external links."""

    id: int
    open_in_new_tab: bool
    custom_icon: str | None = None
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExternalLinkReorder(BaseModel):
    """Schema for reordering external links."""

    ids: list[int] = Field(..., description="List of link IDs in desired order")
