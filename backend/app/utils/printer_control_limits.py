"""Per-model limits for live printer temperature and fan controls.

Values follow Bambu Lab hardware specifications (bed/nozzle maximums) and
which fans exist on each product line. Unknown models receive conservative
defaults; internal MQTT model codes are normalized via ``printer_models``.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.utils.printer_models import normalize_printer_model, normalize_printer_model_id

# Models with active chamber heating (aligned with printer_manager.CHAMBER_TEMP_SUPPORTED_MODELS)
_CHAMBER_HEAT_MODELS = frozenset(
    {
        "X1",
        "X1C",
        "X1E",
        "X2D",
        "P2S",
        "H2C",
        "H2D",
        "H2DPRO",
        "H2S",
        "BLP001",
        "C13",
        "N6",
        "O1D",
        "O1C",
        "O1C2",
        "O1S",
        "O1E",
        "O2D",
        "N7",
    }
)

# Fan indices match M106 P parameter used in bambu_mqtt.set_fan_speed
FAN_PART = 1
FAN_AUX = 2
FAN_CHAMBER = 3

_DEFAULT_BED_MAX = 100
_DEFAULT_NOZZLE_MAX = 300
_DEFAULT_CHAMBER_MAX = 0


@dataclass(frozen=True)
class PrinterControlLimits:
    """Validated ranges and capabilities for a printer model."""

    bed_min: int
    bed_max: int
    nozzle_min: int
    nozzle_max: int
    chamber_min: int
    chamber_max: int
    fans: frozenset[int]
    dual_nozzle: bool

    def as_dict(self) -> dict:
        return {
            "bed_min": self.bed_min,
            "bed_max": self.bed_max,
            "nozzle_min": self.nozzle_min,
            "nozzle_max": self.nozzle_max,
            "chamber_min": self.chamber_min,
            "chamber_max": self.chamber_max,
            "fans": sorted(self.fans),
            "dual_nozzle": self.dual_nozzle,
        }


def _norm(model: str | None) -> str:
    if not model:
        return ""
    short = normalize_printer_model_id(model) or normalize_printer_model(model) or model
    return short.strip().upper().replace(" ", "").replace("-", "")


def _supports_chamber_heat(model: str | None) -> bool:
    if not model:
        return False
    return _norm(model) in _CHAMBER_HEAT_MODELS


# Explicit limits keyed by normalized short name or internal code.
# Sources: Bambu Lab product pages / compare tool (2025–2026).
_MODEL_LIMITS: dict[str, tuple[int, int, int, frozenset[int], bool]] = {
    # bed_max, nozzle_max, chamber_max, fans, dual_nozzle
    "A1": (100, 300, 0, frozenset({FAN_PART}), False),
    "A1MINI": (100, 300, 0, frozenset({FAN_PART}), False),
    "N1": (100, 300, 0, frozenset({FAN_PART}), False),
    "N2S": (100, 300, 0, frozenset({FAN_PART}), False),
    "A11": (100, 300, 0, frozenset({FAN_PART}), False),
    "A12": (100, 300, 0, frozenset({FAN_PART}), False),
    "A04": (100, 300, 0, frozenset({FAN_PART}), False),
    "P1P": (100, 300, 0, frozenset({FAN_PART}), False),
    "C11": (100, 300, 0, frozenset({FAN_PART}), False),
    "P1S": (100, 300, 0, frozenset({FAN_PART, FAN_AUX}), False),
    "C12": (100, 300, 0, frozenset({FAN_PART, FAN_AUX}), False),
    "X1": (120, 300, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False),
    "X1C": (120, 300, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False),
    "BLP001": (120, 300, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False),
    "X1E": (120, 320, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False),
    "C13": (120, 320, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False),
    "P2S": (120, 300, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False),
    "N7": (120, 300, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False),
    "X2D": (120, 300, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False),
    "N6": (120, 300, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False),
    "H2D": (120, 350, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), True),
    "H2DPRO": (120, 350, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), True),
    "H2C": (120, 350, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), True),
    "H2S": (120, 350, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False),
    "O1D": (120, 350, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), True),
    "O1E": (120, 350, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), True),
    "O2D": (120, 350, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), True),
    "O1C": (120, 350, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), True),
    "O1C2": (120, 350, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), True),
    "O1S": (120, 350, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False),
}

_ENCLOSED_DEFAULT = (120, 300, 65, frozenset({FAN_PART, FAN_AUX, FAN_CHAMBER}), False)


def get_printer_control_limits(
    model: str | None,
    *,
    nozzle_count: int | None = None,
) -> PrinterControlLimits:
    """Return temperature/fan limits for a printer model."""
    key = _norm(model)
    row = _MODEL_LIMITS.get(key)
    if row is None and key and _supports_chamber_heat(model):
        row = _ENCLOSED_DEFAULT
    if row is None:
        row = (_DEFAULT_BED_MAX, _DEFAULT_NOZZLE_MAX, _DEFAULT_CHAMBER_MAX, frozenset({FAN_PART}), False)

    bed_max, nozzle_max, chamber_max, fans, dual = row
    if nozzle_count == 2:
        dual = True
    if chamber_max > 0 and not _supports_chamber_heat(model):
        chamber_max = 0

    return PrinterControlLimits(
        bed_min=0,
        bed_max=bed_max,
        nozzle_min=0,
        nozzle_max=nozzle_max,
        chamber_min=0,
        chamber_max=chamber_max,
        fans=fans,
        dual_nozzle=dual,
    )


def validate_bed_target(target: int, limits: PrinterControlLimits) -> None:
    if target < limits.bed_min or target > limits.bed_max:
        raise ValueError(f"Bed temperature must be between {limits.bed_min} and {limits.bed_max}°C")


def validate_nozzle_target(target: int, limits: PrinterControlLimits) -> None:
    if target < limits.nozzle_min or target > limits.nozzle_max:
        raise ValueError(f"Nozzle temperature must be between {limits.nozzle_min} and {limits.nozzle_max}°C")


def validate_chamber_target(target: int, limits: PrinterControlLimits) -> None:
    if limits.chamber_max <= 0:
        raise ValueError("Chamber temperature control is not supported on this printer")
    if target < limits.chamber_min or target > limits.chamber_max:
        raise ValueError(f"Chamber temperature must be between {limits.chamber_min} and {limits.chamber_max}°C")


def validate_fan(fan: int, speed_percent: int, limits: PrinterControlLimits) -> None:
    if fan not in limits.fans:
        raise ValueError(f"Fan {fan} is not available on this printer model")
    if speed_percent < 0 or speed_percent > 100:
        raise ValueError("Fan speed must be between 0 and 100%")


def fan_percent_to_pwm(speed_percent: int) -> int:
    return max(0, min(255, round(speed_percent * 255 / 100)))
