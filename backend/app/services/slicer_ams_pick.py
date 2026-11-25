"""The slicer's AMS pick a virtual printer's queue job keeps for its archive (#2700).

``PrintQueueItem.slicer_ams_mapping`` holds it until the job's print creates
the archive, as ``{"mapping": [global tray ids], "printer_id": id}``. The
printer is the one the slicer resolved the trays against: the job can be moved
to another printer before it prints, and tray numbers mean nothing there, so it
is never taken from the job's current printer.
"""

import json


def pack(mapping_json: str | None, printer_id: int | None) -> str | None:
    """The column value for the slicer's ``mapping_json`` resolved on ``printer_id``."""
    if not mapping_json or printer_id is None:
        return None
    return json.dumps({"mapping": json.loads(mapping_json), "printer_id": printer_id})


def unpack(raw: str | None) -> tuple[list[int], int] | None:
    """``(mapping, printer_id)`` from the column, or None when unset or malformed."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    mapping, printer_id = value.get("mapping"), value.get("printer_id")
    if not isinstance(mapping, list) or not mapping or not isinstance(printer_id, int):
        return None
    return mapping, printer_id
