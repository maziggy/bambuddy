from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# Visual variant applied to a spool's swatch — purely cosmetic, does not
# affect MQTT/firmware. Kept independent of `subtype` so users can override
# the rendering hint without touching Bambu's categorical filament label.
# Mirrors the visual variants the spool form's `KNOWN_VARIANTS` exposes so
# the catalog and spool form share one vocabulary; structural variants like
# gradient/dual-color/tri-color/multicolor combine with `extra_colors` for
# rendering, surface effects (sparkle/wood/marble/glow/matte) layer overlays.
ALLOWED_EFFECT_TYPES = frozenset(
    {
        # Surface effects
        "sparkle",
        "wood",
        "marble",
        "glow",
        "matte",
        # Sheen / finish variants
        "silk",
        "galaxy",
        "rainbow",
        "metal",
        "translucent",
        # Multi-colour structures (drive gradient rendering when paired with extra_colors)
        "gradient",
        "dual-color",
        "tri-color",
        "multicolor",
    }
)

# Cap how many gradient stops we accept on input so a paste of arbitrary text
# can't blow up the stored value or downstream rendering.
MAX_EXTRA_COLOR_STOPS = 8


def normalize_extra_colors(value: str | None) -> str | None:
    """Parse comma-separated hex tokens into canonical lowercase form.

    Accepts 6- or 8-char hex per token, with or without leading `#`. Returns
    None for blank input, raises ValueError for malformed tokens or too many
    stops. Output is the comma-joined canonical form (no `#`, lowercase).
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    tokens = [tok.strip().lstrip("#").lower() for tok in raw.split(",") if tok.strip()]
    if not tokens:
        return None
    if len(tokens) > MAX_EXTRA_COLOR_STOPS:
        raise ValueError(f"extra_colors accepts at most {MAX_EXTRA_COLOR_STOPS} stops")
    for tok in tokens:
        if len(tok) not in (6, 8):
            raise ValueError(f"extra_colors token '{tok}' must be 6 or 8 hex chars")
        try:
            int(tok, 16)
        except ValueError as exc:
            raise ValueError(f"extra_colors token '{tok}' is not valid hex") from exc
    return ",".join(tokens)


def normalize_effect_type(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip().lower()
    if not trimmed:
        return None
    # Tolerate "Dual Color" / "dual_color" / "dual color" → "dual-color" so
    # users pasting from spool-subtype labels don't hit a validation wall.
    canonical = trimmed.replace("_", "-").replace(" ", "-")
    if canonical not in ALLOWED_EFFECT_TYPES:
        raise ValueError(f"effect_type must be one of: {sorted(ALLOWED_EFFECT_TYPES)}")
    return canonical


class SpoolBase(BaseModel):
    material: str = Field(..., min_length=1, max_length=50)
    subtype: str | None = None
    color_name: str | None = None
    rgba: str | None = Field(None, pattern=r"^[0-9A-Fa-f]{8}$")
    extra_colors: str | None = None
    effect_type: str | None = None
    brand: str | None = None

    @field_validator("extra_colors")
    @classmethod
    def _validate_extra_colors(cls, v: str | None) -> str | None:
        return normalize_extra_colors(v)

    @field_validator("effect_type")
    @classmethod
    def _validate_effect_type(cls, v: str | None) -> str | None:
        return normalize_effect_type(v)

    label_weight: int = 1000
    core_weight: int = 250
    core_weight_catalog_id: int | None = None
    weight_used: float = 0
    slicer_filament: str | None = None
    slicer_filament_name: str | None = None
    nozzle_temp_min: int | None = None
    nozzle_temp_max: int | None = None
    note: str | None = None
    tag_uid: str | None = None
    tray_uuid: str | None = None
    data_origin: str | None = None
    tag_type: str | None = None
    cost_per_kg: float | None = Field(default=None, ge=0)
    weight_locked: bool = False
    last_scale_weight: int | None = None
    last_weighed_at: datetime | None = None
    # User-defined category + per-spool low-stock threshold override (#729).
    category: str | None = Field(default=None, max_length=50)
    low_stock_threshold_pct: int | None = Field(default=None, ge=1, le=99)


class SpoolCreate(SpoolBase):
    pass


class SpoolBulkCreate(BaseModel):
    spool: SpoolCreate
    quantity: int = Field(default=1, ge=1, le=100)


class SpoolUpdate(BaseModel):
    material: str | None = None
    subtype: str | None = None
    color_name: str | None = None
    rgba: str | None = Field(None, pattern=r"^[0-9A-Fa-f]{8}$")
    extra_colors: str | None = None
    effect_type: str | None = None
    brand: str | None = None

    @field_validator("extra_colors")
    @classmethod
    def _validate_extra_colors(cls, v: str | None) -> str | None:
        return normalize_extra_colors(v)

    @field_validator("effect_type")
    @classmethod
    def _validate_effect_type(cls, v: str | None) -> str | None:
        return normalize_effect_type(v)

    label_weight: int | None = None
    core_weight: int | None = None
    core_weight_catalog_id: int | None = None
    weight_used: float | None = None
    slicer_filament: str | None = None
    slicer_filament_name: str | None = None
    nozzle_temp_min: int | None = None
    nozzle_temp_max: int | None = None
    note: str | None = None
    tag_uid: str | None = None
    tray_uuid: str | None = None
    data_origin: str | None = None
    tag_type: str | None = None
    cost_per_kg: float | None = Field(default=None, ge=0)
    weight_locked: bool | None = None
    # User-defined category + per-spool low-stock threshold override (#729).
    category: str | None = Field(default=None, max_length=50)
    low_stock_threshold_pct: int | None = Field(default=None, ge=1, le=99)


class SpoolKProfileBase(BaseModel):
    printer_id: int
    extruder: int = 0
    nozzle_diameter: str = "0.4"
    nozzle_type: str | None = None
    k_value: float
    name: str | None = None
    cali_idx: int | None = None
    setting_id: str | None = None


class SpoolKProfileResponse(SpoolKProfileBase):
    id: int
    spool_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class SpoolResponse(SpoolBase):
    id: int
    # rgba is intentionally unconstrained on the response side: the write paths
    # (SpoolCreate, SpoolUpdate) enforce the 8-char hex pattern, but legacy rows
    # or data sourced from AMS firmware / backups may carry malformed values.
    # A single bad row must not 500 the entire inventory list endpoint (#1055).
    rgba: str | None = None
    added_full: bool | None = None
    last_used: datetime | None = None
    encode_time: datetime | None = None
    tag_uid: str | None = None
    tray_uuid: str | None = None
    data_origin: str | None = None
    tag_type: str | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    k_profiles: list[SpoolKProfileResponse] = []

    class Config:
        from_attributes = True


class SpoolAssignmentCreate(BaseModel):
    spool_id: int
    printer_id: int
    ams_id: int
    tray_id: int


class SpoolAssignmentResponse(BaseModel):
    id: int
    spool_id: int
    printer_id: int
    printer_name: str | None = None
    ams_id: int
    tray_id: int
    fingerprint_color: str | None = None
    fingerprint_type: str | None = None
    created_at: datetime
    spool: SpoolResponse | None = None
    configured: bool = False
    ams_label: str | None = None  # User-defined friendly name for the AMS unit

    class Config:
        from_attributes = True
