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
and every doubt returns ``None``, which leaves the slice exactly as it was
before this module existed.
"""

import json
import logging

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
) -> list[int] | None:
    """The ``filament_map`` for this slice, or ``None`` to leave it unchanged.

    ``filament_jsons`` are the filament profiles in plate-slot order, already
    carrying the colour being printed. ``loaded_spools`` is what
    ``GET /printers/available-filaments`` returns for the target model: one
    entry per loaded spool with ``color`` and ``extruder_id``.

    At most one filament is moved to the second extruder. On the printers this
    was measured on, the AMS feeds one extruder and the other is fed only by
    the external spool holder, so the second nozzle has exactly one colour to
    offer and it must be the colour actually on that spool.
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

    # The colours the second extruder has loaded right now.
    aux_colours = {
        rgb
        for spool in loaded_spools
        if isinstance(spool, dict) and spool.get("extruder_id") == aux_mqtt_id
        for rgb in [_rgb(spool.get("color"))]
        if rgb is not None
    }
    if not aux_colours:
        return None  # nothing loaded on the second nozzle

    wanted = [_rgb(_profile(raw).get("filament_colour")) for raw in filament_jsons]

    # A slot that is a byte-for-byte copy of an earlier one is the placeholder
    # the slice route writes into slots the plate does not print. It is never
    # the one worth moving, and a plate whose other slots are all such copies
    # prints a single filament.
    distinct = [i for i, raw in enumerate(filament_jsons) if raw not in filament_jsons[:i]]
    if len(distinct) < 2:
        return None
    candidates = [i for i in distinct if wanted[i] in aux_colours]
    if not candidates:
        return None  # the second nozzle has none of these colours

    # Prefer a filament other than the first: filament 1 is conventionally the
    # base, and Bambu advise against running a print's main material through
    # the second extruder. When only the first matches it is still moved,
    # because one nozzle per colour beats a filament swap at every layer.
    pick = next((i for i in candidates if i > 0), candidates[0])
    mapping = [AUX_EXTRUDER if i == pick else HOME_EXTRUDER for i in range(len(wanted))]
    if len(set(mapping)) < 2:
        return None
    return mapping


async def auto_filament_map(
    db,
    *,
    target_model: str | None,
    printer_json: str,
    filament_jsons: list[str],
) -> list[int] | None:
    """Look up what the target printer has loaded and plan a map from it.

    Never raises. Any failure (an unknown model, an unreachable printer, a
    profile that does not parse) is logged and answered with ``None``, so the
    slice goes ahead exactly as it would have without this.
    """
    from backend.app.utils.printer_models import is_dual_nozzle_model, is_nozzle_rack_model

    if len(filament_jsons) < 2 or not is_dual_nozzle_model(target_model):
        return None
    # A rack machine (H2C) numbers its hotends by rack position, not by
    # extruder, so the two-extruder reasoning here does not describe it.
    if is_nozzle_rack_model(target_model):
        return None

    try:
        from backend.app.api.routes.printers import get_available_filaments

        loaded = await get_available_filaments(model=target_model, location=None, _=None, db=db)
        mapping = plan_filament_map(printer_json, filament_jsons, list(loaded or []))
    except Exception as exc:
        logger.warning("Filament map skipped, slicing with the default grouping: %s", exc)
        return None

    if mapping:
        logger.info("Dual-nozzle %s: filament_map=%s from the loaded spools", target_model, mapping)
    return mapping
