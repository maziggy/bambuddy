"""Printer model normalization utilities.

Converts 3MF printer model names (e.g., "Bambu Lab X1 Carbon") to
normalized short names (e.g., "X1C") that match database storage.
"""

# Map from 3MF printer_model strings to normalized short names
PRINTER_MODEL_MAP = {
    "Bambu Lab X1 Carbon": "X1C",
    "Bambu Lab X1": "X1",
    "Bambu Lab X1E": "X1E",
    "Bambu Lab P1S": "P1S",
    "Bambu Lab P1P": "P1P",
    "Bambu Lab P2S": "P2S",
    "Bambu Lab A1": "A1",
    "Bambu Lab A1 Mini": "A1 Mini",
    "Bambu Lab A1 mini": "A1 Mini",
    # Bambu cloud rolled out a terse model-code rename mid-2026 (#1649);
    # 3MFs prepared with newer cloud presets may carry this short form.
    "Bambu Lab A1M": "A1 Mini",
    "Bambu Lab H2D": "H2D",
    "Bambu Lab H2D Pro": "H2D Pro",
    "Bambu Lab H2C": "H2C",
    "Bambu Lab H2S": "H2S",
    "Bambu Lab X2D": "X2D",
    "Bambu Lab A2L": "A2L",
}

# Map from printer_model_id (internal codes in slice_info.config) to short names
# These are the codes Bambu Studio uses internally
PRINTER_MODEL_ID_MAP = {
    # X1 series
    "C11": "X1C",
    "C12": "X1",
    "C13": "X1E",
    # P1 series
    "P1P": "P1P",
    "P1S": "P1S",
    # P2 series
    "P2S": "P2S",
    # X2 series
    "N6": "X2D",
    # A2 series (A2L is single-FDM + integrated cutter/plotter — single nozzle)
    "N9": "A2L",
    # A1 series
    "A11": "A1",
    "A12": "A1 Mini",
    "N1": "A1 Mini",
    "N2S": "A1",
    "A04": "A1 Mini",
    # H2 series (Office/H series)
    "O1D": "H2D",
    "O1E": "H2D Pro",  # Some devices report O1E
    "O2D": "H2D Pro",  # Some devices report O2D
    "O1C": "H2C",
    "O1C2": "H2C",
    "O1S": "H2S",
}


# Rod/rail type classification for maintenance tasks.
# Carbon rods: X1, P1 series (CoreXY with carbon fiber rods)
# Steel rods: P2S, X2D series (hardened steel linear shafts)
# Linear rails: A1, H2 series (linear rail motion system)
# Values must be uppercase with spaces stripped for normalized comparison.
CARBON_ROD_MODELS = frozenset(
    [
        # Display names (uppercase, no spaces)
        "X1",
        "X1C",
        "X1E",
        "P1P",
        "P1S",
        # Internal codes
        "C11",  # X1C
        "C12",  # X1
        "C13",  # X1E
    ]
)

STEEL_ROD_MODELS = frozenset(
    [
        # Display names (uppercase, no spaces)
        "P2S",
        "X2D",
        # Internal codes
        "N7",  # P2S
        "N6",  # X2D
    ]
)

LINEAR_RAIL_MODELS = frozenset(
    [
        # Display names (uppercase, no spaces)
        "A1",
        "A1MINI",
        "A2L",
        "H2D",
        "H2DPRO",
        "H2C",
        "H2S",
        # Internal codes
        "N1",  # A1 Mini
        "N2S",  # A1
        "N9",  # A2L
        "A04",  # A1 Mini (alternate)
        "A11",  # A1
        "A12",  # A1 Mini
        "O1D",  # H2D
        "O1E",  # H2D Pro
        "O2D",  # H2D Pro (alternate)
        "O1C",  # H2C
        "O1C2",  # H2C (dual nozzle variant)
        "O1S",  # H2S
    ]
)


