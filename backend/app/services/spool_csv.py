"""CSV import/export for the spool inventory (#1576).

One module owns the round-trip: the same fixed column schema is used to
serialise existing spools out and to parse + validate a user-supplied CSV
back in. Validation reuses the `SpoolCreate` Pydantic model so the CSV path
and the form path share a single source of truth — anything the form rejects,
the import rejects too, with the same rules.

The import flow is two-phase by design: `parse_and_validate()` never writes.
The route calls it once for the dry-run preview (so the user sees per-row
valid/error/skipped before committing) and again on confirm, then persists
only the rows that came back `valid`.
"""

import csv
import io
from datetime import datetime

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.models.spool import Spool
from backend.app.schemas.spool import SpoolCreate

# Fixed CSV header, in output order. Round-trips cleanly: export writes these
# columns, import expects them. `material` is the only required field; the rest
# are optional. Keep aligned with the SpoolCreate fields referenced below.
#
# `remaining` is a derived, export-only column (= label_weight - weight_used).
# It's written out for human readability and round-trip clarity, but ignored on
# import — `weight_used` is the source of truth, and accepting both would let
# them contradict. `last_used` is a timestamp the model carries but SpoolCreate
# does not, so import applies it to the ORM object directly (see persist path).
CSV_COLUMNS = [
    "material",
    "brand",
    "subtype",
    "color_name",
    "rgba",
    "extra_colors",
    "effect_type",
    "label_weight",
    "weight_used",
    "remaining",
    "cost_per_kg",
    "nozzle_temp_min",
    "nozzle_temp_max",
    "last_used",
    "note",
]

# Upload ceiling for the import endpoint. A spool inventory CSV is a few KB
# even with thousands of rows; 5 MB is a generous cap that still refuses an
# OOM-sized body before it's read into memory.
MAX_CSV_IMPORT_BYTES = 5 * 1024 * 1024

# Spreadsheet formula-injection guard. A cell whose first character is one of
# these is treated as a formula by Excel / LibreOffice / Sheets; we prefix it
# with a single quote on export so the value renders as literal text.
_FORMULA_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

# Columns whose CSV cell must be coerced to a number before SpoolCreate sees it.
# DictReader hands us strings; SpoolCreate wants int/float. Empty cell → omit
# the field (falls back to the schema default / None).
_INT_COLUMNS = {"label_weight", "nozzle_temp_min", "nozzle_temp_max"}
_FLOAT_COLUMNS = {"cost_per_kg", "weight_used"}


class ImportRowResult(BaseModel):
    """Per-row outcome of a parse+validate pass.

    `spool` carries the validated, SpoolCreate-shaped dict for `valid` rows so
    the route can persist without re-parsing. `resolved_color` flags rows whose
    rgba/extra_colors/effect_type were filled in from the Color Catalog rather
    than supplied in the CSV — surfaced in the preview so the user knows a
    colour was inferred.
    """

    row_number: int  # 1-based data row (header is not counted)
    status: str  # "valid" | "error" | "skipped"
    reason: str | None = None
    material: str | None = None
    brand: str | None = None
    color_name: str | None = None
    rgba: str | None = None
    resolved_color: bool = False
    # True when the colour was resolved from a catalog entry of a DIFFERENT
    # material (no exact material match existed). Surfaced so the preview can
    # warn the user the colour came from another material's variant.
    cross_material_color: bool = False
    spool: dict | None = None


class ImportPreview(BaseModel):
    """Result of a dry-run (or the pre-write pass of a real import)."""

    columns: list[str]
    total: int
    valid_count: int
    error_count: int
    skipped_count: int
    rows: list[ImportRowResult]
    warnings: list[str] = []


class ImportResult(BaseModel):
    """Summary returned after a real (non-dry-run) import."""

    created: int
    skipped: int
    errors: int
    error_rows: list[ImportRowResult] = []


def _normalize_header(name: str) -> str:
    """Map a CSV header cell to a canonical field name.

    Case- and space-tolerant: "Color Name", "color-name", " COLOR_NAME "
    all collapse to "color_name".
    """
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_rgba(value: str) -> str | None:
    """Coerce a user-supplied colour cell to 8-char RRGGBBAA hex, or None.

    Accepts an optional leading `#` and a 6-char RRGGBB form (alpha defaults to
    `ff`). Returns None if the value isn't valid hex of length 6 or 8 — the
    caller turns that into a row error so it isn't silently dropped.
    """
    raw = value.strip().lstrip("#")
    if len(raw) not in (6, 8):
        return None
    try:
        int(raw, 16)
    except ValueError:
        return None
    if len(raw) == 6:
        raw += "ff"
    return raw.lower()


