"""Pydantic schemas for K-profile (pressure advance) management."""

from pydantic import BaseModel


class KProfile(BaseModel):
    """A pressure advance (K) calibration profile stored on the printer."""

    slot_id: int  # Storage slot on printer (limited capacity ~20 slots)
    extruder_id: int = 0  # 0 or 1 for dual nozzle printers
    nozzle_id: str  # e.g., "HS00-0.4" (hardened steel 0.4mm)
    nozzle_diameter: str  # e.g., "0.4"
    filament_id: str  # Bambu filament identifier
    name: str  # User-defined name for the profile
    k_value: str  # Pressure advance coefficient as string, e.g., "0.020000"
    n_coef: str = "0.000000"  # N coefficient (usually 0)
    ams_id: int = 0  # AMS unit ID
    tray_id: int = -1  # AMS tray ID (-1 if not linked)
    setting_id: str | None = None  # Unique setting identifier


class KProfileCreate(BaseModel):
    """Schema for creating/updating a K-profile."""

    slot_id: int = 0  # Storage slot, 0 for new profiles
    extruder_id: int = 0
    nozzle_id: str
    nozzle_diameter: str
    filament_id: str
    name: str
    k_value: str
    n_coef: str = "0.000000"
    ams_id: int = 0
    tray_id: int = -1
    setting_id: str | None = None


class KProfilesResponse(BaseModel):
    """Response containing K-profiles from a printer."""

    profiles: list[KProfile]
    nozzle_diameter: str  # Current nozzle filter


class KProfileDelete(BaseModel):
    """Schema for deleting a K-profile."""

    slot_id: int  # cali_idx - calibration index to delete
    extruder_id: int = 0
    nozzle_id: str  # e.g., "HH00-0.4"
    nozzle_diameter: str  # e.g., "0.4"
    filament_id: str  # Bambu filament identifier
    setting_id: str | None = None  # Setting ID (for X1C series)
