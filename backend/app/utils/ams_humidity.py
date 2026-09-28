"""Shared reading of an AMS unit's humidity.

Bambu sends two humidity fields and they are not the same quantity.
``humidity_raw`` is relative humidity in percent. ``humidity`` is a 1-5 drop
index, and it runs the other way: OpenBambuAPI's push_info sample carries both
in one line -- ``ams0 temp:18.4;humidity:30%;humidity_idx:4`` -- so a high index
means dry where a high percentage means wet.

Falling back from one to the other therefore does not degrade, it inverts.
Index 2 rendered as "2%" reads as the driest a unit can be while the unit is in
fact the second-wettest of the five steps, and no index can ever exceed a
percentage threshold, so the humidity alarm and auto-drying silently never fire
for such a unit (#3140). A unit that reports no percentage has no percentage:
this returns ``None``, which every caller already treats as "no reading" -- the
card hides the indicator, the alarm and auto-drying skip the unit, and the
history chart leaves a gap.

Kept as a leaf module like ``ams_drying``: nothing here imports from the app.
"""

from collections.abc import Mapping
from typing import Any


def ams_humidity_percent(ams_data: Any) -> float | None:
    """Relative humidity in percent for one AMS unit, or ``None``.

    ``None`` covers every case where the unit did not report a usable
    percentage, including the units that send only the 1-5 index -- which is
    deliberately never converted. See the module docstring.
    """
    if not isinstance(ams_data, Mapping):
        return None
    raw = ams_data.get("humidity_raw")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None  # Unparseable reading — not a licence to use the index
