"""Carry a 3MF designer's own process tweaks across a re-slice (#2622).

A MakerWorld model is often published with deliberate deviations from the stock
Bambu process preset — 5 walls, 100% infill, a 0.1mm first layer. Re-slicing that
file for a different printer used to drop every one of them: ``--load-settings``
is authoritative, so the picked process preset wins over the 3MF's embedded
``Metadata/project_settings.config``.

We do not have to *compute* what the designer changed. BambuStudio already did,
and wrote the answer into the file:

    different_settings_to_system = [
        "enable_support;inner_wall_speed;sparse_infill_density;...",   # [0]  process
        "filament_change_length;filament_prime_volume",                # [1..N] filaments
        "machine_start_gcode;bed_custom_model;...",                    # [-1] printer
    ]

The array is ``1 + len(filament_settings_id) + 1`` long — verified against real
files at 2, 3 and 4 filament slots. Index 0 is exactly the set of process keys
that differ from the system preset, which is the reporter's step 1 for free: no
baseline resolution, no shipping BBL profiles into Bambuddy, and no new endpoint
on the slicer sidecar (which exposes bundled presets by name only, with no way to
flatten one).

Delivery is the mechanism ``_patch_process_support_settings`` already proved in
#1881: write the values into the process JSON that goes out as ``--load-settings``.
For a "standard" preset pick that JSON is a ``{inherits: …}`` stub, so the keys we
write are the *child* in the inherits chain and win over the flattened parent.

Not every key is safe to carry, though. Real files put ``inner_wall_speed``,
``outer_wall_speed`` and ``prime_tower_max_speed`` in that list — values tuned for
the designer's machine that can be plain wrong, or out of range, on the target.
Those are classified :data:`PRINTER_COUPLED` and offered unticked; the caller
decides. Nothing is applied that the caller did not ask for by name.
"""

from __future__ import annotations

import json
import logging
import zipfile
from io import BytesIO
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

_PROJECT_SETTINGS = "Metadata/project_settings.config"


class DesignOverride(NamedTuple):
    """One process setting the designer changed away from the system preset."""

    key: str
    value: Any
    printer_coupled: bool
    # Set for the handful of keys that *define* the picked process preset —
    # see :data:`_PRESET_DEFINING`. Offered like printer-coupled ones, never
    # pre-selected, because the user's preset pick has to win over the file.
    preset_defining: bool = False


# Process keys whose sane value depends on the machine, not on the design intent.
# The designer picked these for *their* printer's kinematics, chamber and hotend;
# carrying them onto another model risks a slice that is merely slower/uglier —
# or a hard range-validation reject from the CLI, which is how the very first
# slicer spike died. Offered, but never pre-selected.
#
# Matching is by exact key OR by suffix/substring rule below, because Bambu's
# process schema has dozens of per-feature speed keys and an exhaustive literal
# list would rot on every slicer release.
_PRINTER_COUPLED_EXACT: frozenset[str] = frozenset(
    {
        "default_acceleration",
        "independent_support_layer_height",
        "precise_z_height",
        "travel_acceleration",
        "enable_wrapping_detection",
    }
)

# Substring rules for the families that are always machine-coupled. Kept
# deliberately narrow: "speed", "acceleration"/"accel" and "jerk" are the
# kinematic families, "fan"/"temperature" follow the hotend and chamber, and
# "prime_tower" follows the target's toolchange hardware.
_PRINTER_COUPLED_SUBSTRINGS: tuple[str, ...] = (
    # Prime-tower geometry (and whether there is one at all) follows the target's
    # extruder count and bed, not the design — a real file carries five of these.
    "prime_tower",
    "_speed",
    "speed_",
    "acceleration",
    "_accel",
    "jerk",
    "fan_speed",
    "_temperature",
    "temperature_",
)


# Process keys whose value *is* the preset the user picked. "0.08mm High
# Quality" is not a name with a layer height attached — the layer height is
# what the preset is, and the same holds for the first layer it starts on.
#
# Carrying these from the file would quietly undo an explicit pick: choose the
# 0.08 preset for a MakerWorld file whose designer moved layer height to 0.2
# and, with every non-printer-coupled key pre-selected, the slice comes out at
# 0.2 while the dropdown still reads 0.08. The designer's value stays on offer
# — a re-slice that genuinely wants the design's layer height is one tick away
# — but nothing here is applied without the user saying so.
_PRESET_DEFINING: frozenset[str] = frozenset(
    {
        "layer_height",
        "initial_layer_print_height",
    }
)


