"""What a queue item keeps when its printer changes.

Some of an item's settings were worked out for one printer: its AMS tray
mapping (that printer's trays), "Print Anyway" (an answer to that printer's
filament check) and its nozzle choice (that printer's nozzle rack). On
another printer they mean something else, so they go with the old printer;
the next dispatch works them out again. What the user chose for the print
itself (plate, AMS on or off, filament overrides, print options) stays.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

PRINTER_RESOLVED_DEFAULTS: dict[str, Any] = {
    "ams_mapping": None,
    "skip_filament_check": False,
    "nozzle_mapping": None,
    "nozzle_rack_choice": None,
}


def drop_printer_resolved(item: Any, keep: Iterable[str] = ()) -> None:
    """Reset *item*'s printer-specific settings, except those named in *keep*."""
    kept = set(keep)
    for field, default in PRINTER_RESOLVED_DEFAULTS.items():
        if field not in kept:
            setattr(item, field, default)
