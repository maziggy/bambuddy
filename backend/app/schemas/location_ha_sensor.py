"""Schemas for Home Assistant entities bound to a storage location (#2824)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.app.schemas.printer_ha_sensor import HADisplayEntity  # noqa: F401


class LocationHASensorBase(BaseModel):
    location_id: int
    name: str = Field(..., min_length=1, max_length=100)
    # max_length matches the column (String(255)). The pattern's [a-z0-9_]+ is
    # unbounded, so a direct API caller — the picker only ever offers real
    # Home Assistant ids — could send a longer one: SQLite stores it, but
    # PostgreSQL raises DataError, and the create/update routes only map
    # IntegrityError, so it would surface as a 500 instead of a 422.
    entity_id: str = Field(..., max_length=255, pattern=r"^(binary_sensor|sensor)\.[a-z0-9_]+$")
    kind: Literal["binary", "numeric"] = "binary"
    device_class: str | None = Field(default=None, max_length=32)
    unit: str | None = Field(default=None, max_length=16)

    alert_state: Literal["on", "off"] | None = None
    # allow_inf_nan=False: pydantic's lax mode coerces the strings "nan"/"inf"
    # into real NaN/Infinity floats. A NaN threshold satisfies the "notify
    # needs an alert condition" rule below yet every comparison against it is
    # False — a notification that can never fire — and it skips the
    # below-vs-above ordering check the same way. Responses serialize NaN as
    # null, so the UI would show an empty field over a poisoned row.
    alert_above: float | None = Field(default=None, allow_inf_nan=False)
    alert_below: float | None = Field(default=None, allow_inf_nan=False)

    notify_on_alert: bool = False
    show_on_card: bool = True
    sort_order: int = Field(default=0, ge=0, le=999)

    @model_validator(mode="after")
    def validate_kind_matches_entity(self) -> "LocationHASensorBase":
        domain = self.entity_id.split(".")[0]
        expected = "binary" if domain == "binary_sensor" else "numeric"
        if self.kind != expected:
            raise ValueError(f"kind must be '{expected}' for a {domain} entity")

        # Alert fields are per-kind: a threshold on a battery sensor and an
        # on/off alert on a temperature reading are both configuration the
        # poller would silently ignore, so reject them at the edge instead.
        if self.kind == "binary" and (self.alert_above is not None or self.alert_below is not None):
            raise ValueError("alert_above/alert_below only apply to numeric sensors")
        if self.kind == "numeric" and self.alert_state is not None:
            raise ValueError("alert_state only applies to binary sensors")
        if self.alert_above is not None and self.alert_below is not None and self.alert_below >= self.alert_above:
            raise ValueError("alert_below must be lower than alert_above")

        # A notification with nothing to trigger on would never fire — that
        # reads as a broken feature, not as a no-op.
        if self.notify_on_alert and not self._has_alert_condition():
            raise ValueError("notify_on_alert requires an alert condition")
        return self

    def _has_alert_condition(self) -> bool:
        return self.alert_state is not None or self.alert_above is not None or self.alert_below is not None


class LocationHASensorCreate(LocationHASensorBase):
    pass


class LocationHASensorUpdate(BaseModel):
    """Partial update. Validated against the merged row in the route, because
    the per-kind rules above need fields this payload may not carry."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    # Same column-width bound as the base schema; PATCH reaches the same row.
    entity_id: str | None = Field(default=None, max_length=255, pattern=r"^(binary_sensor|sensor)\.[a-z0-9_]+$")
    kind: Literal["binary", "numeric"] | None = None
    device_class: str | None = Field(default=None, max_length=32)
    unit: str | None = Field(default=None, max_length=16)
    alert_state: Literal["on", "off"] | None = None
    # Same allow_inf_nan story as the base schema. The route's merged-row
    # re-validation would catch these too, but rejecting them here keeps the
    # error attached to the offending field.
    alert_above: float | None = Field(default=None, allow_inf_nan=False)
    alert_below: float | None = Field(default=None, allow_inf_nan=False)
    notify_on_alert: bool | None = None
    show_on_card: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=999)


class LocationHASensorResponse(LocationHASensorBase):
    # Reads must tolerate what writes now reject, or one legacy row 500s the
    # whole list. Three constraints are relaxed here on purpose:
    #
    # * the NaN/inf thresholds a row could carry before allow_inf_nan landed —
    #   serialization turns them into null, which is also what the edit form
    #   should show;
    # * the entity_id length bound, for a row created before max_length existed
    #   (SQLite never enforced the column's 255, so those rows are real);
    # * the entity_id pattern, which the same generation of rows predates.
    #
    # Every one of them is still rejected on the way in, so this widens what
    # can be read back, never what can be stored.
    alert_above: float | None = None
    alert_below: float | None = None
    entity_id: str

    id: int
    last_state: str | None = None
    last_changed: datetime | None = None
    last_checked: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LocationHASensorReading(BaseModel):
    """One sensor's live state, as the filament card and inventory table render it."""

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
    reachable: bool = True
    alert_state: str | None = None
    alert_above: float | None = None
    alert_below: float | None = None
    last_changed: datetime | None = None
    # Lets a consumer that fetched the unfiltered (show_on_card=False) list
    # still pick out the card-visible subset itself, instead of issuing a
    # second request for the same location.
    show_on_card: bool = True
