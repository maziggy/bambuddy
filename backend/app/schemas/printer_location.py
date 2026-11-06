"""Printer locations (groups) and their appearance (#2962)."""

from pydantic import BaseModel, Field, field_validator

from backend.app.schemas.not_null import IgnoresNullForRequiredColumns

# printers.location, print_queue.target_location and printer_locations.name are
# all VARCHAR(100). PostgreSQL refuses a longer value with an error mid-write,
# so it is refused here instead.
LOCATION_NAME_MAX_LENGTH = 100

_ICON_PATTERN = r"^[a-z0-9-]{1,50}$"
_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


def normalize_location_name(value: str | None) -> str | None:
    """A location as stored: trimmed, and None for blank (no location).

    "" and None both meant "no location" before this existed; folding them into
    one value keeps the exact-match filters from treating them as two groups.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("location must be a string")
    value = value.strip()
    if not value:
        return None
    if len(value) > LOCATION_NAME_MAX_LENGTH:
        raise ValueError(f"location must be at most {LOCATION_NAME_MAX_LENGTH} characters")
    return value


def _required_name(value: str) -> str:
    name = normalize_location_name(value)
    if name is None:
        raise ValueError("location name is required")
    return name


def _existing_name(value: str) -> str:
    """The name of a location that already exists, as stored.

    Not length-checked: SQLite never enforced the column width, so a stored
    location can be longer than a new one may be, and it must still be possible
    to rename or delete it.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("location name is required")
    return value.strip()


def _blank_to_none(value: str | None) -> str | None:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class PrinterLocationResponse(BaseModel):
    # None for a location that only exists on printers and has no row yet.
    id: int | None = None
    name: str
    icon: str | None = None
    color: str | None = None
    printer_count: int = 0


class PrinterLocationCreate(BaseModel):
    name: str
    icon: str | None = Field(default=None, pattern=_ICON_PATTERN)
    color: str | None = Field(default=None, pattern=_COLOR_PATTERN)

    _name = field_validator("name")(_required_name)
    _blank = field_validator("icon", "color", mode="before")(_blank_to_none)


class PrinterLocationUpdate(IgnoresNullForRequiredColumns):
    """Rename and/or restyle the location called ``name``.

    ``icon`` and ``color`` are only changed when sent; sending null or "" clears
    them.
    """

    __orm_table__ = "printer_locations"

    name: str
    new_name: str | None = None
    icon: str | None = Field(default=None, pattern=_ICON_PATTERN)
    color: str | None = Field(default=None, pattern=_COLOR_PATTERN)

    _name = field_validator("name")(_existing_name)
    _new_name = field_validator("new_name")(_required_name)
    _blank = field_validator("icon", "color", mode="before")(_blank_to_none)


class PrinterLocationDelete(BaseModel):
    names: list[str] = Field(min_length=1, max_length=500)

    @field_validator("names")
    @classmethod
    def _names(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(_existing_name(v) for v in values))


class PrinterLocationDeleteResult(BaseModel):
    deleted: int
    printers_ungrouped: int


class PrinterLocationAssign(BaseModel):
    """Move printers into ``location``, or out of any location with null."""

    printer_ids: list[int] = Field(min_length=1, max_length=1000)
    location: str | None = None

    _location = field_validator("location")(normalize_location_name)


class PrinterLocationAssignResult(BaseModel):
    moved: int
