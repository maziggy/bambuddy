"""Shared checks run before an AMS drying command is sent.

Both the immediate POST /printers/{id}/drying/start endpoint and the
scheduler's delayed dispatch go through here, so a run that the immediate
path would refuse is never silently published by the scheduled path.
"""

from backend.app.services.printer_manager import drying_screen_only, supports_drying

SCREEN_ONLY_DETAIL = "This printer only supports AMS drying from its own screen"
UNSUPPORTED_DETAIL = "Drying not supported for this printer model or firmware version"

# Firmware dry_sf_reason codes, as surfaced by the AMS status payload.
DRY_SF_REASON_MESSAGES = {
    0: "Printer is busy",
    1: "Insufficient power: too many AMS drying or external PSU required",
    2: "AMS is busy",
    3: "Filament is at the AMS outlet, retract it first",
    4: "AMS is already starting a drying cycle",
    5: "Not supported in 2D mode",
    6: "AMS is already drying",
    7: "AMS firmware is upgrading",
    8: "Plug in the external AMS power adapter to start drying",
}

# Codes 1 and 8 mean a power-supply problem the user has to fix; the rest
# clear on their own. The frontend splits blocked states the same way.
POWER_REASON_CODES = frozenset({1, 8})

# Code 3 also needs the user to act, but the fix is retracting filament rather
# than anything to do with power, so it gets its own token instead of the
# generic "cannot dry right now" the transient codes share.
RETRACT_REASON_CODE = 3

WAITING_REASON_POWER = "ams_power_required"
WAITING_REASON_RETRACT = "ams_retract_filament"
WAITING_REASON_BLOCKED = "ams_blocked"


def check_drying_supported(model: str | None, firmware: str | None, *, require_firmware: bool = True) -> str | None:
    """Return a message if this printer cannot dry, else None.

    Pass require_firmware=False when there is no live status to read a version
    from, which leaves only the model check. Dispatch always has a status and
    judges both.
    """
    if drying_screen_only(model):
        return SCREEN_ONLY_DETAIL
    if require_firmware and not supports_drying(model, firmware):
        return UNSUPPORTED_DETAIL
    return None


def find_ams_unit(state, ams_id: int) -> dict | None:
    """Locate an AMS unit in a live printer status payload."""
    for unit in (state.raw_data.get("ams") if state else None) or []:
        try:
            if int(unit.get("id", -1)) == ams_id:
                return unit
        except (TypeError, ValueError):
            continue
    return None


def blocking_reason_codes(unit: dict | None) -> list[int]:
    """Known dry_sf_reason codes on this unit, in reported order."""
    codes = []
    for code in (unit or {}).get("dry_sf_reason") or []:
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            continue
        if code_int in DRY_SF_REASON_MESSAGES:
            codes.append(code_int)
    return codes


def primary_reason_code(codes: list[int]) -> int | None:
    """The one code to report when the AMS sets several at once.

    Power and filament-at-the-outlet need the user to go and do something; the
    rest clear on their own. Naming an actionable one is more use than whichever
    the firmware happened to list first, and going through here keeps the
    immediate endpoint's message and the scheduled row's waiting_reason
    describing the same blocked AMS the same way.
    """
    for code in codes:
        if code in POWER_REASON_CODES:
            return code
    if RETRACT_REASON_CODE in codes:
        return RETRACT_REASON_CODE
    return codes[0] if codes else None


def waiting_reason_for_codes(codes: list[int]) -> str:
    """Map blocking codes onto the token the frontend translates."""
    code = primary_reason_code(codes)
    if code in POWER_REASON_CODES:
        return WAITING_REASON_POWER
    if code == RETRACT_REASON_CODE:
        return WAITING_REASON_RETRACT
    return WAITING_REASON_BLOCKED


def resolve_filament(unit: dict | None, filament: str) -> str:
    """Fill an empty filament field from the first loaded tray.

    The printer rejects a drying payload with no filament type, so both
    callers fall back to the loaded spool and then to PLA.
    """
    if filament:
        return filament
    for tray in (unit or {}).get("tray") or []:
        tray_type = tray.get("tray_type")
        if tray_type:
            return str(tray_type)
    return "PLA"
