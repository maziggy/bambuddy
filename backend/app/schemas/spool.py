import re
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


def normalize_barcode(value: str | None) -> str | None:
    """Canonicalize a scanned or manually-typed barcode or SKU/article number.

    Purely numeric input is treated as a GTIN: digits only, leading zeros
    stripped, so a UPC-A and its EAN-13 leading-zero form store identically
    and match on a later scan.

    Input containing any letter is instead treated as a manufacturer
    SKU/article number (e.g. a Code 128 "inventory barcode" with no UPC/EAN
    counterpart) and only trimmed + uppercased — digit-stripping an
    alphanumeric code would otherwise mangle it into near-nothing (e.g.
    "ALZMNTABS01" -> "1").
    """
    if value is None:
        return None
    if any(ch.isalpha() for ch in value):
        stripped = value.strip()
        return stripped.upper() or None
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    return digits.lstrip("0") or "0"


# GTIN-8/12/13/14 are the only standard checksummed lengths. The floor below
# the max (rather than requiring an exact match) accounts for leading zeros
# already stripped by normalize_barcode — see classify_code.
_GTIN_LENGTHS = (8, 12, 13, 14)
_MIN_GTIN_LENGTH = 7
_MAX_GTIN_LENGTH = max(_GTIN_LENGTHS)


def _gtin_checksum_valid(digits: str) -> bool:
    payload, check = digits[:-1], int(digits[-1])
    total = 0
    for i, ch in enumerate(reversed(payload)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - (total % 10)) % 10 == check


# Modern Amazon ASINs: exactly "B0" + 8 alphanumerics. Legacy ISBN-10 ASINs
# (books) are all-digit and 10 long — not a GTIN length, so they'd classify
# as "sku", which is fine: no filament carries one.
_ASIN_RE = re.compile(r"^B0[A-Z0-9]{8}$")


# AIM symbology families (sent by scanners configured to transmit AIM IDs —
# see spoolbuddy/daemon/barcode_reader.py) whose symbols carry true GTINs.
# EAN/UPC symbols encode nothing else, and ITF-14 case codes wrap a GTIN.
_GTIN_SYMBOLOGIES = ("ean-upc", "itf")

# 2D symbologies never carry product codes on filament boxes (the Bambu spool
# QR is a URL) — the scan endpoint drops these outright.
IGNORED_SYMBOLOGIES = ("qr", "datamatrix")

# URL-shaped scan payloads. The optional colon matters: the daemon's HID
# keymap historically couldn't type ":", so the Bambu QR arrived as
# "HTTPS//E.BAMBULAB.COM/…".
_URL_PAYLOAD_RE = re.compile(r"(?i)^\s*(https?:?//|www\.)")


def looks_like_url_payload(raw: str | None) -> bool:
    """True when a scanned payload is a URL (a QR code), not a product code.

    Mirrors the daemon-side filter (spoolbuddy/daemon/barcode_reader.py) so
    older daemons — or any other caller of the scan endpoint — get the same
    behavior: URL scans are ignored, never surfaced as a no-match modal.
    """
    if not raw:
        return False
    return bool(_URL_PAYLOAD_RE.match(raw)) or "://" in raw


def classify_code(raw: str | None, symbology: str | None = None) -> tuple[str, str]:
    """Canonicalize `raw` exactly like `normalize_barcode`, then classify it
    down the ladder: ("gtin", digits) → ("asin", B0-shaped) → ("sku",
    stripped-upper). "sku" is a *candidate*: whether it is a real
    manufacturer SKU or some other box code (lot number, FNSKU) can only be
    decided by external knowledge — a community-DB hit or a user link — not
    by the string itself.

    `symbology` is an optional scan-time hint (AIM symbology family from the
    hardware scanner). When present and NOT a GTIN-carrying symbology, the
    GTIN rung is skipped entirely: a Code 128 label wrapping a lot number
    that happens to pass the mod-10 checksum must not land in `gtin_code`.
    A GTIN-carrying symbology changes nothing — the checksum still gates the
    rung — and None (no scanner hint: manual entry, camera scan, older
    daemon) keeps the pure-heuristic behavior.

    Classification runs on the *canonicalized* value, not the raw input, so
    a freshly-scanned barcode and that same barcode already stored on a spool
    (which went through `normalize_barcode` at write time, stripping leading
    zeros) always classify identically — without this, a UPC-A like
    "036000291452" classifies as gtin when scanned (checksum-checked on the
    raw 12 digits) but as sku when re-classified from the stored,
    already-stripped 11-digit form, so a repeat scan of the user's own
    barcode never matches its own inventory row.

    This works because the GTIN mod-10 checksum is invariant to leading-zero
    padding: weights are assigned right-to-left starting at the check digit,
    so a leading zero always lands in a weight-agnostic position and
    contributes 0 to the checksum sum no matter how many digits precede it.
    Padding the canonical (already zero-stripped) form back out to a full
    GTIN length and checksum-checking there therefore gives the exact same
    answer checking the original, un-stripped value would have.
    """
    canonical = normalize_barcode(raw) or ""
    if (
        (symbology is None or symbology in _GTIN_SYMBOLOGIES)
        and canonical.isdigit()
        and _MIN_GTIN_LENGTH <= len(canonical) <= _MAX_GTIN_LENGTH
        and _gtin_checksum_valid(canonical.zfill(_MAX_GTIN_LENGTH))
    ):
        return canonical, "gtin"
    if _ASIN_RE.match(canonical):
        return canonical, "asin"
    return canonical, "sku"


