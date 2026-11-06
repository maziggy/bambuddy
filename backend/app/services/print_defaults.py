"""The install's default print options for a queue job nobody set them on.

The print dialog seeds its toggles from these settings. File Manager's "Add to
queue", the webhook and a pipeline run have no toggles, so they take the
defaults from here; the virtual printer takes them where the slicer sent
nothing. Fallbacks match AppSettings in schemas/settings.py.
"""

from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession


class DefaultPrintOptions(TypedDict):
    bed_levelling: str
    flow_cali: str
    nozzle_offset_cali: str
    vibration_cali: bool
    layer_inspect: bool
    timelapse: bool


def _bool_setting(value: str | None, default: bool) -> bool:
    return value.lower() == "true" if value is not None else default


def _tristate_setting(value: str | None, default: str) -> str:
    """Tri-state default (off/on/auto), coercing legacy true/false rows."""
    if value is None:
        return default
    low = value.strip().lower()
    if low in ("on", "off", "auto"):
        return low
    if low in ("true", "1"):
        return "on"
    if low in ("false", "0"):
        return "off"
    return default


async def default_print_options(db: AsyncSession) -> DefaultPrintOptions:
    """The default print options, keyed by PrintQueueItem column."""
    from backend.app.api.routes.settings import get_setting

    return {
        "bed_levelling": _tristate_setting(await get_setting(db, "default_bed_levelling"), "auto"),
        "flow_cali": _tristate_setting(await get_setting(db, "default_flow_cali"), "auto"),
        "nozzle_offset_cali": _tristate_setting(await get_setting(db, "default_nozzle_offset_cali"), "auto"),
        "vibration_cali": _bool_setting(await get_setting(db, "default_vibration_cali"), True),
        "layer_inspect": _bool_setting(await get_setting(db, "default_layer_inspect"), False),
        "timelapse": _bool_setting(await get_setting(db, "default_timelapse"), False),
    }
