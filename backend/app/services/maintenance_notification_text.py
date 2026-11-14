"""Wording for the maintenance variables of print notifications (#2776).

Pure functions over the maintenance overview's items, so the arithmetic stays
the Maintenance page's own (``_get_printer_maintenance_internal``) and only the
phrasing lives here. A task is counted in print hours or in calendar days
(``interval_type``); the two cannot be added or compared as numbers, so each
amount carries its own unit.
"""

from collections.abc import Iterable
from typing import Any

# The variables every print outcome notification carries, blank when there is
# nothing to say (no printer maintenance set up, or the lookup failed).
NO_MAINTENANCE_VARIABLES: dict[str, str] = {
    "maintenance_due_in": "",
    "maintenance_overdue": "",
    "maintenance_summary": "",
}


def _amount(value: float, unit: str) -> str:
    """``12.5 h`` / ``3 h`` / ``1 day`` / ``5 days``; the sign is left to the caller."""
    text = f"{abs(value):.1f}".rstrip("0").rstrip(".")
    if unit == "days":
        return f"{text} day" if text == "1" else f"{text} days"
    return f"{text} h"


def _remaining(item: Any) -> tuple[float, str]:
    """What is left before the task falls due, in the unit it is counted in.

    Negative once it is overdue.
    """
    if item.interval_type == "days":
        return (item.days_until_due or 0.0, "days")
    return (item.hours_until_due, "hours")


def maintenance_variables(items: Iterable[Any]) -> dict[str, str]:
    """The three maintenance variables for one printer's tasks.

    * ``maintenance_due_in``: how long until the next task falls due, as
      "12.5 h" or "5 days". Of the tasks not yet overdue it takes the one with
      the smallest share of its interval left, the same measure the Maintenance
      page uses to warn (the last tenth), which is what makes hours and days
      comparable. Empty when nothing is left to come.
    * ``maintenance_overdue``: the tasks past their interval and by how much,
      comma-separated ("Nozzle cleaning (2.5 h over), Lubricate rails (3 days over)").
      Empty when none is.
    * ``maintenance_summary``: both in one line for a template with room for one
      variable; empty when the printer has no task to report.

    Disabled tasks are left out, as on the Maintenance page.
    """
    overdue: list[str] = []
    upcoming: list[tuple[float, str, str]] = []  # (share of the interval left, name, amount)

    for item in items:
        if not item.enabled:
            continue
        left, unit = _remaining(item)
        if item.is_due or left <= 0:
            overdue.append(f"{item.maintenance_type_name} ({_amount(left, unit)} over)")
        else:
            interval = item.interval_hours or 0
            share = left / interval if interval > 0 else 1.0
            upcoming.append((share, item.maintenance_type_name, _amount(left, unit)))

    if not overdue and not upcoming:
        return dict(NO_MAINTENANCE_VARIABLES)

    upcoming.sort(key=lambda entry: entry[0])
    due_in = upcoming[0][2] if upcoming else ""

    parts = []
    if overdue:
        parts.append("Overdue: " + ", ".join(overdue))
    if upcoming:
        parts.append(f"Next: {upcoming[0][1]} in {due_in}")

    return {
        "maintenance_due_in": due_in,
        "maintenance_overdue": ", ".join(overdue),
        "maintenance_summary": " | ".join(parts),
    }