def normalize_gtin_code(value: str | None) -> str | None:
    """Validate + canonicalize a value for the GTIN-only `gtin_code` column.

    Only true retail barcodes belong there — anything that doesn't pass the
    GTIN structure/checksum is rejected rather than silently reclassified.
    """
    if value is None or not value.strip():
        return None
    canonical, kind = classify_code(value)
    if kind != "gtin":
        raise ValueError("gtin_code must be a valid GTIN (EAN/UPC with check digit)")
    return canonical


def normalize_asin_code(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().upper()
    if not stripped:
        return None
    if not re.fullmatch(r"[A-Z0-9]{10}", stripped):
        raise ValueError("asin_code must be a 10-character Amazon ASIN")
    return stripped


def normalize_sku_code(value: str | None) -> str | None:
    # Trim + uppercase only — no GTIN digit-canonicalization; a numeric SKU
    # like "17600" must store byte-for-byte as printed.
    if value is None:
        return None
    stripped = value.strip().upper()
    return stripped or None


def normalize_other_code(value: str | None) -> str | None:
    # User-owned code space (self-printed barcodes welcome): trim only. The
    # app imposes no case/format/uniqueness semantics here, and these values
    # are never submitted to a community database.
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


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
    # Anchor for the resettable "Total Consumed" display. The Inventory
    # page shows `weight_used - weight_used_baseline`; the per-spool /
    # bulk "Reset usage to 0" action sets baseline = weight_used so the
    # counter zeroes without touching remaining (#1390).
    weight_used_baseline: float = 0
    slicer_filament: str | None = None
    slicer_filament_name: str | None = None
    nozzle_temp_min: int | None = None
    nozzle_temp_max: int | None = None
    note: str | None = None
    tag_uid: str | None = None
    tray_uuid: str | None = None
    data_origin: str | None = None
    tag_type: str | None = None
    # Typed code columns — see the classification ladder in classify_code.
    # gtin_code holds true retail barcodes ONLY; sku_code the manufacturer
    # article number; asin_code an Amazon ASIN; other_code is the user's own
    # code space (no semantics imposed, never submitted upstream).
    gtin_code: str | None = Field(default=None, max_length=64)
    asin_code: str | None = Field(default=None, max_length=16)
    sku_code: str | None = Field(default=None, max_length=64)
    other_code: str | None = Field(default=None, max_length=64)
    # How the roll was purchased (refill coil vs boxed with a spool). Not
    # "is_refill": a refill eventually gets mounted on a spool — this flag
    # records purchase form, not current physical state (core_weight does).
    bought_as_refill: bool = False
    cost_per_kg: float | None = Field(default=None, ge=0)
    weight_locked: bool = False
    last_scale_weight: int | None = None
    last_weighed_at: datetime | None = None
    # User-defined category + per-spool low-stock threshold override (#729).
    category: str | None = Field(default=None, max_length=50)
    low_stock_threshold_pct: int | None = Field(default=None, ge=1, le=99)
    # Free-text storage location, distinct from `location` (AMS slot
    # assignment). Column has lived on the ORM since the inventory rework
    # but was missing from this schema, so writes were silently dropped (#1291).
    storage_location: str | None = Field(default=None, max_length=255)
    location_id: int | None = Field(default=None, gt=0)


class SpoolCreate(SpoolBase):
    # Write-only: the raw code a scanner read (either symbology). The create
    # route classifies it down the ladder (GTIN / ASIN / SKU-candidate),
    # stores it in the matching *_code column when that column wasn't set
    # explicitly, and cross-fills the sibling columns from the community DBs
    # under the size-consistency rule. Explicit *_code fields always win.
    scanned_code: str | None = Field(default=None, max_length=64)
    # Write-only companion to scanned_code: the AIM symbology family the
    # scanner reported at scan time (e.g. "ean-upc", "code128"). Routing uses
    # it to keep the scan-time classification — without it, a Code 128 numeric
    # the scan demoted could re-promote to gtin_code here.
    scanned_symbology: str | None = Field(default=None, max_length=16)

    # The code validators live on the WRITE schemas (here and SpoolUpdate),
    # not SpoolBase — SpoolResponse inherits SpoolBase, and a legacy/
    # hand-edited row that fails validation must still read back instead of
    # 500ing the inventory list (same escape hatch as rgba, #1055).
    @field_validator("gtin_code")
    @classmethod
    def _validate_gtin_code(cls, v: str | None) -> str | None:
        return normalize_gtin_code(v)

    @field_validator("asin_code")
    @classmethod
    def _validate_asin_code(cls, v: str | None) -> str | None:
        return normalize_asin_code(v)

    @field_validator("sku_code")
    @classmethod
    def _validate_sku_code(cls, v: str | None) -> str | None:
        return normalize_sku_code(v)

    @field_validator("other_code")
    @classmethod
    def _validate_other_code(cls, v: str | None) -> str | None:
        return normalize_other_code(v)


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

    @field_validator("gtin_code")
    @classmethod
    def _validate_gtin_code(cls, v: str | None) -> str | None:
        return normalize_gtin_code(v)

    @field_validator("asin_code")
    @classmethod
    def _validate_asin_code(cls, v: str | None) -> str | None:
        return normalize_asin_code(v)

    @field_validator("sku_code")
    @classmethod
    def _validate_sku_code(cls, v: str | None) -> str | None:
        return normalize_sku_code(v)

    @field_validator("other_code")
    @classmethod
    def _validate_other_code(cls, v: str | None) -> str | None:
        return normalize_other_code(v)

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
    gtin_code: str | None = Field(default=None, max_length=64)
    asin_code: str | None = Field(default=None, max_length=16)
    sku_code: str | None = Field(default=None, max_length=64)
    other_code: str | None = Field(default=None, max_length=64)
    bought_as_refill: bool | None = None
    cost_per_kg: float | None = Field(default=None, ge=0)
    weight_locked: bool | None = None
    # User-defined category + per-spool low-stock threshold override (#729).
    category: str | None = Field(default=None, max_length=50)
    low_stock_threshold_pct: int | None = Field(default=None, ge=1, le=99)
    storage_location: str | None = Field(default=None, max_length=255)
    location_id: int | None = Field(default=None, gt=0)


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


class SpoolFilamentPresetBase(BaseModel):
    """One per-printer-model slicer preset override for a spool.

    ``nozzle_diameter`` defaults to "" meaning "any nozzle of this model". The
    spool form always sends a concrete size; the empty form is for API clients
    that want one value to cover a model. Lengths match the columns, which are wider than
    ``Spool.slicer_filament`` so a preset id that fits the Spoolman write
    schema cannot truncate on the way in.
    """

    printer_model: str = Field(..., min_length=1, max_length=50)
    nozzle_diameter: str = Field(default="", max_length=10)
    slicer_filament: str | None = Field(default=None, max_length=128)
    slicer_filament_name: str | None = Field(default=None, max_length=255)


class SpoolFilamentPresetResponse(SpoolFilamentPresetBase):
    id: int
    spool_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class LinkedCode(BaseModel):
    """One sibling code (GTIN barcode or manufacturer SKU/article number)
    discovered for the same physical product as the primary scanned/entered
    code — e.g. another package-size GTIN, the refill-pack GTIN, or the
    manufacturer SKU. Read-only display data; excludes the primary code
    itself. See `resolve_barcode` in `services/barcode_resolver.py`."""

    code: str
    kind: str  # "gtin" | "sku" (community data files ASINs under sku)
    is_refill: bool = False

    # from_attributes on the nested model itself, not just on its parents:
    # pydantic v2 does NOT cascade a parent's from_attributes into nested
    # BaseModel fields when model_validate() is called directly (FastAPI's
    # response_model path passes it explicitly, which masks the gap). Without
    # this, any route that builds its response object by hand 500s the first
    # time a spool actually has sibling codes.
    class Config:
        from_attributes = True


class SpoolResponse(SpoolBase):
    id: int
    # rgba is intentionally unconstrained on the response side: the write paths
    # (SpoolCreate, SpoolUpdate) enforce the 8-char hex pattern, but legacy rows
    # or data sourced from AMS firmware / backups may carry malformed values.
    # A single bad row must not 500 the entire inventory list endpoint (#1055).
    rgba: str | None = None
    # Same rationale as rgba: the write paths validate/cap the code columns,
    # but SQLite doesn't enforce VARCHAR length or our format rules, so a
    # legacy/hand-edited row must still read back without 500ing the list.
    gtin_code: str | None = None
    asin_code: str | None = None
    sku_code: str | None = None
    other_code: str | None = None
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


class BarcodeLookupResponse(BaseModel):
    """Result of resolving a scanned/entered barcode or SKU to filament fields.

    ``source`` tells the frontend how much to trust the prefilled fields:
    a hit against the user's own inventory is exact, an OFD/SpoolmanDB-Community
    hit is community-sourced.
    """

    enabled: bool = True
    matched: bool = False
    source: str | None = None  # "inventory" | "ofd" | "spoolmandb-community" | None
    barcode: str
    material: str | None = None
    brand: str | None = None
    subtype: str | None = None
    color_name: str | None = None
    rgba: str | None = None
    label_weight: int | None = None
    nozzle_temp_min: int | None = None
    nozzle_temp_max: int | None = None
    # Whether the *scanned* code itself is a no-spool refill (per the community
    # DBs' eans_refill / spool_refill). Lets the SpoolBuddy flow auto-arm its
    # refill toggle instead of making the user remember to flip it for a known
    # refill box.
    is_refill: bool = False
    linked_codes: list[LinkedCode] = []


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
    pending_config: bool = False  # True when slot was empty at assign time; will configure on insert
    ams_label: str | None = None  # User-defined friendly name for the AMS unit

    class Config:
        from_attributes = True
