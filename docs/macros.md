# Macro System

Macros let you automate printer actions by writing simple scripts. Macros are defined as named blocks inside `.cfg` files stored on disk — similar to Klipper's macro format. One `.cfg` file can contain multiple macros.

---

## File format

Macros live in `.cfg` files inside `data/macros/`. Each file can contain any number of macro blocks:

```cfg
[macro preheat_bed]
description: Heat bed to 60°C and wait
trigger: manual
printer: My X1C
M140 S60
WAIT_FOR_TEMP --target=60 --tolerance=2
NOTIFY --message="Bed is ready!"

[macro daily_purge]
description: Run a short purge move every morning
trigger: schedule
cron: 0 8 * * *
G28
G1 E30 F200
G92 E0
```

### Block structure

A block starts with `[macro name]`, where `name` is the macro's identifier. Everything between two block headers (or between the last header and end of file) belongs to that macro.

The **config section** comes first — a sequence of `key: value` lines at the top of the block. Recognised keys:

| Key | Required | Values | Description |
|---|---|---|---|
| `description` | no | any text | Human-readable description shown in the UI |
| `trigger` | no | `manual` · `webhook` · `schedule` | When the macro runs (default: `manual`) |
| `cron` | if `trigger: schedule` | 5-field cron expression | Schedule for automatic execution |
| `printer` | no | printer name (exact match) | Target printer; must match the printer's name in Bambuddy |

Config lines end as soon as the first non-blank, non-comment line that isn't a `key: value` pair is encountered. Everything after that is the **body** — the commands to execute.

Lines starting with `;` or `#` are comments and are ignored during execution.

---

## Body commands

