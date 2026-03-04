"""Service for importing and resolving OrcaSlicer profiles.

Handles:
- Parsing .json, .orca_filament, .zip exports
- Fetching base Bambu profiles from OrcaSlicer GitHub for inheritance resolution
- Caching base profiles in the database with TTL
- Extracting core fields for quick access
"""

import io
import json
import logging
import zipfile
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.local_preset import LocalPreset
from backend.app.models.orca_base_cache import OrcaBaseProfile

logger = logging.getLogger(__name__)

ORCA_BASE_URL = "https://raw.githubusercontent.com/SoftFever/OrcaSlicer/main/resources/profiles/BBL"
CACHE_TTL_DAYS = 7
MAX_INHERITANCE_DEPTH = 10


async def get_cached_base_profile(name: str, db: AsyncSession) -> dict | None:
    """Get a base profile from cache if still fresh."""
    result = await db.execute(select(OrcaBaseProfile).where(OrcaBaseProfile.name == name))
    profile = result.scalar_one_or_none()
    if not profile:
        return None

    # Check TTL
    cutoff = datetime.now(timezone.utc) - timedelta(days=CACHE_TTL_DAYS)
    fetched = profile.fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    if fetched < cutoff:
        return None

    try:
        return json.loads(profile.setting)
    except Exception:
        return None


async def fetch_and_cache_base_profile(name: str, profile_type: str, db: AsyncSession) -> dict | None:
    """Fetch a base profile from OrcaSlicer GitHub and cache it."""
    # Check cache first
    cached = await get_cached_base_profile(name, db)
    if cached is not None:
        return cached

    # Map profile_type to GitHub subdirectory
    type_dirs = {
        "filament": "filament",
        "machine": "machine",
        "printer": "machine",
        "process": "process",
    }
    subdir = type_dirs.get(profile_type, "filament")

    # Try fetching from GitHub
    urls_to_try = [
        f"{ORCA_BASE_URL}/{subdir}/{name}.json",
    ]
    # Also try filament dir as fallback for any type
    if subdir != "filament":
        urls_to_try.append(f"{ORCA_BASE_URL}/filament/{name}.json")

    data = None
    async with httpx.AsyncClient(timeout=15.0) as client:
        for url in urls_to_try:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    break
            except Exception as e:
                logger.debug("Failed to fetch %s: %s", url, e)

    if data is None:
        logger.warning("Could not fetch base profile '%s' from GitHub", name)
        return None

    # Cache in DB
    setting_json = json.dumps(data)
    result = await db.execute(select(OrcaBaseProfile).where(OrcaBaseProfile.name == name))
    existing = result.scalar_one_or_none()
    if existing:
        existing.setting = setting_json
        existing.profile_type = profile_type
        existing.fetched_at = datetime.now(timezone.utc)
    else:
        cache_entry = OrcaBaseProfile(
            name=name,
            profile_type=profile_type,
            setting=setting_json,
            fetched_at=datetime.now(timezone.utc),
        )
        db.add(cache_entry)

    return data


async def resolve_preset(preset_data: dict, profile_type: str, db: AsyncSession, depth: int = 0) -> dict:
    """Recursively resolve inheritance chain, merging parent into child.

    OrcaSlicer uses shallow merge: child keys fully replace parent keys.
    """
    if depth >= MAX_INHERITANCE_DEPTH:
        logger.warning("Inheritance depth limit reached for preset")
        return preset_data

    inherits = preset_data.get("inherits")
    if not inherits:
        return preset_data

    # Fetch the base profile
    base = await fetch_and_cache_base_profile(inherits, profile_type, db)
    if base is None:
        logger.warning("Cannot resolve inherits='%s' — base profile not found", inherits)
        return preset_data

    # Recursively resolve the base first
    resolved_base = await resolve_preset(base, profile_type, db, depth + 1)

    # Shallow merge: start with base, override with child
    merged = {**resolved_base, **preset_data}
    return merged


