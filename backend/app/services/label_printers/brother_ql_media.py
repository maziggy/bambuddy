"""Continuous tapes of the Brother QL-800 family (#3612). Pure data, so the
label renderer can size a roll label without importing the driver.

Values are from Brother's "Raster Command Reference QL-800/810W/820NWB" and
agree with brother_ql's tables. All dots are at 300 dpi. The print head has 720
pins; a tape narrower than the head sits ``left_margin_pins`` pins into the
raster line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TapeId = Literal["12", "29", "38", "50", "54", "62"]

HEAD_PINS = 720
BYTES_PER_LINE = HEAD_PINS // 8  # 90
DPI = 300

# ESC i z media type byte: continuous length tape.
MEDIA_TYPE_CONTINUOUS = 0x0A
# Feed margin ("ESC i d") in dots for continuous tape: 3 mm before and after.
FEED_MARGIN_DOTS = 35

# The QL-800 family: same 720-pin head and raster protocol, 300 dpi.
MODELS: tuple[str, ...] = ("QL-800", "QL-810W", "QL-820NWB")
# The QL-800 takes no TIFF (PackBits) compression (brother_ql models table).
MODELS_WITHOUT_COMPRESSION = frozenset({"QL-800"})


@dataclass(frozen=True)
class TapeSpec:
    tape_mm: int
    printable_dots: int
    left_margin_pins: int

    @property
    def media_width_byte(self) -> int:
        """Media width byte of ``ESC i z``: the tape width in mm."""
        return self.tape_mm

    @property
    def printable_width_mm(self) -> float:
        return self.printable_dots / DPI * 25.4


TAPES: dict[str, TapeSpec] = {
    "12": TapeSpec(12, 106, 29),
    "29": TapeSpec(29, 306, 6),
    "38": TapeSpec(38, 413, 12),
    "50": TapeSpec(50, 554, 12),
    "54": TapeSpec(54, 590, 0),
    "62": TapeSpec(62, 696, 12),
}


def tape_spec(tape: str) -> TapeSpec:
    try:
        return TAPES[tape]
    except KeyError:
        raise ValueError(f"Unknown tape: {tape!r}") from None


def printable_width_mm(tape: str) -> float:
    return tape_spec(tape).printable_width_mm
