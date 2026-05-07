from pydantic import BaseModel, Field, model_validator


class SpoolmanFilamentPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    spool_weight: float | None = Field(None, ge=0.0, le=10_000.0)
    keep_existing_spools: bool = False

    @model_validator(mode="after")
    def keep_existing_requires_weight(self) -> "SpoolmanFilamentPatch":
        if self.keep_existing_spools and self.spool_weight is None:
            raise ValueError("keep_existing_spools=True requires spool_weight to be provided")
        return self


class SpoolmanSlotAssignmentEnriched(BaseModel):
    """Slot assignment row enriched with printer name and AMS label.

    ``printer_name`` is null only in the cascade-deleted edge case where the
    Printer relation has been removed. ``ams_label`` is null when no
    ``ams_labels`` row matches the slot's MQTT serial (or the synthetic
    ``f"p{printer_id}a{ams_id}"`` fallback key).
    """

    printer_id: int
    printer_name: str | None
    ams_id: int
    tray_id: int
    spoolman_spool_id: int
    ams_label: str | None