def extract_core_fields(data: dict) -> dict:
    """Extract commonly needed fields from a resolved preset for quick access."""
    fields: dict = {}

    # filament_type — often a single-element array like ["PLA"]
    ft = data.get("filament_type")
    if isinstance(ft, list) and ft:
        fields["filament_type"] = str(ft[0])
    elif isinstance(ft, str):
        fields["filament_type"] = ft

    # filament_vendor
    fv = data.get("filament_vendor")
    if isinstance(fv, list) and fv:
        fields["filament_vendor"] = str(fv[0])
    elif isinstance(fv, str):
        fields["filament_vendor"] = fv

    # nozzle_temp_min / max — from nozzle_temperature array or range fields
    nozzle_temp = data.get("nozzle_temperature")
    if isinstance(nozzle_temp, list) and nozzle_temp:
        try:
            temps = [int(t) for t in nozzle_temp if str(t).isdigit()]
            if temps:
                fields["nozzle_temp_min"] = min(temps)
                fields["nozzle_temp_max"] = max(temps)
        except (ValueError, TypeError):
            pass

    # Override with explicit range fields if present
    range_low = data.get("nozzle_temperature_range_low")
    range_high = data.get("nozzle_temperature_range_high")
    if isinstance(range_low, list) and range_low:
        try:
            fields["nozzle_temp_min"] = int(range_low[0])
        except (ValueError, TypeError):
            pass
    if isinstance(range_high, list) and range_high:
        try:
            fields["nozzle_temp_max"] = int(range_high[0])
        except (ValueError, TypeError):
            pass

    # pressure_advance — store as JSON string if it's an array
    pa = data.get("pressure_advance")
    if pa is not None:
        fields["pressure_advance"] = json.dumps(pa) if isinstance(pa, list) else str(pa)

    # default_filament_colour
    colour = data.get("default_filament_colour")
    if colour is not None:
        fields["default_filament_colour"] = json.dumps(colour) if isinstance(colour, list) else str(colour)

    # filament_cost
    cost = data.get("filament_cost")
    if isinstance(cost, list) and cost:
        fields["filament_cost"] = str(cost[0])
    elif cost is not None:
        fields["filament_cost"] = str(cost)

    # filament_density
    density = data.get("filament_density")
    if isinstance(density, list) and density:
        fields["filament_density"] = str(density[0])
    elif density is not None:
        fields["filament_density"] = str(density)

    # compatible_printers
    compat = data.get("compatible_printers")
    if isinstance(compat, list):
        fields["compatible_printers"] = json.dumps(compat)

    return fields


MATERIAL_TYPES = [
    "PLA",
    "ABS",
    "ASA",
    "PETG",
    "TPU",
    "PA",
    "PC",
    "PVA",
    "HIPS",
    "PET",
    "PP",
    "PEI",
    "PEEK",
    "PCTG",
    "PPA",
    "POM",
]


def _parse_material_from_name(name: str) -> str | None:
    """Extract filament material type from preset name, e.g. 'Overture PLA Matte' -> 'PLA'."""
    import re

    upper = name.upper()
    for mat in MATERIAL_TYPES:
        if re.search(rf"\b{mat}\b", upper):
            return mat
    return None


def _parse_vendor_from_name(name: str) -> str | None:
    """Extract vendor from preset name, e.g. 'Overture PLA Matte @BBL X1C' -> 'Overture'."""
    import re

    # Strip @printer suffix
    clean = re.sub(r"@.+$", "", name).strip()
    upper = clean.upper()
    for mat in MATERIAL_TYPES:
        idx = upper.find(mat)
        if idx > 0:
            vendor = clean[:idx].strip()
            if vendor and len(vendor) > 1:
                return vendor
    return None


def _type_from_path(zip_entry: str) -> str | None:
    """Infer profile type from the ZIP directory path."""
    parts = zip_entry.lower().replace("\\", "/").split("/")
    for part in parts:
        if part in ("filament",):
            return "filament"
        if part in ("machine", "printer"):
            return "printer"
        if part in ("process", "print"):
            return "process"
    return None


def _guess_profile_type(data: dict, path_hint: str | None = None) -> str:
    """Determine the profile type from JSON data and optional ZIP path hint."""
    import re

    # 1. Explicit "type" field set by OrcaSlicer
    explicit = data.get("type", "").lower()
    if explicit in ("filament",):
        return "filament"
    if explicit in ("machine", "printer"):
        return "printer"
    if explicit in ("process", "print"):
        return "process"

    # 2. ZIP directory path hint (e.g. "filament/MyPreset.json")
    if path_hint:
        from_path = _type_from_path(path_hint)
        if from_path:
            return from_path

    # 3. Strong ID-based heuristics — *_settings_id is definitive
    if "print_settings_id" in data:
        return "process"
    if "filament_settings_id" in data:
        return "filament"
    if "printer_settings_id" in data:
        return "printer"

    # 4. Content-based heuristics — check process BEFORE filament because
    #    resolved process presets can inherit filament_type from their base
    process_keys = {
        "layer_height",
        "first_layer_height",
        "wall_loops",
        "prime_tower_width",
        "prime_tower_max_speed",
        "prime_tower_rib_wall",
        "outer_wall_speed",
        "inner_wall_speed",
        "interlocking_depth",
        "bottom_shell_layers",
        "top_shell_layers",
        "sparse_infill_density",
    }
    if process_keys & data.keys():
        return "process"
    if "machine_max_speed_x" in data or "printer_model" in data or "bed_shape" in data:
        return "printer"
    if "filament_type" in data or "filament_vendor" in data:
        return "filament"

    # 5. Name-based heuristics as last resort
    name = data.get("name", "")
    if re.search(r"\d+\.\d+mm\s", name):
        return "process"
    if name.lower().endswith("process"):
        return "process"

    return "filament"


