"""Approved G-code command prefixes for macro execution.

Only commands whose uppercase prefix appears in this set are forwarded
to the printer via MQTT. All others are rejected with a logged warning.
"""

GCODE_WHITELIST: frozenset[str] = frozenset(
    {
        # Movement
        "G0",
        "G1",
        # Homing
        "G28",
        # Positioning mode
        "G90",
        "G91",
        "G92",
        # Extruder mode
        "M82",
        "M83",
        # Motor disable
        "M84",
        # Nozzle temperature (set / wait)
        "M104",
        "M109",
        # Bed temperature (set / wait)
        "M140",
        "M190",
        # Fan control
        "M106",
        "M107",
        # Tool / extruder select
        "T0",
        "T1",
        "T2",
        "T3",
    }
)


def is_whitelisted(line: str) -> bool:
    """Return True if the first token of line is an approved G-code command."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith(";"):
        return False
    token = stripped.split()[0].upper()
    return token in GCODE_WHITELIST
