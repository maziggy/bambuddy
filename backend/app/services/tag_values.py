"""Checking and normalising the values a filament tag gives (#3657).

Tags come from many writers, so every value is checked before it becomes a
spool field: text trimmed and cut to the column size, colours as RRGGBBAA,
numbers from JSON numbers or numeric strings, out-of-range values dropped.
Shared by the tag format decoders (``opentag3d.py``, ``openspool.py``).
"""

import json
import math
import re

_HEX_COLOUR = re.compile(r"[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?")

# Spool column sizes; the colour name is cut to Spoolman's 64 so Quick Add in
# Spoolman mode doesn't fail on it.
MAX_MATERIAL = 50
MAX_SUBTYPE = 50
MAX_BRAND = 100
MAX_COLOR_NAME = 64
MAX_EXTRA_COLORS = 8


class TagDecodeError(ValueError):
    """A record in a known format that holds nothing usable; the message says why."""


def json_object(payload: bytes) -> dict:
    """A JSON record's object. Raises TagDecodeError when it doesn't parse or isn't an object."""
    try:
        doc = json.loads(payload.decode("utf-8-sig"))  # some writers add a BOM
    except (UnicodeDecodeError, ValueError) as exc:
        raise TagDecodeError("JSON record does not parse") from exc
    if not isinstance(doc, dict):
        raise TagDecodeError("JSON record is not an object")
    return doc


def text(value: object, limit: int) -> str | None:
    """Printable text, trimmed and cut to *limit*; control characters (NUL padding included) are dropped."""
    if not isinstance(value, str):
        return None
    cleaned = "".join(c for c in value if c.isprintable()).strip()[:limit].strip()
    return cleaned or None


def for_log(value: object, limit: int = 60) -> str:
    """Tag content as it may appear in a log line: escaped, on one line, and short."""
    shown = repr(value)
    return shown if len(shown) <= limit else shown[: limit - 3] + "..."


def rgba_from_bytes(raw: bytes) -> str | None:
    """RRGGBBAA, or None for an unset (all-zero) colour."""
    return raw.hex().upper() if any(raw) else None


def rgba_from_hex(value: object) -> str | None:
    """RRGGBBAA from 6 or 8 hex digits, with or without '#'."""
    if not isinstance(value, str):
        return None
    digits = value.strip().lstrip("#")
    if not _HEX_COLOUR.fullmatch(digits):
        return None
    if len(digits) == 6:
        digits += "FF"
    return digits.upper()


def extra_colors(colours: list[str | None]) -> str | None:
    """Gradient stops as Spool.extra_colors stores them: lowercase, comma-separated, at most 8."""
    stops = [c.lower() for c in colours if c][:MAX_EXTRA_COLORS]
    return ",".join(stops) or None


def number(value: object) -> float | None:
    """A number from a JSON number or a numeric string (OpenSpool stores strings)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            result = float(value)
        except OverflowError:  # a JSON integer too big for a float
            return None
    elif isinstance(value, str):
        if "_" in value:  # float() reads "1_000" as 1000
            return None
        try:
            result = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def temperature(value: float | None) -> int | None:
    """Whole degrees C, 1-500, or None."""
    if value is None:
        return None
    rounded = round(value)
    return rounded if 1 <= rounded <= 500 else None


def weight(value: float | None) -> int | None:
    """Whole grams, 1-65535, or None."""
    if value is None:
        return None
    rounded = round(value)
    return rounded if 1 <= rounded <= 65535 else None


def diameter(value: float | None) -> float | None:
    if value is None or not 1.0 <= value <= 3.5:
        return None
    return round(value, 2)
