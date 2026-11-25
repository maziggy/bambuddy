from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, model_validator

from backend.app.utils.tray_ids import AMS3H_UNIT_ID, EXTERNAL_AMS_ID


def _a_slot_ams_id(ams_id: int) -> int:
    """The ams_ids spoolman_slot_assignments' CHECK admits: AMS units 0-7, the
    AMS 3H 32, AMS-HT 128-191, the external slot 255. Checked on the request,
    so a write to Spoolman that goes with the slot row is never made for a row
    the database then refuses (SSA-9)."""
    if 0 <= ams_id <= 7 or 128 <= ams_id <= 191 or ams_id in (AMS3H_UNIT_ID, EXTERNAL_AMS_ID):
        return ams_id
    raise ValueError("ams_id must be 0-7 (AMS), 32 (AMS 3H), 128-191 (AMS-HT) or 255 (external)")


SlotAmsId = Annotated[int, AfterValidator(_a_slot_ams_id)]
# 4-8 only for ams_id 32 / 255: a request with both fields also runs check_slot_tray_id.
SlotTrayId = Annotated[int, Field(ge=0, le=8)]


def check_slot_tray_id(ams_id: int | None, tray_id: int | None) -> None:
    """Refuse slots 4-8 except on the AMS 3H (32) and the external inputs (255), as the table's CHECK does."""
    if ams_id is None or tray_id is None or tray_id <= 3:
        return
    if ams_id not in (AMS3H_UNIT_ID, EXTERNAL_AMS_ID):
        raise ValueError(
            f"tray_id must be 0-3 for ams_id {ams_id}: only the AMS 3H (32) and the external inputs (255) have slots 4-8"
        )


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
    # e.g. "H3C": labels its AMS 3H slots "3H-n" and its external inputs "Ext-n"
    printer_model: str | None = None