def is_preset_defining(key: str) -> bool:
    """Whether this key is the identity of the picked process preset."""
    return key in _PRESET_DEFINING


def is_printer_coupled(key: str) -> bool:
    """Whether carrying this process key across printer models is risky."""
    if key in _PRINTER_COUPLED_EXACT:
        return True
    lowered = key.lower()
    return any(token in lowered for token in _PRINTER_COUPLED_SUBSTRINGS)


def _split_changed_keys(entry: Any) -> list[str]:
    """Parse one ``different_settings_to_system`` entry into its key names."""
    if not isinstance(entry, str):
        return []
    return [part.strip() for part in entry.split(";") if part.strip()]


def extract_design_process_overrides(zip_bytes: bytes) -> list[DesignOverride]:
    """Process settings the 3MF's designer changed away from the system preset.

    Returns an empty list for anything that is not a BambuStudio-style 3MF
    carrying both ``project_settings.config`` and a well-formed
    ``different_settings_to_system`` — including OrcaSlicer files and older
    exports that predate the field. Callers treat empty as "nothing to offer",
    which is the pre-feature behaviour.
    """
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
            if _PROJECT_SETTINGS not in zf.namelist():
                return []
            config = json.loads(zf.read(_PROJECT_SETTINGS).decode("utf-8"))
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, OSError, KeyError):
        return []
    return overrides_from_config(config)


def overrides_from_config(config: Any) -> list[DesignOverride]:
    """``extract_design_process_overrides`` on an already-parsed config dict."""
    if not isinstance(config, dict):
        return []

    changed = config.get("different_settings_to_system")
    if not isinstance(changed, list) or not changed:
        return []

    # Sanity-check the layout before trusting index 0. The array should be
    # [process, *filaments, printer]; a file whose length disagrees with its own
    # filament count is one we do not understand, and guessing there could carry
    # printer G-code into the process slot.
    filaments = config.get("filament_settings_id")
    if isinstance(filaments, list) and len(changed) != len(filaments) + 2:
        logger.debug(
            "3MF different_settings_to_system has %d entries for %d filaments "
            "(expected %d) — skipping design-settings carry-over",
            len(changed),
            len(filaments),
            len(filaments) + 2,
        )
        return []

    overrides: list[DesignOverride] = []
    # Index 0 is the process slot — see the layout in the module docstring. The
    # length check above is what earns the right to index it blindly.
    for key in _split_changed_keys(changed[0]):
        if key not in config:
            # Listed as changed but absent from the flattened config — nothing
            # to carry. Seen with keys the slicer renamed between versions.
            continue
        overrides.append(
            DesignOverride(
                key=key,
                value=config[key],
                printer_coupled=is_printer_coupled(key),
                preset_defining=is_preset_defining(key),
            )
        )

    overrides.sort(key=lambda o: o.key)
    return overrides


def apply_design_overrides(process_json: str, overrides: list[DesignOverride], selected_keys: list[str]) -> str:
    """Write the selected designer values into the outgoing process JSON.

    ``selected_keys`` is authoritative — a key the caller did not name is not
    applied even when it is present in ``overrides``. Returns ``process_json``
    unchanged when nothing is selected or the JSON is unparseable, so a bad
    input degrades to a plain profile slice rather than failing it.
    """
    if not selected_keys or not overrides:
        return process_json

    wanted = set(selected_keys)
    by_key = {o.key: o.value for o in overrides if o.key in wanted}
    if not by_key:
        return process_json

    try:
        process_cfg = json.loads(process_json)
    except json.JSONDecodeError:
        return process_json
    if not isinstance(process_cfg, dict):
        return process_json

    process_cfg.update(by_key)
    logger.info("Carrying %d design setting(s) onto the picked process preset: %s", len(by_key), sorted(by_key))
    return json.dumps(process_cfg)
