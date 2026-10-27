"""Default print options for queue items created without the print dialog (#3397)."""

from sqlalchemy.ext.asyncio import AsyncSession

TRISTATE_VALUES = ("off", "on", "auto")

# Setting key suffix -> fallback, matching the PrintQueueItem column defaults and
# the print dialog's DEFAULT_PRINT_OPTIONS.
_TRISTATE_DEFAULTS = {"bed_levelling": "auto", "flow_cali": "auto", "nozzle_offset_cali": "auto"}
_BOOL_DEFAULTS = {"vibration_cali": True, "layer_inspect": False, "timelapse": False}


def _tristate(value: str | None, default: str) -> str:
    """Tri-state workflow default, coercing legacy true/false rows."""
    if value is None:
        return default
    low = value.strip().lower()
    if low in TRISTATE_VALUES:
        return low
    if low in ("true", "1"):
        return "on"
    if low in ("false", "0"):
        return "off"
    return default


def _bool(value: str | None, default: bool) -> bool:
    return value.lower() == "true" if value is not None else default


async def default_print_options_for_new_queue_item(db: AsyncSession) -> dict[str, str | bool]:
    """The print options for a queue item created without the print dialog.

    The dialog seeds its toggles from the ``default_*`` settings (Settings ->
    Workflow -> Default Print Options). The library bulk add, the webhook and a
    pipeline run have no toggles to seed, so without this their rows kept the
    column defaults -- timelapse off whatever the install asked for. The
    returned keys are ``PrintQueueItem`` columns, ready to pass as keywords.
    """
    from backend.app.api.routes.settings import get_setting

    options: dict[str, str | bool] = {}
    for key, default in _TRISTATE_DEFAULTS.items():
        options[key] = _tristate(await get_setting(db, f"default_{key}"), default)
    for key, default in _BOOL_DEFAULTS.items():
        options[key] = _bool(await get_setting(db, f"default_{key}"), default)
    return options