# Models without any external storage (MicroSD / SD card slot).
# The A1 and A1 Mini ship with internal storage only — there is no
# firmware-side "Store sent files on external storage" toggle and no
# slicer-side equivalent surfaces one. The connection diagnostic's
# external_storage check (printer_diagnostic.py) must skip on these
# models instead of reporting fail from a 0-valued home_flag bit.
NO_EXTERNAL_STORAGE_MODELS = frozenset(
    [
        # Display names (uppercase, no spaces)
        "A1",
        "A1MINI",
        # Internal codes
        "N1",  # A1 Mini
        "N2S",  # A1
        "A04",  # A1 Mini (alternate)
        "A11",  # A1
        "A12",  # A1 Mini
    ]
)


# Models that HAVE a MicroSD slot but expose NO reachable control to enable
# the "Store sent files on external storage" option. The toggle only renders
# in Bambu Studio when the printer publishes the
# `support_save_remote_print_file_to_storage` capability in its live status;
# current P1-series firmware (through 01.10.00.00) never publishes it, and
# the P1S/P1P have no on-printer screen, so `store_to_sdcard` (home_flag bit
# 11) is stuck at False with no way for the user to change it. The
# external_storage diagnostic must therefore skip (not fail) on these models
# — a hard fail would be permanently unresolvable (#2524). If a future
# firmware surfaces the capability, remove the model here and the check
# reactivates. Bambu Lab's own storage-cache wiki lists P1 Series as "Not
# Supported", corroborating this.
NO_REMOTE_STORAGE_TOGGLE_MODELS = frozenset(
    [
        # Display names (uppercase, no spaces)
        "P1S",
        "P1P",
    ]
)


# Models with an ethernet port.
# X1, P1P, A1, A1 Mini do NOT have ethernet.
ETHERNET_MODELS = frozenset(
    [
        # Display names (uppercase, no spaces)
        "X1C",
        "X1E",
        "X2D",
        "P1S",
        "P2S",
        "H2D",
        "H2DPRO",
        "H2C",
        "H2S",
        # Internal codes
        "C11",  # X1C
        "C13",  # X1E
        "N6",  # X2D
        "P1S",  # P1S
        "O1D",  # H2D
        "O1E",  # H2D Pro
        "O2D",  # H2D Pro (alternate)
        "O1C",  # H2C
        "O1C2",  # H2C (dual nozzle variant)
        "O1S",  # H2S
    ]
)


# Dual-nozzle (dual-extruder) printers. Single source of truth for nozzle
# class — consumed by ``BambuMQTTClient.start_print``, the K-profile routes,
# and the re-slice nozzle-class guard (previously an inline model tuple
# duplicated across all three). Re-slicing a model laid out for a single-nozzle
# printer onto one of these — or vice versa — is not yet supported: the source
# 3MF's embedded single-nozzle filament/extruder layout is not a valid
# dual-nozzle project and BambuStudio's multi-extruder validator rejects it.
DUAL_NOZZLE_MODELS = frozenset(
    [
        # Display names (uppercase, no spaces)
        "H2D",
        "H2DPRO",
        "H2C",
        "X2D",
        # Internal codes
        "O1D",  # H2D
        "O1E",  # H2D Pro
        "O2D",  # H2D Pro (alternate)
        "O1C",  # H2C
        "O1C2",  # H2C (dual nozzle variant)
        "N6",  # X2D
    ]
)


def has_ethernet(model: str | None) -> bool:
    """Return True if the printer model has an ethernet port."""
    if not model:
        return False
    normalized = model.strip().upper().replace(" ", "").replace("-", "")
    return normalized in ETHERNET_MODELS


def has_external_storage(model: str | None) -> bool:
    """Return True if the printer model can have a MicroSD / external storage slot.

    Defaults to True when the model is unknown — the diagnostic only flips
    its check on for the explicit no-storage list. New models added to the
    Bambu lineup without a slot must be added to ``NO_EXTERNAL_STORAGE_MODELS``
    or the diagnostic will continue to evaluate ``store_to_sdcard`` against
    a hardware feature the printer doesn't have.
    """
    if not model:
        return True
    normalized = model.strip().upper().replace(" ", "").replace("-", "")
    return normalized not in NO_EXTERNAL_STORAGE_MODELS


