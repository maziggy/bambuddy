"""Spread a multi-colour slice across both nozzles of a dual-nozzle printer.

Without a filament map the slicer CLI puts every filament on extruder 1, so a
two-colour job on an H2D or X2D swaps filament at every colour change while
the other nozzle stays cold. Measured on one plate, giving the second colour
to the second nozzle took the print from 2h29m29s / 76.91 g to 1h54m9s /
70.02 g.

The CLI honours ``filament_map`` only from its ``--filament-map`` flag (with
``--filament-map-mode Manual``). The same value in the process profile or the
3MF is written into the output and ignored, and the automatic modes still
answer "1 1" when the second extruder's filament is not declared to them. So
the map is computed here and sent to the sidecar as ``filamentMap``.

The map has to match what is physically loaded, or the print comes out in the
wrong colours. It is therefore derived from the spools the printer reports,
matched on material type as well as colour, and every doubt returns ``None``,
which leaves the slice exactly as it was before this module existed.
"""

import json
import logging
from collections.abc import Callable

from backend.app.utils.filament_types import canonical_filament_type, known_printer_filament_type

logger = logging.getLogger(__name__)

# The slicer numbers extruders 1..N in ``filament_map``; the printer reports
# MQTT extruder ids (0 = right/main, 1 = left/deputy). A machine profile states
# the translation in ``physical_extruder_map``: entry ``i`` is the MQTT id of
# slicer extruder ``i + 1``. Every Bambu dual-nozzle profile without a nozzle
# rack inherits ``["1", "0"]`` from ``fdm_bbl_3dp_002_common``. A leaf preset
# whose ``inherits`` chain the sidecar resolves does not carry the key itself,
# so that inherited value is what is used when it is missing.
DEFAULT_PHYSICAL_EXTRUDER_MAP = ("1", "0")

# The extruder every filament prints from when no map is given ("1 1").
HOME_EXTRUDER = 1
# The one filament moved off it goes here.
AUX_EXTRUDER = 2


def _rgb(value: object) -> str | None:
    """``#RRGGBB`` / ``#RRGGBBAA`` / ``RRGGBB`` as upper-case ``RRGGBB``.

    A filament profile stores ``filament_colour`` as a one-element list while
    the printer reports a bare string; both arrive here. The alpha byte is
    dropped so an opaque AMS colour still matches the profile's 6-digit one.
    """
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if not isinstance(value, str):
        return None
    hex_part = value.strip().lstrip("#").upper()
    if len(hex_part) < 6:
        return None
    return hex_part[:6]


def _material(profile: dict) -> str | None:
    """The canonical material type a filament profile prints, or ``None``.

    A full profile states it in ``filament_type`` (a one-element list, like
    ``filament_colour``). A standard preset arrives as an ``inherits`` stub
    that the sidecar expands later, so only its name is here; Bambu's preset
    names carry the material ("Bambu PETG HF @BBL X2D"), and a name that does
    not reduce to a known type gives ``None`` rather than a guess.
    """
    declared = profile.get("filament_type")
    if isinstance(declared, (list, tuple)):
        declared = declared[0] if declared else None
    if isinstance(declared, str) and declared.strip():
        return canonical_filament_type(declared.strip())
    name = profile.get("name") or profile.get("inherits")
    if isinstance(name, str):
        reduced = known_printer_filament_type(name)
        if reduced is not None:
            return canonical_filament_type(reduced)
    return None


def _spool_material(spool: dict) -> str | None:
    value = spool.get("type")
    if not isinstance(value, str) or not value.strip():
        return None
    return canonical_filament_type(value.strip())


