"""HMS fault descriptions.

The texts come from Bambu Studio's own HMS files, generated into
``backend/app/data/hms_catalog.json`` by ``scripts/generate_hms_catalog.py``
(issue #2728). Do not edit the JSON by hand; rerun the script.

Two key spaces, never mixed:

  ``hms``    faults from the report's ``hms[]`` array, keyed by the 16-hex code
             the printer screen shows: ``attr`` then ``code``, i.e. module,
             module no., part, part no., alert level, error.
  ``error``  ``print_error`` faults, keyed by the 8-hex value.

Each has a merged table plus, per 3-character serial prefix, the entries whose
text differs on that model (0300_8001 is "paused by the user" on some models and
"paused by a pause command in the file" on others). A code Bambu lists with
empty text is stored as "" and reads as no description.

Only English ships; Bambu Studio has more languages if that is ever wanted.
"""

import json
from pathlib import Path

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "hms_catalog.json"

# Loaded once at import, like hms_actions.json. Absolute path so the load does
# not depend on the working directory (systemd unit, Docker, tests).
with _DATA_FILE.open(encoding="utf-8") as _f:
    _CATALOG: dict = json.load(_f)

_TABLES: dict[int, tuple[dict[str, str], dict[str, dict[str, str]]]] = {
    16: (_CATALOG["hms"], _CATALOG["hms_by_model"]),
    8: (_CATALOG["error"], _CATALOG["error_by_model"]),
}


def lookup_fault(full_code: str | None, model: str | None = None) -> str | None:
    """Return the catalogue entry for a fault: its text, "" when Bambu lists the
    code without text, or None when the code is not listed at all.

    ``full_code`` is the identifier the firmware matches on: 8 hex chars for a
    ``print_error``, 16 for an ``hms[]`` entry. ``model`` is the printer's
    3-character serial prefix; a model-specific text wins over the merged one,
    and an unknown or missing model uses the merged table.

    There is no fallback from one key space to the other. A 16-char code used to
    be collapsed to its first and last groups and looked up as a ``print_error``,
    but no real ``hms[]`` code has an error group at or above 0x4000, where every
    ``print_error`` key sits, so the collapse could only ever attach a
    neighbouring fault's sentence (#2728).
    """
    if not full_code:
        return None
    code = full_code.strip().upper()
    tables = _TABLES.get(len(code))
    if tables is None:
        return None
    merged, by_model = tables
    if model:
        specific = by_model.get(model.upper(), {}).get(code)
        if specific is not None:
            return specific
    return merged.get(code)


def describe_fault(full_code: str | None, model: str | None = None) -> str | None:
    """The fault's description, or None when there is no text for it.

    Resolved once at parse time so every surface that reports a fault -- the
    status response, the WebSocket broadcast, the completion payload,
    notifications -- says the same thing (#2926). None covers both "not listed"
    and "listed without text"; ``lookup_fault`` tells the two apart.
    """
    return lookup_fault(full_code, model) or None


def get_error_description(error_code: str, model: str | None = None) -> str | None:
    """Description for a ``print_error`` short code such as "0300_400C"."""
    return describe_fault(error_code.replace("_", ""), model)


def alert_level_from_print_error(error: int) -> int:
    """Alert level of a ``print_error``, from the first hex digit of its error.

    A ``print_error`` is a bare 32-bit module/error word with no level field.
    Its error number carries the level instead: 0x4xxx stops the task, 0x8xxx
    pauses it, 0xCxxx is a prompt. Mapped onto the ``hms[]`` alert levels
    (1 error, 2 warning, 3 notification) so both kinds sort and filter the same
    way. 0 for anything else, which Bambu defines as an invalid level.
    """
    return {0x4: 1, 0x8: 2, 0xC: 3}.get((error >> 12) & 0xF, 0)


def hms_fault_counts(error) -> bool:
    """Whether a fault counts as a problem: the same rule the frontend's
    ``filterKnownHMSErrors`` applies to the printer card, badge and camera wall.

    It counts when Bambu publishes text for it or it offers action buttons, and
    its level is a real one. An ``hms[]`` fault at level 3 (notification) with
    no actions does not count: those are things like "the top cover is open" or
    "the chamber is hot, fan speed increased", which a printer can hold through
    a whole print. A ``print_error`` at the same level (0xCxxx) still counts, as
    it always has; those are prompts such as "unable to start drying" (#2728).
    """
    if error.severity < 1:
        return False
    has_actions = bool(getattr(error, "actions", None))
    is_hms_notice = len(getattr(error, "full_code", "") or "") == 16 and error.severity == 3
    return has_actions or (bool(getattr(error, "description", None)) and not is_hms_notice)