The body is a [Jinja2](https://jinja.palletsprojects.com/) template. It is rendered first (injecting live printer context), then each resulting line is dispatched as a command.

### Context variables

| Variable | Type | Description |
|---|---|---|
| `printer.state` | string | Current state: `RUNNING`, `IDLE`, `PAUSE`, `FINISH`, etc. |
| `printer.connected` | bool | Whether the printer is connected |
| `printer.nozzle_temp` | float | Current nozzle temperature (°C) |
| `printer.bed_temp` | float | Current bed temperature (°C) |
| `printer.progress` | float | Print progress 0–100 |
| `printer.layer` | int | Current layer number |
| `printer.total_layers` | int | Total layers in current print |
| `printer.current_print` | string\|null | Filename of current print |
| `ams` | list | List of AMS unit data dicts (raw from MQTT) |
| `queue` | int | Number of items currently in the print queue |

**Example — conditional preheat:**
```cfg
[macro conditional_heat]
{% if printer.bed_temp < 50 %}
M140 S60
WAIT_FOR_TEMP --target=60 --tolerance=3
{% endif %}
G28
```

### System commands

| Command | Arguments | Description |
|---|---|---|
| `AMS_DRYING` | `--ams=N --temp=T --duration=H` | Dry AMS slot N at T°C for H hours |
| `PRINTER_PAUSE` | _(none)_ | Pause the current print |
| `PRINTER_RESUME` | _(none)_ | Resume a paused print |
| `PRINTER_STOP` | _(none)_ | Stop the current print |
| `NOTIFY` | `--message="text"` | Send a notification via all configured providers |
| `WAIT` | `--seconds=N` | Wait N seconds (max 300) |
| `WAIT_FOR_TEMP` | `--target=T --tolerance=D --max_wait=S` | Wait until nozzle reaches T±D°C, timeout after S seconds (default 300) |

### Approved G-code commands

Only the following G-code commands are forwarded to the printer. Any other G-code is logged as a warning and ignored.

| Command(s) | Description |
|---|---|
| `G0`, `G1` | Linear move |
| `G2`, `G3` | Arc move |
| `G4` | Dwell / delay (max 90 s per call) |
| `G28` | Auto home |
| `G29`, `G29.1`, `G29.2` | Bed mesh calibration / Z offset |
| `G90`, `G91` | Absolute / relative positioning |
| `G92` | Set current position |
| `G380` | Guarded Z move (Bambu) |
| `G392` | Clog detection toggle (Bambu) |
| `M17` | Enable steppers |
| `M18`, `M84` | Disable steppers |
| `M73`, `M73.2` | Set / reset print progress display |
| `M82`, `M83` | Absolute / relative extruder mode |
| `M104`, `M109` | Set nozzle temperature |
| `M106`, `M107` | Fan speed / fan off |
| `M140`, `M190` | Set bed temperature |
| `M142` | Aux fan / chamber temp (X1C/X1E) |
| `M201`, `M203`, `M204`, `M204.2`, `M205` | Motion limits (accel, feed rate, jerk) |
| `M211` | Soft endstops |
| `M220`, `M221` | Feed rate / flow rate override |
| `M290`, `M290.2` | XY compensation |
| `M302` | Cold extrusion toggle |
| `M400` | Wait for moves to finish |
| `M412` | Filament runout detection |
| `M500` | Save to EEPROM |
| `M620`, `M620.1`, `M620.3`, `M621`, `M622`, `M623`, `M630` | AMS filament control |
| `M900` | Pressure advance |
| `M960` | LED / laser toggle |
| `M970`, `M970.3`, `M973`, `M974`, `M975` | Vibration / camera / noise cancellation |
| `M981`, `M982`, `M982.2`, `M982.4` | Spaghetti / motor noise detection |
| `M991` | Layer change notification / timelapse |
| `M1002`, `M1003`, `M1005`, `M1006`, `M1007` | LCD, bed levelling, skew, buzzer, subsystem |
| `T0`–`T3` | Select AMS tray |
| `T255`, `T1000`, `T1100` | Switch to empty / local / scanning tool |

---

## Calling other macros

A macro body can invoke another macro by name using `run_macro()`:

```cfg
[macro print_ready]
{{ run_macro("preheat_bed") }}
G28
NOTIFY --message="Print ready!"
```

The named macro's commands execute inline within the parent run's log. If the named macro cannot be found, a warning is logged and execution continues.

### Cycle detection

If macro A calls macro B which calls macro A again, the runner detects the cycle and stops with an error. Infinite recursion is not possible.

---

## Trigger types

The trigger is set via the `trigger:` config line in the file — not through the UI.

### `trigger: manual` (default)

Run on demand from the UI (Run button) or via the REST API:

```
POST /api/v1/macros/{id}/run
Authorization: Bearer <your-jwt-token>
Content-Type: application/json

{"printer_id": 1}
```

### `trigger: webhook`

Triggered by an external HTTP call using an API key. The URL is shown in the run history panel in the UI.

```
POST /api/v1/webhook/macro/{id}/run
Authorization: Bearer <api-key>
Content-Type: application/json

{"printer_id": 1}
```

### `trigger: schedule`

Runs automatically on a cron schedule. Set the schedule with the `cron:` config line:

```cfg
[macro morning_purge]
trigger: schedule
cron: 0 8 * * 1-5
G28
G1 E20 F200
G92 E0
```

Standard 5-field cron syntax:
```
┌──── minute (0-59)
│ ┌── hour (0-23)
│ │ ┌─ day of month (1-31)
│ │ │ ┌ month (1-12)
│ │ │ │ ┌ day of week (0-6, Sun=0)
* * * * *
```

The scheduler checks every 60 seconds, so actual fire time may be up to 60 seconds late.

---

## Managing files in the UI

The Macros page shows a list of `.cfg` files on the left. Selecting a file shows all macro blocks defined in it on the right.

- **New file** — creates a blank `.cfg` file, or upload an existing one
- **Edit file** — opens a text editor with syntax highlighting and a hints panel (context variables, system commands, G-code whitelist, available macros)
- **Download / Upload** — in the editor header; upload replaces the current content in the editor (save to persist)
- **Delete file** — removes the file and all its macros and their run history permanently

Each macro row shows its trigger badge (showing the cron expression for scheduled macros), a last-run status icon, a history button that expands the run log inline, and a run button.

---

## Embedding macros in G-code files

You can embed macro triggers directly in `.gcode` files inside a `.3mf` archive:

```gcode
; MACRO: notify_print_started
G28
```

When Bambuddy archives a print, it scans the G-code for `; MACRO: name` lines and fires those macros after archiving completes.

### Constraints for embedded macros

Because Bambu Lab printers execute G-code autonomously, embedded macros **cannot** interact with the printer. The following are **blocked**:

- All whitelisted G-code commands
- `AMS_DRYING`, `PRINTER_PAUSE`, `PRINTER_RESUME`, `PRINTER_STOP`, `WAIT_FOR_TEMP`

The following **are allowed**:

- `NOTIFY` — send a notification
- `WAIT` — delay (side-effect only)
- Calls to other macros (subject to the same restrictions)

---

## Tips & gotchas

- **Printer name must be exact.** The `printer:` value is matched case-sensitively against the printer's name in Bambuddy. If the name is not found, the macro runs without a targeted printer.
- **Trigger and cron are read from the file.** There is no settings UI for these — edit the `.cfg` file directly.
- **Saving the file re-syncs all macros.** Adding, renaming, or removing a `[macro name]` block takes effect immediately when you save. Removed blocks are permanently deleted along with their run history.
- **Jinja2 sandbox.** Blocks access to Python builtins (`open`, `os`, `__import__`, etc.). Standard filters (`|int`, `|upper`, `|default`) and control flow (`{% if %}`, `{% for %}`) work normally.
- **WAIT is capped at 300 seconds.** Longer values are silently clamped.
- **WAIT_FOR_TEMP defaults to 300 s timeout.** Override with `--max_wait=600` if needed.
- **Printer context is empty if no printer is targeted.** Guard with `{% if printer %}` if the macro might run without one.
- **The `.cfg` file is the source of truth.** You can edit files directly on disk; changes are picked up automatically at server startup or when the file is saved through the UI.
