"""Sanity-check a sliced file before Bambuddy is willing to print it.

A Bambu printer's start G-code is where the AMS load (``M620``) and the
preparation-stage announcements (``M1002 gcode_claim_action``) live. Slice
without it and the job still dispatches, still heats the bed and still moves
the toolhead — it simply extrudes nothing, reports no stage, and sits at layer
0 until someone notices (#2838).

Nothing downstream can tell that apart from a print that has not started yet,
so the only place to catch it is here, on the bytes the slicer just produced.
All 56 instantiable presets in the shipped Bambu bundle carry
``gcode_claim_action``, which makes its absence a reliable signal rather than
a heuristic.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile

logger = logging.getLogger(__name__)

# Present in the start G-code of every instantiable machine preset in the
# bundle. `M620` is equally universal today, but this one is the marker whose
# absence the reporter could see from the printer's side: no claim actions
# means `stg_cur` stays -1 and the UI never names a preparation step.
_START_GCODE_MARKER = "gcode_claim_action"

_PROJECT_SETTINGS = "Metadata/project_settings.config"

# The start block sits after the file header and the embedded thumbnails, well
# inside this. Bounded so a pathological output cannot turn the check into a
# multi-hundred-megabyte read.
_GCODE_SCAN_BYTES = 4 * 1024 * 1024


def _as_text(value: object) -> str:
    """Slicer config values arrive as a bare string or a one-element list."""
    if isinstance(value, list):
        return "".join(str(v) for v in value)
    return "" if value is None else str(value)


def start_gcode_is_missing(content: bytes, *, export_3mf: bool) -> bool:
    """Whether ``content`` was sliced without the printer's start G-code.

    Answers False whenever the question cannot be settled — an unreadable
    archive, a missing config, a decode failure. A slice that is merely
    unusual must not be blocked by a check that only knows how to recognise
    one specific defect; the caller has no better information than we do.
    """
    if not content:
        return False

    if not export_3mf:
        head = content[:_GCODE_SCAN_BYTES].decode("utf-8", errors="ignore")
        return bool(head) and _START_GCODE_MARKER not in head

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            raw = archive.read(_PROJECT_SETTINGS)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        logger.debug("Slice output check skipped: cannot read %s (%s)", _PROJECT_SETTINGS, exc)
        return False

    try:
        settings = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.debug("Slice output check skipped: %s is not valid JSON (%s)", _PROJECT_SETTINGS, exc)
        return False

    if not isinstance(settings, dict) or "machine_start_gcode" not in settings:
        logger.debug("Slice output check skipped: no machine_start_gcode in %s", _PROJECT_SETTINGS)
        return False

    return _START_GCODE_MARKER not in _as_text(settings["machine_start_gcode"])


def missing_start_gcode_message(printer_preset_name: str) -> str:
    """The 502 body for a slice that came back without its start G-code.

    Names the sidecar because that is where the fix is: Bambuddy sends the
    bundled preset by name and the sidecar resolves it, so an older image
    resolves it to a generic 577-character stub and no amount of retrying in
    Bambuddy will change the result.
    """
    return (
        f"The slicer returned a file with no printer start G-code for '{printer_preset_name}'. "
        "Printing it would heat the printer and extrude nothing, so it was not saved. "
        "This is fixed by updating the slicer sidecar image: older ones cannot read the "
        "companion profile that holds the real start G-code for most Bambu printers. "
        "Update the sidecar and slice again."
    )
