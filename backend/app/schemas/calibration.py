"""Schemas for Bambu's native printer-calibration command."""

from pydantic import BaseModel, Field


class NativeCalibrationRequest(BaseModel):
    """User intent; the server derives the safe MQTT option bitmask."""

    # None preserves compatibility with callers of the former full-calibration
    # endpoint. New UI callers always provide an explicit selection.
    stages: list[str] | None = Field(default=None, max_length=6)
    # FINISH is accepted only with this explicit confirmation. It does not
    # clear Bambuddy's separate print-queue plate-clear gate.
    plate_clear_confirmed: bool = False
