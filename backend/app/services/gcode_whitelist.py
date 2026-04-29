"""Approved G-code command prefixes for macro execution.

Only commands whose uppercase prefix appears in this set are forwarded
to the printer via MQTT. All others are rejected with a logged warning.
"""

GCODE_WHITELIST: frozenset[str] = frozenset(
    {
        # ── Movement ─────────────────────────────────────────────────────────
        # XY movement via gcode_line is unsafe on Bambu firmware: G91 is ignored
        # for XY, so coordinates are always absolute and the toolhead can crash.
        # Use the bed-jog UI for XY; these are kept for Z-only and E moves.
        "G0",
        "G1",
        # Arc moves (clockwise / counter-clockwise)
        "G2",
        "G3",
        # Bambu guarded Z move — must be preceded by G91
        "G380",
        # ── Homing & positioning ──────────────────────────────────────────────
        "G28",  # Auto home (all / X / Y / Z)
        "G29",  # Bed mesh calibration
        "G29.1",  # Set Z offset
        "G29.2",  # Toggle bed mesh compensation
        "G90",  # Absolute positioning
        "G91",  # Relative positioning
        "G92",  # Set current position (e.g. G92 E0)
        # ── Temperature ───────────────────────────────────────────────────────
        "M104",  # Set nozzle temperature (non-blocking)
        "M109",  # Set nozzle temperature (blocking)
        "M140",  # Set bed temperature (non-blocking)
        "M190",  # Set bed temperature (blocking)
        # ── Fan control ───────────────────────────────────────────────────────
        # Always specify P1/P2/P3 — M106 without P is silently ignored on Bambu
        "M106",  # Set fan speed (P1=part, P2=aux, P3=chamber)
        "M107",  # Fan off
        "M142",  # Aux fan / chamber temp
        # ── Motor control ─────────────────────────────────────────────────────
        "M17",  # Enable steppers
        "M18",  # Disable steppers
        "M84",  # Disable steppers
        # ── Extruder mode ─────────────────────────────────────────────────────
        "M82",  # Absolute extruder mode
        "M83",  # Relative extruder mode
        # ── Motion limits ─────────────────────────────────────────────────────
        "M201",  # Set max acceleration per axis
        "M203",  # Set max feed rate per axis
        "M204",  # Set default acceleration
        "M204.2",  # Set acceleration multiplier (Bambu)
        "M205",  # Set jerk limits
        "M211",  # Soft endstops (enable / disable / save / restore)
        "M220",  # Feed rate override
        "M221",  # Flow rate override (also soft endstop control — see Gcodes_reference.md)
        # ── Wait / timing ─────────────────────────────────────────────────────
        "M400",  # Wait for moves to finish (+ optional delay S/P/U)
        "G4",  # Dwell / delay (max 90 s per call)
        # ── Pressure advance ──────────────────────────────────────────────────
        "M900",  # Pressure advance (K factor)
        # ── LED & laser ───────────────────────────────────────────────────────
        "M960",  # LED / laser toggle (S0-S5, P0/1)
        # ── Camera ───────────────────────────────────────────────────────────
        "M973",  # Nozzle camera on/off/expose
        "M981",  # Spaghetti detector on/off
        "M991",  # Layer change notification / timelapse
        # ── Sound ─────────────────────────────────────────────────────────────
        "M1006",  # Speaker / buzzer (enable, play note, wait)
        # ── Print progress & display ──────────────────────────────────────────
        "M73",  # Set build progress / remaining time
        "M73.2",  # Reset time constant
        "M1002",  # LCD action status / conditionals / speed level (Bambu)
        # ── AMS & filament ────────────────────────────────────────────────────
        "M412",  # Filament runout detection toggle
        "M620",  # AMS filament control (retract, select tray, calibrate)
        "M620.1",  # AMS flush speed / temperature
        "M620.3",  # Tangle detection toggle
        "M621",  # Load filament from AMS tray
        "M622",  # Conditional block start (pairs with M623)
        "M623",  # Conditional block end
        "M302",  # Cold extrusion toggle
        "G392",  # Clog detection toggle (Bambu)
        # Tool / extruder select — T0-T3 are AMS trays; T255/T1000/T1100 are special
        "T0",
        "T1",
        "T2",
        "T3",
        "T255",  # Switch to empty tool
        "T1000",  # Switch to local nozzle
        "T1100",  # Switch to scanning space
        # ── Vibration compensation & calibration ──────────────────────────────
        "M970",  # Vibration frequency sweep
        "M970.3",  # Vibration fast sweep
        "M974",  # Apply vibration curve fit
        "M975",  # Toggle vibration compensation
        "M982",  # Motor noise cancellation params
        "M982.2",  # Motor noise cancellation toggle
        "M982.4",  # Motor noise cancellation parameters (variant)
        # ── Skew & XY compensation ────────────────────────────────────────────
        "M1005",  # Skew calibration (calculate / set / toggle)
        "M290",  # XY compensation
        "M290.2",  # XY compensation (variant)
        # ── Printer configuration & EEPROM ────────────────────────────────────
        "M500",  # Save to EEPROM
        "M630",  # Reset internal Bambu state
        "M1003",  # Bed leveling / power loss recovery (Bambu)
        "M1007",  # Keep subsystem enabled (Bambu)
    }
)


def is_whitelisted(line: str) -> bool:
    """Return True if the first token of line is an approved G-code command."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith(";"):
        return False
    token = stripped.split()[0].upper()
    return token in GCODE_WHITELIST