def has_remote_storage_toggle(model: str | None) -> bool:
    """Return True if the model exposes a reachable control for the
    "Store sent files on external storage" option.

    False for P1-series (has an SD slot, but no on-printer screen and no
    published `support_save_remote_print_file_to_storage` capability, so the
    Bambu Studio toggle never renders). The external_storage diagnostic uses
    this to skip rather than report an unresolvable fail (#2524). Defaults to
    True for unknown models so the check keeps working on anything not
    explicitly listed.
    """
    if not model:
        return True
    normalized = model.strip().upper().replace(" ", "").replace("-", "")
    return normalized not in NO_REMOTE_STORAGE_TOGGLE_MODELS


def is_dual_nozzle_model(model: str | None) -> bool:
    """Return True if the printer model has two nozzles (H2D family / X2D)."""
    if not model:
        return False
    normalized = model.strip().upper().replace(" ", "").replace("-", "")
    return normalized in DUAL_NOZZLE_MODELS


def get_rod_type(model: str | None) -> str | None:
    """Return the rod/rail type for a printer model.

    Returns:
        "carbon" for X1/P1 series (carbon fiber rods),
        "steel_rod" for P2S/X2D series (hardened steel rods),
        "linear_rail" for A1/H2 series (linear rails),
        None for unknown models.
    """
    if not model:
        return None
    normalized = model.strip().upper().replace(" ", "").replace("-", "")
    if normalized in CARBON_ROD_MODELS:
        return "carbon"
    if normalized in STEEL_ROD_MODELS:
        return "steel_rod"
    if normalized in LINEAR_RAIL_MODELS:
        return "linear_rail"
    return None


# G-code interchange families (#2578). A sliced 3MF may target a different
# model ONLY within its family: same kinematics, build volume and G-code
# dialect. X1/P1 series are the one proven-interchangeable group (256mm
# CoreXY, single nozzle — mixed farms intentionally run X1-sliced jobs on
# P1S/P1P). Everything else is exact-match only; extend deliberately, never
# by assumption — a wrong entry here dispatches G-code onto hardware it was
# not sliced for.
# Short display names only (uppercase, no spaces) — is_gcode_compatible()
# resolves internal codes (C11, O1D, ...) to short names before lookup.
GCODE_COMPAT_FAMILIES = (frozenset(["X1", "X1C", "X1E", "P1P", "P1S"]),)


def is_gcode_compatible(sliced_for_model: str | None, target_model: str | None) -> bool:
    """Return True when G-code sliced for one model may be dispatched to the other.

    Unknown/missing metadata on either side returns True — we can only
    validate what the 3MF declares, and legacy files without
    ``sliced_for_model`` must keep working.
    """
    if not sliced_for_model or not target_model:
        return True

    def _norm(model: str) -> str:
        # Internal codes (e.g. "C11") → short names first, so "C11" vs "X1C"
        # compares equal instead of leaning on family membership.
        resolved = PRINTER_MODEL_ID_MAP.get(model.strip(), model)
        return resolved.strip().upper().replace(" ", "").replace("-", "")

    a = _norm(sliced_for_model)
    b = _norm(target_model)
    if a == b:
        return True
    return any(a in family and b in family for family in GCODE_COMPAT_FAMILIES)


def normalize_printer_model_id(model_id: str | None) -> str | None:
    """Convert printer_model_id (internal code) to normalized short name.

    Args:
        model_id: The printer_model_id from slice_info.config (e.g., "C11", "O1D")

    Returns:
        Normalized short name (e.g., "X1C", "H2D") or the original ID if unknown.
    """
    if not model_id:
        return None

    # Check known mappings
    if model_id in PRINTER_MODEL_ID_MAP:
        return PRINTER_MODEL_ID_MAP[model_id]

    # Return original if unknown (might already be a short name)
    return model_id


def normalize_printer_model(raw_model: str | None) -> str | None:
    """Convert 3MF printer_model to normalized short name.

    Args:
        raw_model: The printer_model string from 3MF metadata
            (e.g., "Bambu Lab X1 Carbon")

    Returns:
        Normalized short name (e.g., "X1C") or None if input is empty.
        Unknown models have "Bambu Lab " prefix stripped.
    """
    if not raw_model:
        return None

    # Check known mappings first
    if raw_model in PRINTER_MODEL_MAP:
        return PRINTER_MODEL_MAP[raw_model]

    # Strip "Bambu Lab " prefix for unknown models
    stripped = raw_model.replace("Bambu Lab ", "").strip()
    return stripped or None
