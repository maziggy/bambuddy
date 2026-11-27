"""Which printers the plate-clear gate holds (#2860).

"Require plate-clear confirmation" is an app setting, and a printer may
override it: on, off, or no override (the column is null) to follow the
setting. A mixed fleet needs this, for example a printer with an automatic
plate changer next to one that is cleared by hand.

The overrides are cached here, by printer id, so the scheduler, WLED and the
notification can ask without a query per printer. They are loaded at startup
and kept current by the printer routes. This module imports nothing from the
app, so every service can use it.
"""

_overrides: dict[int, bool] = {}


def set_override(printer_id: int, value: bool | None) -> None:
    """Record a printer's override; None follows the app setting."""
    if value is None:
        _overrides.pop(printer_id, None)
    else:
        _overrides[printer_id] = bool(value)


def load_overrides(rows: dict[int, bool | None]) -> None:
    """Replace every cached override with the rows read from the database."""
    _overrides.clear()
    for printer_id, value in rows.items():
        set_override(printer_id, value)


def override(printer_id: int) -> bool | None:
    """The printer's own setting; None when it follows the app setting."""
    return _overrides.get(printer_id)


def applies(printer_id: int, setting: bool) -> bool:
    """Whether the gate applies to this printer, given the app setting."""
    return _overrides.get(printer_id, setting)
