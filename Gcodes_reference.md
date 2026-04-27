# Bambu Lab G-code Reference

Compiled from: standard Marlin documentation, Bambu X1 wiki (X1Plus project), A1 Mini start routine analysis, and community research.

> **Safety note:** Several commands in this document are undocumented or partially documented. Commands marked ⚠️ are experimental or carry risk of unexpected behavior. Commands marked 🔴 conflict with another command and the conflict is called out explicitly.

---

## Table of Contents

1. [Movement Commands](#movement-commands)
2. [Homing & Positioning](#homing--positioning)
3. [Temperature Control](#temperature-control)
4. [Fan Control](#fan-control)
5. [Motor Control](#motor-control)
6. [Limits & Endstops](#limits--endstops)
7. [Extrusion Control](#extrusion-control)
8. [Print Progress & Display](#print-progress--display)
9. [AMS & Filament Control](#ams--filament-control)
10. [Vibration Compensation & Calibration](#vibration-compensation--calibration)
11. [Pressure Advance](#pressure-advance)
12. [Skew & XY Compensation](#skew--xy-compensation)
13. [LED & Laser Controls](#led--laser-controls)
14. [Camera Controls](#camera-controls)
15. [Sound (M1006)](#sound-m1006)
16. [Timing & Flow Control](#timing--flow-control)
17. [Printer Configuration & EEPROM](#printer-configuration--eeprom)
18. [Print Metadata & Job Control](#print-metadata--job-control)
19. [Undocumented / Experimental](#undocumented--experimental)
20. [Useful Coordinates (X1 Series)](#useful-coordinates-x1-series)
21. [Conflict Index](#conflict-index)

---

## Movement Commands

### G0 — Rapid Linear Move (Travel)
```
G0 [X<mm>] [Y<mm>] [Z<mm>] [F<mm/s>]
```
Non-print travel move. No extrusion. Firmware may treat G0 and G1 identically.

| Param | Type | Description |
|-------|------|-------------|
| X | float (mm) | Target X position |
| Y | float (mm) | Target Y position |
| Z | float (mm) | Target Z position |
| F | float (mm/s) | Feed rate (speed) |

**Example:** `G0 X128 Y128 F10000` — move to center of build plate at 10 000 mm/s

> **Bambu note:** XY movement via `gcode_line` MQTT command is treated as absolute even if G91 was issued. G91 (relative mode) is effectively ignored for XY axes when sending commands via API/terminal. Z movement via G380 is preferred for safe Z travel.

---

### G1 — Controlled Linear Move (Print)
```
G1 [X<mm>] [Y<mm>] [Z<mm>] [E<mm>] [F<mm/s>]
```
Linear move with optional extrusion. Used for all printing moves.

| Param | Type | Description |
|-------|------|-------------|
| X | float (mm) | Target X position |
| Y | float (mm) | Target Y position |
| Z | float (mm) | Target Z position |
| E | float (mm) | Extrusion amount (absolute or relative depending on M82/M83) |
| F | float (mm/s) | Feed rate |

**Examples:**
```gcode
G1 X0 Y0 F10000 E5    ; move to 0,0 extruding 5mm of filament
G1 F20000             ; adjust feed rate for upcoming moves
```

> **Bambu note:** Same XY absolute-only caveat as G0. Do not use for XY moves via API; firmware silently ignores relative mode for XY via gcode_line.

---

### G2 — Clockwise Arc Move
```
G2 [X<mm>] [Y<mm>] [Z<mm>] [I<mm>] [J<mm>] [R<mm>] [F<mm/s>] [E<mm>]
```

| Param | Type | Description |
|-------|------|-------------|
| X, Y, Z | float (mm) | Target endpoint |
| I | float (mm) | X offset of arc center (relative) |
| J | float (mm) | Y offset of arc center (relative) |
| R | float (mm) | Arc radius (alternative to I/J) |
| F | float (mm/s) | Feed rate |
| E | float (mm) | Extrusion amount |

**Example:** `G2 X2 Y7 I-4 J-3` — clockwise arc to (2,7) with center offset (-4,-3)

---

### G3 — Counter-Clockwise Arc Move
```
G3 [X<mm>] [Y<mm>] [Z<mm>] [I<mm>] [J<mm>] [R<mm>] [F<mm/s>] [E<mm>]
```
Same parameters as G2, counter-clockwise direction.

**Example:** `G3 X20 Y20 R5 E0.5` — CCW arc around (20,20) with radius 5, extruding 0.5mm

---

### G5 — B-Spline Curve Move
Not standard in Marlin. Likely ignored on Bambu printers. Avoid.

---

### G380 — Guarded Z Move (Bambu-specific)
```
G380 S<mode> Z<mm> F<speed>
```
Safe Z-axis move with limit/guard checking. **Must be preceded by G91 (relative mode).**

| Param | Values | Description |
|-------|--------|-------------|
| S | 2 | Move Z axis only (upward guarded) |
| S | 3 | Move Z axis only (downward guarded, gentle) |
| Z | float (mm) | Distance to move (relative) |
| F | float (mm/s) | Speed |

**Example:**
```gcode
G91
G380 S2 Z10 F1200    ; guarded Z up 10mm
G380 S3 Z-6 F1200   ; guarded Z down 6mm
G90
```

---

## Homing & Positioning

### G28 — Auto Home
```
G28 [X] [Y] [Z] [P<precision>] [T<temp>]
```
Homes axes to endstop zero positions.

| Argument | Description |
|----------|-------------|
| (none) | Home all axes |
| X | Home X and Y axes |
| Z | Home Z axis |
| Z P0 | Low precision Z home |
| Z P0 T\<temp\> | Low precision Z home with heated nozzle (temp in °C) |
| T\<temp\> | Permissive temperature home (e.g. `G28 T300`) |
| X Y | Explicitly home X and Y |

**Examples:**
```gcode
G28          ; home all
G28 X Y      ; home X and Y first (recommended before Z)
G28 Z P0 T300
```

> **A1 Mini sequence:** Always home X/Y before Z to avoid collisions. Use `G28 Z P0 T300` for low-precision warm Z home during startup.

---

### G29 — Bed Mesh Calibration
```
G29 [A<1>] [X<mm>] [Y<mm>] [I<width>] [J<height>]
```
Runs bed leveling/mesh compensation.

| Param | Description |
|-------|-------------|
| A1 | Auto mesh with print area hint |
| X, Y | Start coordinate of print area |
| I, J | Width and height of print area |

**Example:** `G29 A1 X10 Y10 I200 J200`

---

### G29.1 — Set Z Offset
```
G29.1 Z<offset_mm>
```
Manually set Z height offset in mm. Negative = closer to bed.

**Example:** `G29.1 Z{-0.02}` — textured PEI offset

---

### G29.2 — Toggle Bed Mesh Compensation
```
G29.2 S<0|1>
```
| S | Description |
|---|-------------|
| 0 | Disable ABL (use for raw Z homing) |
| 1 | Enable ABL with loaded mesh |

---

### G90 — Absolute Positioning
Sets X, Y, Z to absolute coordinate mode. Most common mode during printing.

---

### G91 — Relative Positioning
Sets X, Y, Z to relative coordinate mode. Each move is offset from current position.

> **Bambu caveat:** Relative mode is ignored for XY axes when commands are sent via `gcode_line` MQTT API. Z works correctly with G380. Use G90 + absolute coordinates for reliable XY movement via API.

---

### G92 — Set Current Position
```
G92 [X<mm>] [Y<mm>] [Z<mm>] [E<mm>]
```
Redefines current position without moving. Does not home.

**Common use:** `G92 E0` — reset extruder position to zero before a move.

---

### G92.1 — Reset Position to Machine Coordinates ⚠️
```
G92.1 E0
```
Seen in undocumented context. Likely resets extruder to machine zero. Use with caution.

---

## Temperature Control

### M104 — Set Hotend Temperature (Non-blocking)
```
M104 S<temp> [T<tool>] [H<flag>]
```
Sets hotend target temperature and continues immediately without waiting.

| Param | Range | Description |
|-------|-------|-------------|
| S | 0–300 °C | Target temperature |
| T | 0, 1... | Tool index (for multi-tool) |
| H | int | Bambu heating hint/override flag |

**Example:** `M104 S200` — set hotend to 200°C

---

### M109 — Set Hotend Temperature (Blocking)
```
M109 S<temp> [T<tool>] [H<flag>]
```
Same as M104 but **blocks G-code execution** until target temperature is reached.

| Param | Range | Description |
|-------|-------|-------------|
| S | 0–300 °C | Target temperature |
| T | 0, 1... | Tool index |
| H | int | Bambu override flag |

**Example:** `M109 S250` — wait until hotend reaches 250°C

---

### M140 — Set Bed Temperature (Non-blocking)
```
M140 S<temp> [H<flag>]
```

| Param | Range | Description |
|-------|-------|-------------|
| S | 0–110 °C | Target bed temperature |
| H | int | Bambu heating hint |

**Example:** `M140 S65`

---

### M190 — Set Bed Temperature (Blocking)
```
M190 S<temp> [H<flag>]
```
Same as M140 but **blocks** until bed reaches temperature.

| Param | Range | Description |
|-------|-------|-------------|
| S | 0–110 °C | Target bed temperature |
| H | int | Bambu override flag |

---

## Fan Control

### M106 — Set Fan Speed
```
M106 [P<fan>] S<speed>
```
Sets a specific fan to the given speed.

| Param | Values | Description |
|-------|--------|-------------|
| P | 1 | Part cooling fan |
| P | 2 | Auxiliary fan |
| P | 3 | Chamber fan |
| S | 0–255 | Speed (0 = off, 255 = full) |

**Examples:**
```gcode
M106 P1 S255    ; full part fan
M106 P2 S128    ; aux fan at ~50%
M106 P3 S0      ; chamber fan off
```

> **Critical:** On Bambu printers, `M106` without a `P` parameter is **silently ignored**. Always specify which fan with `P1`, `P2`, or `P3`.

---

### M107 — Turn Fan Off
```
M107 [P<fan>]
```
Turns off the specified fan (or all fans if P omitted, though P-less behavior may be unreliable — use `M106 P<n> S0` for certainty).

| Param | Values | Description |
|-------|--------|-------------|
| P | 1, 2, 3 | Fan to turn off |

---

### M142 — Aux Fan / Chamber Temp (Bambu X1C/X1E)
```
M142 P<fan> R<rpm?> S<speed>
```
Firmware reference command for aux fan and chamber temperature on X1C/X1E.

| Param | Description |
|-------|-------------|
| P | Fan index |
| R | RPM or target? (not fully documented) |
| S | Speed 0–255 |

**Example:** `M142 P1 R35 S40`

> Partially documented. Prefer M106 P2/P3 for aux and chamber fan on known Bambu models.

---

## Motor Control

### M17 — Enable Steppers / Set Motor Current
```
M17 [X<amps>] [Y<amps>] [Z<amps>] [E<amps>] [R] [S]
```

| Argument | Description |
|----------|-------------|
| (none) | Enable all stepper motors |
| X Y Z E | Set current in amps for each axis |
| R | Restore default current values |
| S | Enable steppers (explicit) |

**Defaults (from slicer):** X=1.2A, Y=1.2A, Z=0.75A

**Examples:**
```gcode
M17                     ; enable all motors
M17 X0.7 Y0.9 Z0.3     ; set optimized currents (A1 Mini startup)
M17 Z0.5               ; restore Z current after homing
M17 R                  ; restore all defaults
```

> **Sound use:** M17 must be called before M1006 (sound) to engage the stepper driver used for audio generation.

---

### M18 — Disable Steppers
```
M18 [X] [Y] [Z] [E]
```

| Argument | Description |
|----------|-------------|
| (none) | Disable all stepper motors |
| X Y Z E | Disable only specified axes |

**Example:** `M18` — disable all (used after sound playback to reset)

---

### M84 — Disable Steppers (Marlin Alias)
Equivalent to M18. Disables all stepper motors. Use M18 on Bambu for consistency.

---

## Limits & Endstops

### M201 — Set Max Acceleration
```
M201 [X<mm/s²>] [Y<mm/s²>] [Z<mm/s²>] [E<mm/s²>]
```
Sets maximum acceleration per axis.

| Param | Description |
|-------|-------------|
| Z | Z axis acceleration limit (mm/s²) |

> On Bambu, M201 is documented specifically for Z axis only. X/Y/E may or may not be accepted.

---

### M203 — Set Max Feed Rate
```
M203 [X<mm/s>] [Y<mm/s>] [Z<mm/s>] [E<mm/s>]
```
Sets maximum feed rate for each axis.

---

### M204 — Set Default Acceleration
```
M204 [S<mm/s²>] [P<print_accel>] [T<travel_accel>]
```

| Param | Description |
|-------|-------------|
| S | General acceleration limit |
| P | Print acceleration |
| T | Travel acceleration |

**Example:** `M204 S6000` — set default acceleration to 6000 mm/s²

---

### M204.2 — Set Acceleration Multiplier (Bambu)
```
M204.2 K<multiplier>
```
Scales the acceleration by a unitless multiplier. Default = 1.0.

| Param | Default | Description |
|-------|---------|-------------|
| K | 1.0 | Acceleration magnitude multiplier |

---

### M205 — Advanced Motion Settings
```
M205 [X<mm/s>] [Y<mm/s>] [Z<mm/s>] [E<mm/s>]
```
Sets jerk limits per axis (minimum instantaneous speed change without acceleration planning).

| Param | Default | Description |
|-------|---------|-------------|
| X Y Z E | 0 | Jerk limit in mm/s |

---

### M211 — Soft Endstops
```
M211 [S] [X<0|1>] [Y<0|1>] [Z<0|1>] [R]
```

| Argument | Description |
|----------|-------------|
| S | Push (save) current soft endstop state |
| X Y Z | Enable (1) or disable (0) endstop per axis |
| R | Restore previously saved endstop state |

**Examples:**
```gcode
M211 X1 Y1 Z1    ; enable all soft endstops
M211 X0 Y0 Z0    ; disable all (for wiper/service area access)
M211 S           ; save current state
M211 R           ; restore saved state
```

> Commonly used in start routines: disable before service area moves, restore after.

---

### M220 — Set Feed Rate Override
```
M220 S<percent> [B<flag>] [K<unitless>]
```

| Param | Default | Description |
|-------|---------|-------------|
| S | 100 | Speed as percentage (100 = normal) |
| B | — | Bambu-specific override flag (B1) |
| K | 1.0 | Feed rate as unitless multiplier (Bambu speed system) |

**Examples:**
```gcode
M220 S100     ; reset to 100% speed
M220 K1.0     ; set feed rate multiplier to 1x
M220 B1       ; Bambu-specific override
```

---

### M221 — Flow Rate / Soft Endstop Control 🔴 CONFLICT

> **⚠️ CONFLICT:** M221 has **two unrelated uses** depending on parameters:
> - `M221 S<percent>` → set extrusion flow rate override
> - `M221 X/Y/Z` → configure soft endstop enable/disable per axis
>
> These are completely different functions sharing the same command code. This is a Bambu firmware quirk. Pay close attention to parameters.

#### Use 1: Flow Rate Override (Standard Marlin / Bambu)
```
M221 S<percent> [B<flag>]
```

| Param | Default | Description |
|-------|---------|-------------|
| S | 100 | Flow rate as percentage (100 = normal) |
| B | — | Bambu override flag (B1) |

**Example:** `M221 S100` — reset flow to 100%

#### Use 2: Soft Endstop Control (Bambu-specific)
```
M221 [S] [X<0|1>] [Y<0|1>] [Z<0|1>]
```

| Argument | Description |
|----------|-------------|
| S (no value) | Push soft endstop status |
| X 0 / X 1 | Disable / enable X soft endstop |
| Y 0 / Y 1 | Disable / enable Y soft endstop |
| Z 0 / Z 1 | Disable / enable Z soft endstop |

**Examples:**
```gcode
M221 S          ; push/query soft endstop status
M221 X0 Y0 Z1  ; disable X and Y, enable Z endstop
M221 Z1         ; enable Z endstop
M221 Z0         ; turn off Z endstop
```

> **Note:** On Bambu, prefer M211 for soft endstop control — it is better documented and less ambiguous. M221 with X/Y/Z params appears in X1Plus wiki only.

---

## Extrusion Control

### M82 — Extruder Absolute Mode
Set extruder to absolute extrusion mode. E values are absolute coordinates.

---

### M83 — Extruder Relative Mode
Set extruder to relative extrusion mode. E values are incremental from current position.

**Standard start sequence:** `G90` (absolute XYZ) + `M83` (relative E) is the most common combination.

---

## Print Progress & Display

### M73 — Set Build Progress
```
M73 P<percent> R<remaining_min>
```
Updates print progress on the printer display.

| Param | Description |
|-------|-------------|
| P | Progress percentage (0–100) |
| R | Remaining time in minutes |

---

### M73.2 — Reset Time Constant
```
M73.2 R<multiplier>
```
Resets the print time constant used for display. Default = 1.0.

| Param | Default | Description |
|-------|---------|-------------|
| R | 1.0 | Time constant multiplier |

**Example:** `M73.2 R1.0` — reset to default at start of print

---

### M1002 — LCD Display / Job Control (Bambu) 🔴 MULTI-PURPOSE

> **⚠️ MULTI-PURPOSE:** M1002 has at least three distinct uses depending on the argument string passed:

#### Use 1: Display Action Status
```
M1002 gcode_claim_action : <action_id>
```
Shows a status message on the printer LCD during print.

| ID | Message |
|----|---------|
| 0 | Clear |
| 1 | Auto bed levelling |
| 2 | Heatbed preheating |
| 3 | Sweeping XY mech mode |
| 4 | Changing filament |
| 5 | M400 pause |
| 6 | Paused due to filament runout |
| 7 | Heating hotend |
| 8 | Calibrating extrusion |
| 9 | Scanning bed surface |
| 10 | Inspecting first layer |
| 11 | Identifying build plate type |
| 12 | Calibrating Micro Lidar |
| 13 | Homing toolhead |
| 14 | Cleaning nozzle tip |
| 15 | Checking extruder temperature |
| 16 | Paused by user |
| 17 | Front cover fell off |
| 18 | Calibrating micro lidar |
| 19 | Calibrating extruder flow |
| 20 | Paused — nozzle temp malfunction |
| 21 | Paused — heat bed temp malfunction |

#### Use 2: Conditional Flag Evaluation
```
M1002 judge_flag <flag_name>
```
Evaluates a slicer variable/flag to conditionally execute the following M622/M623 block.

**Examples:**
```gcode
M1002 judge_flag build_plate_detect_flag
M1002 judge_flag g29_before_print_flag
M1002 judge_last_extrude_cali_success
```

#### Use 3: Speed Level Setting
```
M1002 set_gcode_claim_speed_level
```
Updates speed profile on LCD. Default = 5.

#### Use 4: Filament Type Announcement
```
M1002 set_filament_type:<type_string>
```
**Example:** `M1002 set_filament_type:PLA`

> **Conflict note:** M1002 in the Bambu-specific table was listed as "End of print metadata block (job status, logs)" — this conflicts with the X1Plus wiki which documents it as an LCD/action/flag control command. The X1Plus documentation is more authoritative for in-print use.

---

## AMS & Filament Control

### T — Tool/Filament Selection
```
T<tool_id>
```

| ID | Description |
|----|-------------|
| 0, 1, 2, 3 | AMS tray index — triggers AMS filament change |
| 255 | Switch to empty tool |
| 1000 | Switch to nozzle (local tool) |
| 1100 | Switch to scanning space |

---

### M412 — Filament Runout Detection
```
M412 S<0|1>
```

| S | Description |
|---|-------------|
| 0 | Disable runout detection |
| 1 | Enable runout detection |

---

### M620 — AMS Filament Control (Bambu)
```
M620 [M] [S<tray>A] [C<ams_idx>] [R<tray_idx>] [P<tray_idx>]
```

| Argument | Description |
|----------|-------------|
| M | Enable remap mode |
| S\<n\>A | Select AMS tray n (with remap) |
| C\<n\> | Calibrate AMS by AMS index |
| R\<n\> | Refresh AMS by tray index |
| P\<n\> | Select AMS tray by tray index |
| S\<n\> | Select AMS by tray index (without A suffix) |
| S255 | Retract filament |

**AMS retract sequence:**
```gcode
M620 S255
; retraction gcode here
M621 S255
```

---

### M620.1 — AMS Flush Speed/Temp
```
M620.1 E F<speed> T<temp>
```
Sets flush (purge) speed and temperature for AMS filament change.

| Param | Description |
|-------|-------------|
| E | Enable/execute |
| F | Flush speed (mm/min) |
| T | Flush temperature (°C) |

---

### M620.3 — Tangle Detection
```
M620.3 W<0|1>
```

| W | Description |
|---|-------------|
| 0 | Disable tangle detection |
| 1 | Enable tangle detection |

---

### M621 — Load Filament in AMS
```
M621 S<tray>A
```
Loads filament from AMS tray into extruder.

| Param | Description |
|-------|-------------|
| S\<n\>A | Load from tray n |
| S255 | Complete retraction |

---

### M622 — Conditional Block Start
```
M622 J<value>
M622 S<value>
```
Starts a conditional G-code block evaluated by the preceding `M1002 judge_flag` call. Paired with M623.

| Param | Description |
|-------|-------------|
| J | Condition value (0 or 1) |
| S | Condition from flag (S1 = true) |

---

### M623 — Conditional Block End
```
M623
```
Closes an M622 conditional block. Always paired with M622.

---

### M1004 — AMS / Filament Path Control (Bambu)
AMS or filament path selection/control. Parameters not fully documented.

---

### M1010 — AMS Info Block (Bambu)
Reports AMS info (filament type, color, location). Used in print metadata.

---

### M302 — Cold Extrusion Toggle
```
M302 S<min_temp> P<0|1>
```

| Param | Description |
|-------|-------------|
| S70 | Minimum extrusion temperature threshold |
| P0 | Disable cold extrusion protection |
| P1 | Enable cold extrusion protection |

---

### G392 — Clog Detection Toggle (Bambu)
```
G392 S<0|1>
```

| S | Description |
|---|-------------|
| 0 | Disable clog detection |
| 1 | Enable clog detection |

---

## Vibration Compensation & Calibration

### M970 — Vibration Compensation Frequency Sweep
```
M970 Q<axis> A<amplitude> B<lower_hz> C<upper_hz> [H<hz>] K<mode>
```

| Param | Description |
|-------|-------------|
| Q | Axis: 0=Y, 1=X |
| A | Amplitude (Hz) |
| B | Lower bound of sweep (Hz) |
| C | Upper bound of sweep (Hz) |
| H | Optional, units Hz (undefined purpose) |
| K | Mode: 0 or 1 |

**Full X/Y sweep example:**
```gcode
M970 Q1 A7 B10 C125 K0    ; X axis range 1
M970 Q1 A7 B125 C250 K1   ; X axis range 2
M974 Q1 S2 P0              ; X axis curve fit
M970 Q0 A9 B10 C125 H20 K0 ; Y axis range 1
M970 Q0 A9 B125 C250 K1   ; Y axis range 2
M974 Q0 S2 P0              ; Y axis curve fit
M975 S1                    ; enable
```

---

### M970.3 — Vibration Compensation Fast Sweep ⚠️
```
M970.3 Q<axis> A<amplitude> B<lower_hz> C<upper_hz> [H<hz>] K<mode>
```
Same parameters as M970, faster sweep variant. Less documented.

---

### M974 — Apply Vibration Curve Fit
```
M974 Q<axis> S2 P0
```
Applies curve fitting to vibration compensation data collected by M970.

| Param | Description |
|-------|-------------|
| Q | Axis: 0=Y, 1=X |
| S | Mode (S2 = standard) |
| P | Parameter (P0) |

---

### M975 — Toggle Vibration Compensation
```
M975 S<0|1>
```

| S | Description |
|---|-------------|
| 0 | Disable vibration compensation |
| 1 | Enable vibration compensation |

---

### M982 — Motor Noise Cancellation
```
M982 Q P V D L T I
```
Motor noise cancellation settings. Parameters not fully documented.

---

### M982.2 — Motor Noise Cancellation Toggle
```
M982.2 S<0|1>
```

| S | Description |
|---|-------------|
| 0 | Disable cog noise reduction |
| 1 | Enable cog noise reduction |

**In start routine:** `M982.2 S1` (enable) is standard.

---

### M982.4 — Motor Noise Parameters ⚠️
```
M982.4 S<val> V<val>
```
Partially documented noise cancellation parameter setter.

---

### M983 — Dynamic Extrusion Compensation (Bambu) ⚠️
```
M983 F<flow_rate> A<amplitude> H<nozzle_dia>
```
Used during dynamic flow calibration in start routine.

| Param | Description |
|-------|-------------|
| F | Flow rate (mm³/s) |
| A | Amplitude |
| H | Nozzle diameter (mm) |

---

### M984 — Extrusion Calibration Finalize (Bambu) ⚠️
```
M984 A<amplitude> E<val> S<val> F<flow_rate> H<nozzle_dia>
```
Final correction step after M983 calibration. Parameters partially documented.

---

## Pressure Advance

### M900 — Pressure Advance (Linear Advance)
```
M900 [K<factor>] [L<limit>] [M<val>]
```

| Param | Description |
|-------|-------------|
| K | Pressure advance factor (0.0 = disabled) |
| L | Limit value (1000.0 = standard) |
| M | Mode parameter |
| (none) | Publish currently saved pressure advance values |

**Examples:**
```gcode
M900 K0.0 L1000.0 M1.0   ; baseline PA (calibration start)
M900                       ; query current values
```

---

## Skew & XY Compensation

### M1005 — Skew Calibration 🔴 CONFLICT

> **⚠️ CONFLICT:** M1005 appears in two incompatible ways:
> - X1Plus wiki: fully documented as skew compensation command
> - Earlier Bambu-specific table: listed as "Unknown – likely print status or internal state logging"
>
> The X1Plus documentation is more authoritative. Treat the "unknown" description as outdated/incorrect.

```
M1005 [X<mm>] [Y<mm>] [I<rad>] [P<0|1>]
```

| Param | Description |
|-------|-------------|
| X Y | Diagonal lengths (mm) — calculates skew angle in radians |
| I | Overwrite skew value directly (radians) |
| P0 | Disable skew compensation |
| P1 | Enable skew compensation |

---

### M290 — XY Compensation ⚠️
```
M290 X<mm> Y<mm>
```
XY dimensional compensation offset. Relationship to M290.2 unclear.

---

### M290.2 — XY Compensation (Variant) ⚠️
```
M290.2 X<mm> Y<mm>
```
XY compensation, possibly a newer or alternate form. Both M290 and M290.2 exist in the wiki with identical descriptions — distinction not documented.

---

## LED & Laser Controls

### M960 — LED / Laser Toggle
```
M960 S<channel> P<0|1>
```

| S | P | Description |
|---|---|-------------|
| 0 | 0/1 | Toggle all LEDs |
| 1 | 0/1 | Toggle horizontal laser |
| 2 | 0/1 | Toggle vertical laser |
| 4 | 0/1 | Toggle nozzle LED |
| 5 | 0/1 | Toggle logo LED |

**Examples:**
```gcode
M960 S5 P1    ; enable toolhead lamp (logo LED)
M960 S1 P0    ; laser ch1 off
M960 S5 P0    ; toolhead lamp off
```

---

## Camera Controls

### M971 — Capture Image
```
M971 S<mode> P<exposure>
```
Captures an image to `/userdata/log/`.

| Param | Description |
|-------|-------------|
| S | Mode |
| P | Exposure value |

---

### M972 — Camera Clarity Measurement ⚠️
```
M972 S5 P0
```
Measures Xcam clarity. Limited documentation.

---

### M973 — Nozzle Camera Control
```
M973 S<mode> [P<exposure>]
```

| S | P | Description |
|---|---|-------------|
| 1 | — | Nozzle cam autoexpose |
| 3 | — | Nozzle cam on |
| 4 | — | Nozzle cam off |
| 6 | 0 | Auto expose for horizontal laser |
| 6 | 1 | Auto expose for vertical laser |
| S | P | Set nozzle camera exposure |

---

### M976 — Scan / Detection Operations
```
M976 S<mode> P<param>
```

| S | P | Description |
|---|---|-------------|
| 1 | \<num\> | First layer scan |
| 2 | 1 | Hotbed scan |
| 3 | 2 | Register void printing detection |

---

### M981 — Spaghetti Detector
```
M981 S<0|1> P20000
```

| S | Description |
|---|-------------|
| 0 | Spaghetti detector off |
| 1 | Spaghetti detector on |

---

### M991 — Layer Change Notification / Timelapse
```
M991 S0 P<param>
```

| P | Description |
|---|-------------|
| 0 | Notify printer of layer change |
| -1 | End smooth timelapse at safe position |

---

## Sound (M1006)

### M1006 — Speaker / Buzzer Control (Bambu)
```
M1006 [S<1>] [W] [A<0>] [B<rest_ms>] [L<dur_ms>] [C<note>] [D<atk>] [M<peak>] [E<note2>] [F<rel>] [N<vol>]
```

#### Enable Speaker
```
M1006 S1
```
Must be called before playing notes. Requires M17 (motors enabled) first.

#### Play Note
```
M1006 A0 B<rest_ms> L<duration_ms> C<start_note> D<attack_ms> M<peak_vol> E<end_note> F<release_ms> N<volume>
```

| Param | Type | Description |
|-------|------|-------------|
| A | 0 | Always 0 (waveform type?) |
| B | int (ms) | Rest/gap before note starts |
| L | int (ms) | Note duration |
| C | MIDI note (0–127) | Start pitch (MIDI note number, **not Hz**) |
| D | int (ms) | Attack duration |
| M | 0–100 | Peak volume during attack |
| E | MIDI note (0–127) | End pitch (sustain/target pitch) |
| F | int (ms) | Release duration |
| N | 0–100 | Overall note volume |

> **Important:** C and E take **MIDI note numbers**, not frequencies.
> - C=E for a pure tone
> - C≠E for pitch sweep/bend between two notes
>
> Common MIDI note mapping:
> | Note | MIDI |
> |------|------|
> | A4 | 57 |
> | Bb4 | 58 |
> | B4 | 59 |
> | C5 | 60 |
> | D5 | 62 |
> | Eb5 | 63 |
> | E5 | 64 |
> | G5 | 67 |
> | Bb5 | 70 |
> | G4 | 55 |

#### Wait for Sound Finish
```
M1006 W
```
Blocks until the sound sequence completes.

**Minimal example:**
```gcode
M17
M400 S1
M1006 S1
M1006 A0 B0 L500 C60 D10 M100 E60 F10 N100   ; middle C for 500ms
M1006 W
M18
```

---

## Timing & Flow Control

### M400 — Wait for Moves to Finish
```
M400 [S<seconds>] [P<milliseconds>] [U<1>]
```

| Param | Description |
|-------|-------------|
| (none) | Wait for all movements to complete |
| S\<t\> | Wait for completion + additional delay in seconds |
| P\<t\> | Wait for completion + additional delay in milliseconds |
| U1 | Wait until user presses Resume on display |

**Examples:**
```gcode
M400        ; sync all moves
M400 S1     ; sync + 1s delay
M400 P500   ; sync + 500ms delay
M400 U1     ; pause until user resumes
```

---

### G4 — Dwell (Delay)
```
G4 [S<seconds>] [P<milliseconds>]
```
Inserts a delay. **Does not block G-code execution pipeline** in the same way M400 does — subsequent G-code may be buffered.

> **Max value:** G4 S90 (90 seconds). For longer delays, chain multiple `G4 S90`.

---

### M630 — Reset Internal State (Bambu)
```
M630 S0 P0
```
Resets Bambu internal printer state. Used in start routines after homing.

---

## Printer Configuration & EEPROM

### M500 — Save to EEPROM
```
M500
```
Persists current settings (acceleration, endstops, etc.) to flash/EEPROM.

---

### M1003 — Bed Leveling / Power Loss Recovery (Bambu) 🔴 CONFLICT

> **⚠️ CONFLICT:** M1003 appears with two different uses:
> - Bambu M-code table: "Custom bed leveling (Bambu's version of mesh calibration)"
> - Printer configuration table: `M1003 S0/S1` toggles power loss recovery
>
> These appear to be different functions. The S0/S1 toggle form is from the X1Plus printer config section. Treat both as present and context-dependent.

```
M1003 S<0|1>
```

| S | Description |
|---|-------------|
| 0 | Disable power loss recovery |
| 1 | Enable power loss recovery |

---

### M1007 — Keep Enabled (Bambu) ⚠️
```
M1007 S1
```
Seen at end of A1 Mini start routine. Purpose: "bambu: keep enabled." Likely keeps some subsystem active. Not further documented.

---

### M9833.2 — Set Noise Parameters (Bambu) ⚠️
```
M9833.2
```
Sets internal noise parameters. Seen in A1 Mini start routine with comment "bambu: set noise params." No further documentation.

---

## Print Metadata & Job Control

### M1001 — Start of Print Metadata Block (Bambu)
Marks beginning of job metadata (job ID, print name, etc.). Parameters not documented in the source material.

> Listed as "invalid test/sub-test case!" in undocumented section — may have a secondary or error state form.

---

### M1002 — See [Print Progress & Display](#print-progress--display) above

---

### G39.4 — Quick Build Plate Detection (Bambu) ⚠️
```
G39.4
```
Bambu-specific quick build plate type detection. Seen inside M622 conditional block in A1 Mini routine. Not further documented.

---

## Undocumented / Experimental

> **Warning:** These commands are poorly documented or completely undocumented. Use with extreme caution. Some may cause undefined behavior or damage.

| Command | Notes |
|---------|-------|
| `G29.4 S0/S1` | Toggles "high freq z comp" — toggle high frequency Z compensation |
| `G29.5` | Returns "G29.5 failed: invalid params" — unknown purpose |
| `G29.6`, `G29.7`, `G29.8` | Appears to run normal bed probing sequence |
| `M963` | "sequence dismatch!" error seen — internal calibration state machine |
| `M964` | `NO_NEW_EXTRIN_CALI_PARA` — no new extrinsic calibration parameters |
| `M966` | Unknown |
| `M967` | Unknown |
| `M969 S1 N3 A2000` | Unknown — possibly motor/resonance related |
| `M980.3 A B C D E F G H I J K` | Unknown — large parameter set, possibly calibration matrix |
| `M1005` | See [Skew Calibration](#m1005----skew-calibration--conflict) — documented conflict |
| `G92.1 E0` | Reset extruder to machine coordinates — caution |
| `M2000–M2010` | Material tuning parameters (retraction, flow, accel, cooling, pressure advance, min/max flow) — not individually documented |

---

## Useful Coordinates (X1 Series)

| Location | X | Y | Z |
|----------|---|---|---|
| Center of build plate | 128 | 128 | — |
| LiDAR reference grid | 240 | 90 | 8 |
| Print finished position | 65 | 260 | 10 |
| Bed tramming screw 1 | 134.8 | 242.8 | — |
| Bed tramming screw 2 | 33.2 | 13.2 | — |
| Bed tramming screw 3 | 222.8 | 13.2 | — |
| Nozzle wipe tab | 135 | 253 | — |
| Purge line start | 18 | 1.0 | 0.8 |
| Service area edge | 0 | — | — |
| Wiper position | -13.5 | — | — |

**Z offsets:**
- Textured PEI plate: −0.04 mm
- Default: 0 mm

---

## Conflict Index

| Conflict | Commands | Description |
|----------|----------|-------------|
| 🔴 Flow rate vs soft endstops | M221 S vs M221 X/Y/Z | `M221 S<n>` sets flow %; `M221 X/Y/Z` controls soft endstops. Same command, completely different functions. |
| 🔴 Bed leveling vs power loss recovery | M1003 | Documented as bed leveling in one source, power loss recovery toggle in another. |
| 🔴 Job end vs LCD action | M1002 | Listed as "end of print metadata block" in one source; documented as LCD status, conditionals, speed level in X1Plus wiki. X1Plus is authoritative. |
| 🔴 Skew calibration vs unknown | M1005 | Listed as "unknown, likely state logging" in Bambu table; fully documented as skew calibration in X1Plus wiki. X1Plus is authoritative. |
| ⚠️ XY movement in relative mode | G91 + G0/G1 | Relative mode is ignored for XY axes when sending via gcode_line MQTT API. Commands are always treated as absolute. Z works correctly. |
| ⚠️ M106 without P | M106 | Without a P parameter specifying the fan (P1/P2/P3), M106 is silently ignored on Bambu printers. |
| ⚠️ XY compensation | M290 vs M290.2 | Both documented with identical descriptions. Distinction unclear. |
