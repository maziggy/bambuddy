# Macro System

Macros let you automate printer actions by writing simple scripts. Each macro is a plain-text [Jinja2](https://jinja.palletsprojects.com/) template stored as a `.jinja2` file on disk.

---

## What are macros?

A macro is a script that runs a sequence of commands against a printer. Commands can be:
- Standard G-code (from an approved whitelist)
- Built-in system commands (`AMS_DRYING`, `NOTIFY`, `WAIT`, etc.)
- Jinja2 template logic (conditionals, loops, variables)
- Calls to other macros

Macro files live in `data/macros/` (or wherever `DATA_DIR` points). You can edit them directly on disk or through the UI.

---

## Writing your first macro

```jinja2
{# Heat bed and notify when ready #}
M140 S60
WAIT_FOR_TEMP --target=60 --tolerance=2
NOTIFY --message="Bed is ready!"
```

Save this as a macro named `heat_bed` through the UI, or place it at `data/macros/heat_bed.jinja2`.

---

## Context variables

These variables are available in every macro script at render time:

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

**Example: conditional preheat**
```jinja2
{% if printer.bed_temp < 50 %}
M140 S60
WAIT_FOR_TEMP --target=60 --tolerance=3
{% endif %}
G28
```

---

## System commands reference

| Command | Arguments | Description |
|---|---|---|
| `AMS_DRYING` | `--ams=N --temp=T --duration=H` | Dry AMS slot N at T°C for H hours |
| `PRINTER_PAUSE` | _(none)_ | Pause the current print |
| `PRINTER_RESUME` | _(none)_ | Resume a paused print |
| `PRINTER_STOP` | _(none)_ | Stop the current print |
| `NOTIFY` | `--message="text"` | Send a notification via all configured providers |
| `WAIT` | `--seconds=N` | Wait N seconds (max 300) |
| `WAIT_FOR_TEMP` | `--target=T --tolerance=D --max_wait=S` | Wait until nozzle reaches T±D°C, timeout after S seconds (default 300) |

**Example: dry then notify**
```jinja2
AMS_DRYING --ams=0 --temp=65 --duration=4
NOTIFY --message="Drying started for AMS slot 0"
```

---

## Approved G-code commands

Only the following G-code commands are allowed in macros. Any other G-code is logged as a warning and ignored.

| Command | Description |
|---|---|
| `G0`, `G1` | Linear move |
| `G28` | Home all axes |
| `G90` | Absolute positioning |
| `G91` | Relative positioning |
| `G92` | Set position |
| `M82` | Absolute extruder mode |
| `M83` | Relative extruder mode |
| `M84` | Disable steppers |
| `M104` | Set nozzle temperature (no wait) |
| `M109` | Set nozzle temperature (wait) |
| `M140` | Set bed temperature (no wait) |
| `M190` | Set bed temperature (wait) |
| `M106` | Set fan speed |
| `M107` | Fan off |
| `T0`–`T3` | Select tool/extruder |

**Comments** (lines starting with `#` or `;`) and **blank lines** are always ignored.

---

## Calling other macros

A macro can invoke another macro by name using the `run_macro()` function:

```jinja2
{# Run a sub-macro inline #}
{{ run_macro("preheat_bed") }}
G28
NOTIFY --message="Print ready!"
```

The sub-macro is resolved by its **name** field (as stored in the database). Its commands are executed inline within the parent run's log.

### Cycle detection

If macro A calls macro B which calls macro A again, the runner detects the cycle and stops with an error logged to the run output. Infinite recursion is not possible.

---

## Trigger types

Each macro has a **trigger type** that controls when it runs:

### Manual
Run on demand from the UI ("Run Now" button) or via the REST API:
```
POST /api/v1/macros/{id}/run
Content-Type: application/json
Authorization: Bearer <your-jwt-token>

{"printer_id": 1}
```

### Webhook
Triggered by an external HTTP call using an API key:
```
POST /api/v1/webhook/macro/{id}/run
Authorization: Bearer <api-key>
Content-Type: application/json

{"printer_id": 1}
```

The webhook URL is shown in the macro's Settings tab. API keys need the **"macros" permission** (`can_run_macros = true`) to call this endpoint.

### Schedule
Runs automatically on a cron schedule. Enter a standard 5-field cron expression:

```
┌──── minute (0-59)
│ ┌── hour (0-23)
│ │ ┌─ day of month (1-31)
│ │ │ ┌ month (1-12)
│ │ │ │ ┌ day of week (0-6, Sun=0)
* * * * *
```

Examples:
- `0 8 * * 1-5` — Every weekday at 8:00 AM
- `*/30 * * * *` — Every 30 minutes
- `0 20 * * *` — Every day at 8:00 PM

The scheduler checks every 60 seconds, so the actual fire time may be up to 60 seconds late.

---

## Embedding macros in G-code files

You can embed macro trigger calls directly in `.gcode` files inside a `.3mf` archive using a special comment syntax:

```gcode
; --- start of print ---
G28 ; home axes
; MACRO: notify_print_started
G0 Z5
```

### How it works

When Bambuddy archives a print (at print start), it scans the G-code for `; MACRO: name` comment lines. Any macros found are triggered **after archiving completes**, with the macro identified by its **name** field.

### Important constraints

Because Bambu Lab printers execute G-code autonomously (the firmware owns the print stream), embedded macros **cannot** interact with the printer mid-print. The following commands are **blocked** when a macro is triggered from a G-code embed:

- All whitelisted G-code commands (`G28`, `M104`, etc.)
- `AMS_DRYING`
- `PRINTER_PAUSE` / `PRINTER_RESUME` / `PRINTER_STOP`
- `WAIT_FOR_TEMP`

The following commands **are allowed** in embedded macros:
- `NOTIFY` — send a notification
- `WAIT` — delay (side-effect only, does not affect print)
- Calls to other macros (subject to the same restrictions)

This design is intentional: embedded macros are for **observing and reacting** to print events, not controlling the printer.

**Example — notify when a print starts:**
```gcode
; MACRO: on_print_started
```

Where `on_print_started` is a macro with script:
```jinja2
NOTIFY --message="Print has started!"
```

---

## Tips & gotchas

- **Macro names are identifiers.** They are slugified (spaces → underscores, lowercase) when the file is created. Use consistent names when calling sub-macros.
- **Jinja2 sandbox.** The sandbox blocks access to Python builtins (`open`, `os`, `__import__`, etc.). Standard Jinja2 filters (`|int`, `|upper`, `|default`, etc.) and control flow (`{% if %}`, `{% for %}`) work normally.
- **WAIT is capped at 300 seconds.** Longer waits are silently capped to prevent runaway executions.
- **WAIT_FOR_TEMP has a max_wait of 300s** by default. Override with `--max_wait=600` if needed.
- **Printer context is empty if no printer is targeted.** A macro with no `printer_id` (neither stored nor passed at run time) will have `printer = {}` in context. Guard with `{% if printer %}` if the macro might run without a printer.
- **Sub-macro runs share the same log** only in terms of causation — they each create their own log if they have a `MacroRun` record. Sub-macros called via `{{ run_macro() }}` in Jinja2 templates execute as background tasks and may not appear in the parent run's log.
- **Files on disk are the source of truth** for script content. The database holds metadata only. You can edit `.jinja2` files directly, but changes won't show in the UI until the macro record's `updated_at` is refreshed (re-save via UI or API).
