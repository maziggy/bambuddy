"""HMS action lookup.

Bambu printers report HMS errors with a fixed catalog of remediation actions
(resume / stop / check assistant / etc.). The catalog is bundled as JSON, keyed
by the 3-letter SN prefix (printer model code: 03W = A1, 31B = X1C, etc.) and
the short error code with no separator.

The action IDs and their string names are derived from BambuStudio's source via
`scripts/update_hms_actions.py`. The data file itself is fetched from Bambu's
public `e.bambulab.com/hms/GetActionImage.php` endpoint.
"""

import json
from enum import StrEnum
from pathlib import Path

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "hms_actions.json"

# Loaded eagerly at import — the file is ~150KB and only read once. Using an
# absolute path keeps the load independent of CWD (systemd unit, Docker
# entrypoint, pytest run from `backend/`).
with _DATA_FILE.open("r", encoding="utf-8") as _f:
    _actions: dict[str, dict[str, list[str]]] = json.load(_f)


class HMSAction(StrEnum):
    """Remediation actions a Bambu printer can offer for an HMS error.

    Values intentionally match the constants used in BambuStudio's source so the
    HMS-data fetcher can map Bambu's integer action IDs straight to these
    strings. The CANCLE typo is preserved verbatim — it's how BambuStudio spells
    it, and changing it would break the action lookup against the catalog.
    """

    RESUME_PRINTING = "RESUME_PRINTING"
    RESUME_PRINTING_DEFECTS = "RESUME_PRINTING_DEFECTS"
    RESUME_PRINTING_PROBELM_SOLVED = "RESUME_PRINTING_PROBELM_SOLVED"
    STOP_PRINTING = "STOP_PRINTING"
    CHECK_ASSISTANT = "CHECK_ASSISTANT"
    FILAMENT_EXTRUDED = "FILAMENT_EXTRUDED"
    RETRY_FILAMENT_EXTRUDED = "RETRY_FILAMENT_EXTRUDED"
    CONTINUE = "CONTINUE"
    LOAD_VIRTUAL_TRAY = "LOAD_VIRTUAL_TRAY"
    OK_BUTTON = "OK_BUTTON"
    FILAMENT_LOAD_RESUME = "FILAMENT_LOAD_RESUME"
    JUMP_TO_LIVEVIEW = "JUMP_TO_LIVEVIEW"
    NO_REMINDER_NEXT_TIME = "NO_REMINDER_NEXT_TIME"
    REFRESH_NOZZLE = "REFRESH_NOZZLE"
    IGNORE_NO_REMINDER_NEXT_TIME = "IGNORE_NO_REMINDER_NEXT_TIME"
    IGNORE_RESUME = "IGNORE_RESUME"
    PROBLEM_SOLVED_RESUME = "PROBLEM_SOLVED_RESUME"
    TURN_OFF_FIRE_ALARM = "TURN_OFF_FIRE_ALARM"
    RETRY_PROBLEM_SOLVED = "RETRY_PROBLEM_SOLVED"
    STOP_DRYING = "STOP_DRYING"
    CANCLE = "CANCLE"  # sic — verbatim from BambuStudio
    REMOVE_CLOSE_BTN = "REMOVE_CLOSE_BTN"
    PROCEED = "PROCEED"
    OK_JUMP_RACK = "OK_JUMP_RACK"
    ABORT = "ABORT"
    DISABLE_PURIFICATION = "DISABLE_PURIFICATION"
    DONT_REMIND_NEXT_TIME = "DONT_REMIND_NEXT_TIME"
    DBL_CHECK_CANCEL = "DBL_CHECK_CANCEL"
    DBL_CHECK_DONE = "DBL_CHECK_DONE"
    DBL_CHECK_RETRY = "DBL_CHECK_RETRY"
    DBL_CHECK_RESUME = "DBL_CHECK_RESUME"
    DBL_CHECK_OK = "DBL_CHECK_OK"


def get_actions_for_error_code(device: str, error_code: str) -> list[str]:
    """Look up the action list for a printer SN prefix + short error code.

    Returns the empty list if the printer model or the error code is unknown —
    the modal renders no buttons in that case, which is the correct fallback.
    """
    return _actions.get(device, {}).get(error_code, [])
