"""Schemas for Home Assistant entities bound to a printer (#1148, #448)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PrinterHASensorBase(BaseModel):
    printer_id: int
    name: str = Field(..., min_length=1, max_length=100)
    # max_length matches the column (String(255)). The pattern's [a-z0-9_]+ is
    # unbounded, so a direct API caller could send a longer id: SQLite stores
    # it, PostgreSQL raises DataError, and it would surface as a 500 rather
    # than a 422. Same bound as the location sibling.
    entity_id: str = Field(..., max_length=255, pattern=r"^(binary_sensor|sensor)\.[a-z0-9_]+$")
    kind: Literal["binary", "numeric"] = "binary"
    device_class: str | None = Field(default=None, max_length=32)
    unit: str | None = Field(default=None, max_length=16)

    alert_state: Literal["on", "off"] | None = None
    alert_above: float | None = None
    alert_below: float | None = None

    block_print: bool = False
    notify_on_alert: bool = False
    show_on_printer_card: bool = True
    sort_order: int = Field(default=0, ge=0, le=999)

    @model_validator(mode="after")
    def validate_kind_matches_entity(self) -> "PrinterHASensorBase":
        domain = self.entity_id.split(".")[0]
        expected = "binary" if domain == "binary_sensor" else "numeric"
        if self.kind != expected:
            raise ValueError(f"kind must be '{expected}' for a {domain} entity")

        # Alert fields are per-kind: a threshold on a door contact and an
        # on/off alert on a thermometer are both configuration the poller
        # would silently ignore, so reject them at the edge instead.
        if self.kind == "binary" and (self.alert_above is not None or self.alert_below is not None):
            raise ValueError("alert_above/alert_below only apply to numeric sensors")
        if self.kind == "numeric" and self.alert_state is not None:
            raise ValueError("alert_state only applies to binary sensors")
        if self.alert_above is not None and self.alert_below is not None and self.alert_below >= self.alert_above:
            raise ValueError("alert_below must be lower than alert_above")

        # An interlock or a notification with nothing to trigger on would never
        # fire — that reads as a broken feature, not as a no-op.
        if (self.block_print or self.notify_on_alert) and not self._has_alert_condition():
            raise ValueError("block_print and notify_on_alert require an alert condition")
        return self

    def _has_alert_condition(self) -> bool:
        return self.alert_state is not None or self.alert_above is not None or self.alert_below is not None


class PrinterHASensorCreate(PrinterHASensorBase):
    pass


class PrinterHASensorUpdate(BaseModel):
    """Partial update. Validated against the merged row in the route, because
    the per-kind rules above need fields this payload may not carry."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    # Same column-width bound as the base schema; PATCH reaches the same row.
    entity_id: str | None = Field(default=None, max_length=255, pattern=r"^(binary_sensor|sensor)\.[a-z0-9_]+$")
    kind: Literal["binary", "numeric"] | None = None
    device_class: str | None = Field(default=None, max_length=32)
    unit: str | None = Field(default=None, max_length=16)
    alert_state: Literal["on", "off"] | None = None
    alert_above: float | None = None
    alert_below: float | None = None
    block_print: bool | None = None
    notify_on_alert: bool | None = None
    show_on_printer_card: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=999)


class PrinterHASensorResponse(PrinterHASensorBase):
    # Reads stay tolerant of what writes now reject: this feature shipped
    # before entity_id was bounded, and SQLite never enforced the column's 255,
    # so a row longer than that can genuinely exist. Inheriting the bound would
    # turn it into a 500 on the list route — the same failure the bound was
    # added to prevent, moved from the write path to the read path. The pattern
    # is dropped with it, for the same generation of rows. Writes are unchanged.
    entity_id: str

    id: int
    last_state: str | None = None
    last_changed: datetime | None = None
    last_checked: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PrinterHASensorReading(BaseModel):
    """One sensor's live state, as the printer card renders it."""

    id: int
    name: str
    entity_id: str
    kind: str
    device_class: str | None = None
    unit: str | None = None
    # Raw HA state: "on"/"off" for binary, the numeric string for sensors.
    # None when the entity is unavailable or has not been polled yet.
    state: str | None = None
    value: float | None = None  # numeric sensors only, parsed from state
    alerting: bool = False
    block_print: bool = False
    reachable: bool = True
    last_changed: datetime | None = None


class HADisplayEntity(BaseModel):
    """A bindable entity, as offered by the picker."""

    entity_id: str
    friendly_name: str
    state: str | None = None
    domain: str
    device_class: str | None = None
    unit_of_measurement: str | None = None