def _parse_datetime(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, or None if it isn't valid.

    Accepts what `datetime.isoformat()` produces (what export writes) plus a
    trailing 'Z' for UTC, which `fromisoformat` rejects before Python 3.11.
    """
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


async def _load_color_catalog(db: AsyncSession) -> list[ColorCatalogEntry]:
    """Load the whole Color Catalog once so per-row resolution is in-memory.

    A CSV can hold hundreds of rows; resolving each with its own SELECT would
    be an N+1 against a small, rarely-changing table. We pull it once here and
    let `_resolve_color` match against the list.
    """
    result = await db.execute(select(ColorCatalogEntry))
    return list(result.scalars().all())


def _resolve_color(
    catalog: list[ColorCatalogEntry], brand: str | None, color_name: str | None, material: str | None
) -> tuple[str, str | None, str | None, bool] | None:
    """Match brand + color_name against the preloaded catalog (case-insensitive).

    Returns (rgba, extra_colors, effect_type, cross_material) on a match, else
    None. Prefers an entry whose material matches; if none does it falls back to
    another material's entry and sets cross_material=True so the caller can warn
    that the colour came from a different material's variant.
    """
    if not brand or not color_name:
        return None

    brand_l = brand.strip().lower()
    name_l = color_name.strip().lower()
    material_l = material.strip().lower() if material else None

    matches = [
        entry
        for entry in catalog
        if entry.hex_color and entry.manufacturer.lower() == brand_l and entry.color_name.lower() == name_l
    ]
    if not matches:
        return None

    exact = next((e for e in matches if material_l and e.material and e.material.lower() == material_l), None)
    row = exact if exact is not None else matches[0]
    cross_material = exact is None

    rgba = _normalize_rgba(row.hex_color)
    if rgba is None:
        return None
    return rgba, row.extra_colors, row.effect_type, cross_material


def _readable_validation_error(exc: ValidationError) -> str:
    """Flatten a Pydantic ValidationError into one short, user-facing line."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "value"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


def _empty_preview(warnings: list[str]) -> ImportPreview:
    """A preview with no rows — used for the early-exit cases (bad/empty file)."""
    return ImportPreview(
        columns=CSV_COLUMNS,
        total=0,
        valid_count=0,
        error_count=0,
        skipped_count=0,
        rows=[],
        warnings=warnings,
    )


async def parse_and_validate(raw_bytes: bytes, db: AsyncSession) -> ImportPreview:
    """Parse a CSV blob, validate + colour-resolve each row. Never writes.

    Decodes UTF-8 (BOM tolerant), reads with DictReader against the fixed
    schema, and classifies each row as valid / error / skipped. Valid rows
    carry a SpoolCreate-shaped `spool` dict ready to persist.
    """
    warnings: list[str] = []

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _empty_preview(["File is not valid UTF-8 text."])

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return _empty_preview(["CSV is empty."])

    norm_header = [_normalize_header(h) for h in header]
    known = set(CSV_COLUMNS)
    unknown = [h for h in norm_header if h and h not in known]
    if unknown:
        warnings.append(f"Ignoring unknown columns: {', '.join(unknown)}")
    # Map canonical field name → column index in this file (first occurrence).
    col_index: dict[str, int] = {}
    for idx, h in enumerate(norm_header):
        if h in known and h not in col_index:
            col_index[h] = idx

    if "material" not in col_index:
        return _empty_preview(warnings + ["Required column 'material' is missing from the header."])

    # Pull the catalog once; per-row colour resolution matches against this
    # list in memory instead of issuing a SELECT per row.
    catalog = await _load_color_catalog(db)

    def cell(row: list[str], field: str) -> str:
        idx = col_index.get(field)
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    rows: list[ImportRowResult] = []
    valid = error = skipped = 0

    for row_number, raw_row in enumerate(reader, start=1):
        # Fully blank row (no non-empty cell) → skip silently.
        if not any(c.strip() for c in raw_row):
            rows.append(ImportRowResult(row_number=row_number, status="skipped", reason="Empty row"))
            skipped += 1
            continue

        material = cell(raw_row, "material")
        brand = cell(raw_row, "brand") or None
        color_name = cell(raw_row, "color_name") or None

        if not material:
            rows.append(
                ImportRowResult(
                    row_number=row_number,
                    status="error",
                    reason="material is required",
                    brand=brand,
                    color_name=color_name,
                )
            )
            error += 1
            continue

        data: dict = {"material": material}
        if brand:
            data["brand"] = brand
        if color_name:
            data["color_name"] = color_name

        row_error: str | None = None

        # Plain text passthrough columns.
        for field in ("subtype", "effect_type", "extra_colors", "note"):
            value = cell(raw_row, field)
            if value:
                data[field] = value

        # Numeric columns: parse only if present, else leave to schema defaults.
        for field in _INT_COLUMNS:
            value = cell(raw_row, field)
            if value:
                try:
                    data[field] = int(value)
                except ValueError:
                    row_error = f"{field} must be a whole number (got '{value}')"
                    break
        if row_error is None:
            for field in _FLOAT_COLUMNS:
                value = cell(raw_row, field)
                if value:
                    try:
                        data[field] = float(value)
                    except ValueError:
                        row_error = f"{field} must be a number (got '{value}')"
                        break

        # Bounds check: weight_used must be within [0, label_weight]. The schema
        # accepts any float, so a negative or over-full value would otherwise be
        # imported silently. label_weight falls back to the schema default when
        # the CSV omits it.
        if row_error is None and "weight_used" in data:
            used = data["weight_used"]
            label = data.get("label_weight", 1000)
            if used < 0:
                row_error = f"weight_used cannot be negative (got {used})"
            elif used > label:
                row_error = f"weight_used ({used}) exceeds label_weight ({label})"

        # `last_used` is an ORM-only timestamp (not on SpoolCreate); parse it
        # here and apply it to the validated dict after the SpoolCreate gate.
        last_used: datetime | None = None
        if row_error is None:
            last_used_cell = cell(raw_row, "last_used")
            if last_used_cell:
                last_used = _parse_datetime(last_used_cell)
                if last_used is None:
                    row_error = f"last_used must be an ISO date/time (got '{last_used_cell}')"

        resolved_color = False
        cross_material_color = False
        if row_error is None:
            # Colour precedence: explicit rgba wins; else resolve brand+name
            # from the catalog; else leave blank.
            rgba_cell = cell(raw_row, "rgba")
            if rgba_cell:
                normalized = _normalize_rgba(rgba_cell)
                if normalized is None:
                    row_error = f"rgba must be 6- or 8-char hex (got '{rgba_cell}')"
                else:
                    data["rgba"] = normalized
            else:
                resolved = _resolve_color(catalog, brand, color_name, material)
                if resolved is not None:
                    rgba_val, extra_val, effect_val, cross_material_color = resolved
                    data["rgba"] = rgba_val
                    # CSV-supplied extra_colors/effect_type take precedence over
                    # the catalog's; only fill from catalog when absent.
                    if extra_val and "extra_colors" not in data:
                        data["extra_colors"] = extra_val
                    if effect_val and "effect_type" not in data:
                        data["effect_type"] = effect_val
                    resolved_color = True

        if row_error is not None:
            rows.append(
                ImportRowResult(
                    row_number=row_number,
                    status="error",
                    reason=row_error,
                    material=material,
                    brand=brand,
                    color_name=color_name,
                )
            )
            error += 1
            continue

        # Final gate: SpoolCreate runs the same validators the form uses
        # (rgba pattern, extra_colors/effect_type normalisation, bounds).
        try:
            spool = SpoolCreate(**data)
        except ValidationError as exc:
            rows.append(
                ImportRowResult(
                    row_number=row_number,
                    status="error",
                    reason=_readable_validation_error(exc),
                    material=material,
                    brand=brand,
                    color_name=color_name,
                )
            )
            error += 1
            continue

        spool_data = spool.model_dump()
        if last_used is not None:
            # last_used isn't a SpoolCreate field; graft it onto the persisted
            # dict so the ORM object carries it.
            spool_data["last_used"] = last_used

        rows.append(
            ImportRowResult(
                row_number=row_number,
                status="valid",
                material=material,
                brand=brand,
                color_name=color_name,
                rgba=spool.rgba,
                resolved_color=resolved_color,
                cross_material_color=cross_material_color,
                spool=spool_data,
            )
        )
        valid += 1

    return ImportPreview(
        columns=CSV_COLUMNS,
        total=valid + error + skipped,
        valid_count=valid,
        error_count=error,
        skipped_count=skipped,
        rows=rows,
        warnings=warnings,
    )


def serialize(spools: list[Spool]) -> bytes:
    """Render spools to CSV bytes using the fixed schema (export side).

    rgba is written without a leading `#`, matching the import-side
    normalisation, so export → import round-trips without transformation.
    `remaining` is derived (label_weight - weight_used) and `last_used` is
    written as ISO-8601; empty/None fields become empty cells.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    for spool in spools:
        writer.writerow([_sanitize_cell(_cell_value(spool, col)) for col in CSV_COLUMNS])
    return output.getvalue().encode("utf-8")


def _sanitize_cell(value: str) -> str:
    """Neutralise spreadsheet formula injection.

    A free-text field (note, color_name) starting with =, +, -, @, tab, or CR
    is evaluated as a formula by Excel/Sheets/LibreOffice when the CSV is
    opened. Prefixing with a single quote forces it to render as literal text.
    """
    if value and value[0] in _FORMULA_INJECTION_PREFIXES:
        return "'" + value
    return value


def _cell_value(spool: Spool, col: str) -> str:
    """Render one spool field for export. Handles the derived `remaining`
    column and ISO-formats `last_used`; everything else is str() of the value."""
    if col == "remaining":
        # Derived for display: label_weight - weight_used, clamped at 0.
        return str(max(0, round((spool.label_weight or 0) - (spool.weight_used or 0))))
    value = getattr(spool, col, None)
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    # Whole-number floats (weight_used, cost_per_kg) export as ints — "300",
    # not "300.0" — for a cleaner, human-friendly CSV. import re-parses fine.
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