def _profile(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def plan_filament_map(
    printer_json: str,
    filament_jsons: list[str],
    loaded_spools: list[dict],
    used_slots: set[int] | None = None,
) -> list[int] | None:
    """The ``filament_map`` for this slice, or ``None`` to leave it unchanged.

    ``filament_jsons`` are the filament profiles in plate-slot order, already
    carrying the colour being printed. ``loaded_spools`` is what
    ``loaded_filaments`` returns for the target printers: one entry per loaded
    spool with ``type``, ``color`` and ``extruder_id``. ``used_slots`` are the
    1-based slots the plate prints with, when the source says so; the slice
    route fills the other slots with a stand-in profile, and those are never
    moved. ``None`` means every slot prints.

    At most one filament is moved to the second extruder. On the printers this
    was measured on, the AMS feeds one extruder and the other is fed only by
    the external spool holder, so the second nozzle has exactly one filament
    to offer, and it must be the material and colour actually on that spool.
    """
    if len(filament_jsons) < 2:
        return None  # single colour: nothing to spread

    printer = _profile(printer_json)
    if not printer:
        return None  # not a profile this can read
    nozzles = printer.get("nozzle_diameter")
    if isinstance(nozzles, list) and len(nozzles) < 2:
        return None  # the profile says one nozzle

    physical = printer.get("physical_extruder_map") or list(DEFAULT_PHYSICAL_EXTRUDER_MAP)
    if not isinstance(physical, list) or len(physical) != 2:
        return None
    try:
        aux_mqtt_id = int(physical[AUX_EXTRUDER - 1])
    except (TypeError, ValueError):
        return None

    # The (material, colour) pairs the second extruder has loaded right now.
    aux_filaments = {
        (material, rgb)
        for spool in loaded_spools
        if isinstance(spool, dict) and spool.get("extruder_id") == aux_mqtt_id
        for material, rgb in [(_spool_material(spool), _rgb(spool.get("color")))]
        if material is not None and rgb is not None
    }
    if not aux_filaments:
        return None  # nothing loaded on the second nozzle

    profiles = [_profile(raw) for raw in filament_jsons]
    wanted = [(_material(profile), _rgb(profile.get("filament_colour"))) for profile in profiles]

    printing = [i for i in range(len(wanted)) if used_slots is None or (i + 1) in used_slots]
    if len({wanted[i] for i in printing}) < 2:
        return None  # the plate prints one filament
    candidates = [i for i in printing if wanted[i] in aux_filaments]
    if not candidates:
        return None  # the second nozzle has none of these filaments

    # Prefer a filament other than the first: filament 1 is conventionally the
    # base, and Bambu advise against running a print's main material through
    # the second extruder. When only the first matches it is still moved,
    # because one nozzle per colour beats a filament swap at every layer.
    pick = next((i for i in candidates if i > 0), candidates[0])
    return [AUX_EXTRUDER if i == pick else HOME_EXTRUDER for i in range(len(wanted))]


async def auto_filament_map(
    db,
    *,
    target_model: str | None,
    printer_json: str,
    filament_jsons: list[str],
    allows: Callable[[int], bool],
    used_slots: set[int] | None = None,
) -> list[int] | None:
    """Look up what the target printers have loaded and plan a map from it.

    ``allows`` says which printers the slice may plan from: the user's printer
    scope, narrowed to one printer when the caller knows it. A map read from a
    printer the user cannot see would send them a file that waits for filament
    on their own printer.

    Never raises. Any failure (an unknown model, an unreachable printer, a
    profile that does not parse) is logged and answered with ``None``, so the
    slice goes ahead exactly as it would have without this.
    """
    from backend.app.utils.printer_models import is_dual_nozzle_model, is_nozzle_rack_model

    if target_model is None or len(filament_jsons) < 2 or not is_dual_nozzle_model(target_model):
        return None
    # A rack machine (H2C) numbers its hotends by rack position, not by
    # extruder, so the two-extruder reasoning here does not describe it.
    if is_nozzle_rack_model(target_model):
        return None

    try:
        from backend.app.services.loaded_filaments import loaded_filaments

        loaded = await loaded_filaments(db, target_model, allows=allows)
        mapping = plan_filament_map(printer_json, filament_jsons, list(loaded or []), used_slots)
    except Exception as exc:
        logger.warning("Filament map skipped, slicing with the default grouping: %s", exc)
        return None

    if mapping:
        logger.info("Dual-nozzle %s: filament_map=%s from the loaded spools", target_model, mapping)
    return mapping