async def import_orca_file(filename: str, content: bytes, db: AsyncSession) -> dict:
    """Import presets from a file (.json, .orca_filament, .bbscfg, .bbsflmt, .zip).

    Returns dict with keys: success, imported, skipped, errors.
    """
    imported = 0
    skipped = 0
    errors: list[str] = []

    # Determine file type
    lower_name = filename.lower()

    if lower_name.endswith(".json"):
        # Single JSON preset
        try:
            data = json.loads(content)
            result = await _import_single_preset(data, db, path_hint=filename)
            if result == "imported":
                imported += 1
            elif result == "skipped":
                skipped += 1
            else:
                errors.append(result)
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON: {e}")
    elif lower_name.endswith((".orca_filament", ".zip", ".bbscfg", ".bbsflmt")):
        # ZIP archive — extract and parse each JSON
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for entry in zf.namelist():
                    if entry.endswith(".json") and "bundle_structure" not in entry:
                        try:
                            raw = zf.read(entry)
                            data = json.loads(raw)
                            result = await _import_single_preset(data, db, path_hint=entry)
                            if result == "imported":
                                imported += 1
                            elif result == "skipped":
                                skipped += 1
                            else:
                                errors.append(f"{entry}: {result}")
                        except json.JSONDecodeError:
                            errors.append(f"{entry}: Invalid JSON")
                        except Exception as e:
                            errors.append(f"{entry}: {e}")
        except zipfile.BadZipFile:
            errors.append("Invalid ZIP/orca_filament archive")
    else:
        errors.append(f"Unsupported file type: {filename}")

    return {
        "success": imported > 0 or (imported == 0 and skipped > 0 and not errors),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
    }


async def _import_single_preset(data: dict, db: AsyncSession, path_hint: str | None = None) -> str:
    """Import a single preset dict. Returns 'imported', 'skipped', or error string."""
    name = data.get("name")
    if not name:
        return "Preset has no name"

    # Check for duplicate by name
    result = await db.execute(select(LocalPreset).where(LocalPreset.name == name))
    if result.scalar_one_or_none():
        return "skipped"

    profile_type = _guess_profile_type(data, path_hint)
    inherits_value = data.get("inherits")

    # Resolve inheritance
    try:
        resolved = await resolve_preset(data, profile_type, db)
    except Exception as e:
        logger.warning("Failed to resolve inheritance for '%s': %s", name, e)
        resolved = data

    # Extract core fields
    core = extract_core_fields(resolved)

    # Fallback: parse material/vendor from preset name if not found in data
    filament_type = core.get("filament_type") or _parse_material_from_name(name)
    filament_vendor = core.get("filament_vendor") or _parse_vendor_from_name(name)

    preset = LocalPreset(
        name=name,
        preset_type=profile_type,
        source="orcaslicer",
        filament_type=filament_type,
        filament_vendor=filament_vendor,
        nozzle_temp_min=core.get("nozzle_temp_min"),
        nozzle_temp_max=core.get("nozzle_temp_max"),
        pressure_advance=core.get("pressure_advance"),
        default_filament_colour=core.get("default_filament_colour"),
        filament_cost=core.get("filament_cost"),
        filament_density=core.get("filament_density"),
        compatible_printers=core.get("compatible_printers"),
        setting=json.dumps(resolved),
        inherits=inherits_value,
        version=data.get("version"),
    )
    db.add(preset)
    return "imported"


async def refresh_base_cache(db: AsyncSession) -> dict:
    """Force refresh all cached base profiles."""
    result = await db.execute(select(OrcaBaseProfile))
    profiles = result.scalars().all()

    refreshed = 0
    failed = 0

    for profile in profiles:
        # Clear fetched_at to force re-fetch
        try:
            profile.fetched_at = datetime.min
            data = await fetch_and_cache_base_profile(profile.name, profile.profile_type, db)
            if data:
                refreshed += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    return {"refreshed": refreshed, "failed": failed, "total": len(profiles)}


async def get_cache_status(db: AsyncSession) -> dict:
    """Get the status of the base profile cache."""
    result = await db.execute(select(OrcaBaseProfile))
    profiles = result.scalars().all()

    cutoff = datetime.now(timezone.utc) - timedelta(days=CACHE_TTL_DAYS)
    fresh = 0
    stale = 0

    for p in profiles:
        fetched = p.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if fetched >= cutoff:
            fresh += 1
        else:
            stale += 1

    return {
        "total": len(profiles),
        "fresh": fresh,
        "stale": stale,
        "ttl_days": CACHE_TTL_DAYS,
    }


async def reclassify_presets(db: AsyncSession) -> dict:
    """Re-evaluate preset_type for all local presets using the improved heuristic."""
    result = await db.execute(select(LocalPreset))
    presets = result.scalars().all()

    reclassified = 0
    for preset in presets:
        try:
            data = json.loads(preset.setting)
        except Exception:
            continue

        new_type = _guess_profile_type(data)
        if new_type != preset.preset_type:
            logger.info(
                "Reclassifying '%s' from '%s' to '%s'",
                preset.name,
                preset.preset_type,
                new_type,
            )
            preset.preset_type = new_type
            reclassified += 1

    return {"total": len(presets), "reclassified": reclassified}
