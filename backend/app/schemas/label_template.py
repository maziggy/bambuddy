"""Sheet layouts for custom label sheets, and the saved templates that hold
them (#3296)."""

import unicodedata
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.services.label_renderer import (
    MAX_COLS,
    MAX_LENGTH_MM,
    MAX_ROWS,
    MIN_LABEL_H_MM,
    MIN_LABEL_W_MM,
    PageSize,
    SheetLayout,
)

# Each character can casefold to at most three ("ﬃ" -> "ffi"), so a 100-character
# name always has a key that fits the 300-character column.
NAME_MAX_LENGTH = 100
NAME_KEY_MAX_LENGTH = 300


def label_template_name_key(name: str) -> str:
    """What makes two names the same template: case and Unicode composition
    ignored, so "Café" typed with a combining accent matches the composed one."""
    return unicodedata.normalize("NFC", name).casefold()


class SheetLayoutSpec(BaseModel):
    """A sheet layout as a request carries it. Lengths are in millimetres."""

    model_config = ConfigDict(allow_inf_nan=False)

    page: PageSize
    label_w_mm: float = Field(ge=MIN_LABEL_W_MM, le=MAX_LENGTH_MM)
    label_h_mm: float = Field(ge=MIN_LABEL_H_MM, le=MAX_LENGTH_MM)
    cols: int = Field(ge=1, le=MAX_COLS)
    rows: int = Field(ge=1, le=MAX_ROWS)
    top_mm: float = Field(ge=0, le=MAX_LENGTH_MM)
    left_mm: float = Field(ge=0, le=MAX_LENGTH_MM)
    col_gap_mm: float = Field(default=0.0, ge=0, le=MAX_LENGTH_MM)
    row_gap_mm: float = Field(default=0.0, ge=0, le=MAX_LENGTH_MM)

    @model_validator(mode="after")
    def fits_on_page(self) -> "SheetLayoutSpec":
        problem = self.to_layout().problem()
        if problem:
            raise ValueError(problem)
        return self

    def to_layout(self) -> SheetLayout:
        return SheetLayout(**self.model_dump())


class SheetLayoutResponse(BaseModel):
    """A stored layout, returned as saved. Not re-validated, so a template saved
    under looser limits still lists rather than failing the whole response."""

    page: str
    label_w_mm: float
    label_h_mm: float
    cols: int
    rows: int
    top_mm: float
    left_mm: float
    col_gap_mm: float
    row_gap_mm: float


class LabelTemplateIn(BaseModel):
    """Create or replace a saved template: a name and the whole layout."""

    name: str = Field(..., min_length=1, max_length=NAME_MAX_LENGTH)
    layout: SheetLayoutSpec

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        v = unicodedata.normalize("NFC", v.strip())
        if not v:
            raise ValueError("name must not be empty")
        # NFC can (rarely) lengthen a name, so both columns are checked again.
        if len(v) > NAME_MAX_LENGTH or len(label_template_name_key(v)) > NAME_KEY_MAX_LENGTH:
            raise ValueError("name is too long")
        return v

    @property
    def name_key(self) -> str:
        return label_template_name_key(self.name)


class LabelTemplateResponse(BaseModel):
    id: int
    name: str
    layout: SheetLayoutResponse
    created_at: datetime
    updated_at: datetime
