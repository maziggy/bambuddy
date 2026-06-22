"""Printer model normalization utilities.

Converts 3MF printer model names (e.g., "Bambu Lab X1 Carbon") to
normalized short names (e.g., "X1C") that match database storage.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FullCalibrationProfile:
    """Verified native full-calibration command profile for one model family."""

    model_family: str
    option: int


@dataclass(frozen=True)
class CalibrationStage:
    """One verified native calibration stage exposed by Bambu Studio."""

    code: str
    option: int


# Stable stage identifiers cross the REST boundary. The backend maps them to
# Bambu Studio's native calibration option bits instead of accepting a raw mask.
CALIBRATION_STAGES: tuple[CalibrationStage, ...] = (
    CalibrationStage("bed_leveling", 2),
    CalibrationStage("vibration_compensation", 4),
    CalibrationStage("motor_noise_cancellation", 8),
    CalibrationStage("nozzle_offset", 16),
    CalibrationStage("high_temperature_bed", 32),
    CalibrationStage("nozzle_clump_detection", 64),
)
_CALIBRATION_STAGES_BY_CODE = {stage.code: stage for stage in CALIBRATION_STAGES}


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


# Bambu Studio's Calibration dialog enables every calibration option supported
# by the connected model by default. Its DeviceManager builds the native MQTT
# payload as ``print.command="calibration"`` and ORs the selected flags:
# lidar=1, bed=2, vibration=4, motor-noise=8, nozzle-offset=16,
# high-temperature-bed=32, clump-position=64.
#
# Evidence (pinned upstream source):
# https://github.com/bambulab/BambuStudio/blob/5875ec284a397703edf38eb8ee9a3903ea99a09f/src/slic3r/GUI/DeviceManager.cpp#L1892-L1913
# The model-specific support flags come from the matching files under
# ``resources/printers/`` at that same revision. Unknown profiles intentionally
# return None: sending a generic mask could enable a calibration stage that a
# newer or differently configured printer does not implement.
_FULL_CALIBRATION_PROFILES: dict[str, FullCalibrationProfile] = {
    # X1 family: micro-LiDAR + bed-leveling + vibration compensation.
    "X1": FullCalibrationProfile("X1", 7),
    "X1C": FullCalibrationProfile("X1", 7),
    "X1E": FullCalibrationProfile("X1", 7),
    "BLP001": FullCalibrationProfile("X1", 7),
    "BLP002": FullCalibrationProfile("X1", 7),
    "C13": FullCalibrationProfile("X1", 7),
    # P1 family: bed-leveling + vibration compensation.
    "P1P": FullCalibrationProfile("P1", 6),
    "P1S": FullCalibrationProfile("P1", 6),
    "C11": FullCalibrationProfile("P1", 6),
    "C12": FullCalibrationProfile("P1", 6),
    # A1 family: bed-leveling + vibration compensation + motor-noise tuning.
    "A1": FullCalibrationProfile("A1", 14),
    "A1MINI": FullCalibrationProfile("A1", 14),
    "N1": FullCalibrationProfile("A1", 14),
    "N2S": FullCalibrationProfile("A1", 14),
    "A04": FullCalibrationProfile("A1", 14),
    "A11": FullCalibrationProfile("A1", 14),
    "A12": FullCalibrationProfile("A1", 14),
    # P2S and H2S additionally expose high-temperature-bed and
    # clump-position calibration in current Bambu Studio model data.
    "P2S": FullCalibrationProfile("P2S", 102),
    "N7": FullCalibrationProfile("P2S", 102),
    "H2S": FullCalibrationProfile("H2S", 102),
    "O1S": FullCalibrationProfile("H2S", 102),
    # H2D family: bed-leveling + vibration compensation + nozzle-offset +
    # high-temperature-bed calibration.
    "H2D": FullCalibrationProfile("H2D", 54),
    "H2DPRO": FullCalibrationProfile("H2D", 54),
    "O1D": FullCalibrationProfile("H2D", 54),
    "O1E": FullCalibrationProfile("H2D", 54),
    "O2D": FullCalibrationProfile("H2D", 54),
}


def get_full_calibration_profile(model: str | None) -> FullCalibrationProfile | None:
    """Return the verified native full-calibration profile for ``model``.

    This is deliberately allow-list based. H2C and unknown/future models are
    unavailable until their Bambu Studio command profile is independently
    verified, rather than receiving an assumed H2D-compatible payload.
    """
    if not model:
        return None
    normalized = model.strip().upper().replace(" ", "").replace("-", "")
    return _FULL_CALIBRATION_PROFILES.get(normalized)


def get_supported_calibration_stages(model: str | None) -> tuple[CalibrationStage, ...]:
    """Return verified native calibration stages for ``model``."""
    profile = get_full_calibration_profile(model)
    if profile is None:
        return ()
    return tuple(stage for stage in CALIBRATION_STAGES if profile.option & stage.option)


def get_calibration_option(model: str | None, stages: list[str] | None) -> int:
    """Derive a verified native option bitmask from selected stage codes.

    ``None`` preserves the former full-calibration API behavior. An explicit
    selection must be non-empty, unique, and supported by the model profile.
    """
    profile = get_full_calibration_profile(model)
    if profile is None:
        raise ValueError("Calibration is not verified for this printer model")
    if stages is None:
        return profile.option
    if not stages:
        raise ValueError("Select at least one calibration stage")
    if len(set(stages)) != len(stages):
        raise ValueError("Calibration stages must not be duplicated")

    option = 0
    for code in stages:
        stage = _CALIBRATION_STAGES_BY_CODE.get(code)
        if stage is None or not profile.option & stage.option:
            raise ValueError("Selected calibration stage is not supported by this printer")
        option |= stage.option
    return option


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
