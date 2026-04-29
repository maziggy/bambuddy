# Macro System Functions Reference

This document lists every system function available in macro bodies and Jinja2 context,
plus all the capabilities that can be added as integrations. Functions marked **[built-in]**
are already registered and work today. Functions marked **[integration]** exist in Bambuddy
but need a file in `backend/app/services/macro_integrations/` to be exposed.

---

## How commands work in a macro body

```cfg
[macro example]
COMMAND_NAME --arg1=value --arg2=value
```

Commands are dispatched after Jinja2 rendering. Flags use `--key=value` syntax.
Some flags have short aliases (`--seconds` → `-s`). Arguments marked **required\*** must be provided.

## How context variables work

Context variables are injected into the template before rendering. Access them with `{{ var }}`.
Functions that provide a context variable run once per macro execution.

```cfg
[macro example]
{% if printer.nozzle_temp > 200 %}
NOTIFY --message="Nozzle is hot"
{% endif %}
```

---

## Built-in commands

### `NOTIFY` [built-in]
Send a notification through all enabled providers (Telegram, Discord, Email, etc.).

```cfg
NOTIFY --message="Bed is ready!"
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--message` / `--m` | yes* | — | Text to send |

Allowed in embedded macros: **yes**

---

### `SET_VAR` [built-in — vars]
Persist a value under a named key. Survives macro runs, scheduler ticks, and server restarts.

```cfg
SET_VAR --key=last_material --value="{{ assignments[0].material }}"
SET_VAR --key=cooldown_notified --value=true --ttl=86400
SET_VAR --key=run_count --value=0 --scope=macro
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--key` | yes* | — | Variable name (any string) |
| `--value` | yes* | — | Value to store — numbers, booleans, and JSON arrays/objects are preserved as-is; anything else stored as a string |
| `--ttl` | no | permanent | Time-to-live in seconds; expired vars are invisible and pruned hourly |
| `--scope` | no | `global` | `global` — shared across all macros; `macro` — isolated to this macro |

Allowed in embedded macros: **yes**

---

### `DELETE_VAR` [built-in — vars]
Delete a persisted variable by key.

```cfg
DELETE_VAR --key=cooldown_notified
DELETE_VAR --key=run_count --scope=macro
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--key` | yes* | — | Variable name to delete |
| `--scope` | no | `global` | Must match the scope used when setting |

Allowed in embedded macros: **yes**

---

### `WAIT` [built-in]
Pause macro execution for N seconds.

```cfg
WAIT --seconds=30
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--seconds` / `--s` | yes* | `1` | Duration (capped at 300 s) |

Allowed in embedded macros: **yes**

---

### `WAIT_FOR_TEMP` [built-in]
Block until the nozzle reaches the target temperature, or until timeout.

```cfg
WAIT_FOR_TEMP --target=200 --tolerance=5 --max_wait=300
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--target` | yes* | — | Target nozzle temperature °C |
| `--tolerance` | no | `5` | Acceptable deviation ±°C |
| `--max_wait` | no | `300` | Timeout in seconds (capped at 600) |

Requires printer: **yes** — Allowed in embedded macros: **no**

---

### `AMS_DRYING` [built-in]
Start a filament drying cycle on an AMS unit.

```cfg
AMS_DRYING --ams=0 --temp=65 --duration=4
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--ams` / `--a` | no | `0` | AMS unit index (0–3) |
| `--temp` / `--t` | no | `45` | Temperature °C |
| `--duration` / `--d` | no | `4` | Duration in hours |

Requires printer: **yes** — Allowed in embedded macros: **no**

---

### `PRINTER_PAUSE` [built-in]
Pause the current print.

```cfg
PRINTER_PAUSE
```

Requires printer: **yes** — Allowed in embedded macros: **no**

---

### `PRINTER_RESUME` [built-in]
Resume a paused print.

```cfg
PRINTER_RESUME
```

Requires printer: **yes** — Allowed in embedded macros: **no**

---

### `PRINTER_STOP` [built-in]
Stop and cancel the current print.

```cfg
PRINTER_STOP
```

Requires printer: **yes** — Allowed in embedded macros: **no**

---

### `CLEAR_HMS_ERRORS` [built-in — printer_extended]
Clear active HMS/print errors on the printer (equivalent to pressing "Confirm" on the error screen).

```cfg
CLEAR_HMS_ERRORS
```

Requires printer: **yes** — Allowed in embedded macros: **no**

---

### `PRINT_QUEUE_ADD` [built-in — printer_extended]
Add a library file to the print queue for the target printer.

```cfg
PRINT_QUEUE_ADD --file_id=42
PRINT_QUEUE_ADD --file_id=42 --plate=2
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--file_id` | yes* | — | Library file ID to enqueue |
| `--plate` | no | `1` | Plate number for multi-plate 3MF files |

Requires printer: **yes** — Allowed in embedded macros: **no**

---

### `ASSIGN_SPOOL` [built-in — assignments]
Assign a spool from inventory to an AMS tray on the target printer. Records the assignment in the DB; does not send an MQTT reconfigure command (that happens automatically on the next AMS status push).

```cfg
ASSIGN_SPOOL --spool_id=7 --ams=0 --tray=2
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--spool_id` | yes* | — | Spool ID from inventory |
| `--ams` | yes* | — | AMS unit index (0–3, 255 = external spool) |
| `--tray` | yes* | — | Tray slot index (0–3) |

Requires printer: **yes** — Allowed in embedded macros: **no**

---

### `UNASSIGN_SPOOL` [built-in — assignments]
Remove the spool assignment from a specific AMS tray.

```cfg
UNASSIGN_SPOOL --ams=0 --tray=2
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--ams` | yes* | — | AMS unit index |
| `--tray` | yes* | — | Tray slot index |

Requires printer: **yes** — Allowed in embedded macros: **no**

---

## Built-in context variables

These are always available in the Jinja2 template when a printer is targeted.

| Variable | Type | Description |
|---|---|---|
| `printer.state` | string | `RUNNING`, `IDLE`, `PAUSE`, `FINISH`, `FAILED`, etc. |
| `printer.connected` | bool | Whether the printer is online |
| `printer.nozzle_temp` | float | Current nozzle temperature °C |
| `printer.bed_temp` | float | Current bed temperature °C |
| `printer.progress` | float | Print progress 0–100 |
| `printer.layer` | int | Current layer number |
| `printer.total_layers` | int | Total layers in the current print |
| `printer.current_print` | string\|null | Filename of the active print job |
| `ams` | list | Raw AMS unit data from MQTT |
| `queue` | int | Items in the print queue |
| `hms_errors` | list | Active HMS errors — each entry has `code`, `severity` (1–4), `message`, `module` |
| `assignments` | list | Current AMS spool assignments — each entry has `ams_id`, `tray_id`, `spool_id`, `material`, `color`, `brand` |
| `vars` | dict | All non-expired persisted variables (global + this macro's scoped vars). Scoped vars shadow globals with the same key. |

---

## Integrations available to add

The following capabilities exist in Bambuddy services and can each be exposed by creating a
file in `backend/app/services/macro_integrations/`. Each section shows the recommended
command names, flags, and context variables.

---

### Printer — extended control

**File:** `macro_integrations/printer_extended.py`
**Service:** `printer_manager` → `get_client(printer_id)`

#### `SET_BED_TEMP`
Set the bed target temperature without waiting.

```cfg
SET_BED_TEMP --target=60
```
`--target`* — temperature °C. Requires printer.

#### `SET_NOZZLE_TEMP`
Set the nozzle target temperature without waiting.

```cfg
SET_NOZZLE_TEMP --target=220
```
`--target`* — temperature °C. `--nozzle` — nozzle index (default `0`). Requires printer.

#### `SET_CHAMBER_TEMP`
Set the chamber target temperature.

```cfg
SET_CHAMBER_TEMP --target=40
```
`--target`* — temperature °C. Requires printer.

#### `SET_PRINT_SPEED`
Change the active print speed profile.

```cfg
SET_PRINT_SPEED --mode=2
```
`--mode`* — `1` silent · `2` standard · `3` sport · `4` ludicrous. Requires printer.

#### `SET_FAN`
Set a specific fan to a speed percentage.

```cfg
SET_FAN --fan=1 --speed=80
```
`--fan`* — fan index. `--speed`* — 0–100%. Requires printer.

#### `CHAMBER_LIGHT`
Turn the chamber light on or off.

```cfg
CHAMBER_LIGHT --on=true
```
`--on`* — `true` or `false`. Requires printer.

#### `AMS_LOAD`
Load filament from an AMS tray.

```cfg
AMS_LOAD --tray=0
```
`--tray`* — AMS tray index. `--extruder` — extruder index. Requires printer.

#### `AMS_UNLOAD`
Unload the current filament.

```cfg
AMS_UNLOAD
```
Requires printer.

#### `HOME_AXES`
Home one or more axes.

```cfg
HOME_AXES --axes=XYZ
```
`--axes` — axes string (default `XYZ`). Requires printer.

#### `WAIT_FOR_COOLDOWN`
Wait until the nozzle cools below a target temperature.

```cfg
WAIT_FOR_COOLDOWN --target=50 --timeout=600
```
`--target` — temperature °C (default `50`). `--timeout` — seconds (default `600`). Requires printer.

#### `printer` context variable
Extend the existing `printer` context with chamber temperature and AMS tray details.

```cfg
{% if printer.chamber_temp > 45 %}
SET_CHAMBER_TEMP --target=35
{% endif %}
```

Additional fields: `printer.chamber_temp`, `printer.speed_mode`, `printer.fan_speeds`,
`printer.ams_trays` (list of tray dicts with `tray_id`, `color`, `material`, `remain`).

---

### Smart plugs

**File:** `macro_integrations/smart_plugs.py`
**Services:** `smart_plug_manager`, `homeassistant`, `tasmota`, `rest_smart_plug`

#### `PLUG_ON`
Turn a smart plug on by its name or ID.

```cfg
PLUG_ON --name="Printer PSU"
```
`--name`* — plug display name as configured in Bambuddy. `--id` — plug DB id (alternative).

#### `PLUG_OFF`
Turn a smart plug off.

```cfg
PLUG_OFF --name="Printer PSU"
```
`--name`* — plug display name. `--id` — plug DB id (alternative).

#### `PLUG_TOGGLE`
Toggle a smart plug state.

```cfg
PLUG_TOGGLE --name="Printer PSU"
```

#### `plugs` context variable
Inject current state of all smart plugs.

```cfg
{% for plug in plugs %}
; {{ plug.name }} is {{ plug.state }}
{% endfor %}
```
`plugs` — list of dicts: `name`, `state` (`ON`/`OFF`/`unknown`), `power_w`, `energy_today_kwh`.

---

### Spoolman

**File:** `macro_integrations/spoolman.py`
**Service:** `spoolman` (via `get_spoolman_client()`)

#### `SPOOL_USE`
Record filament consumption against a spool.

```cfg
SPOOL_USE --spool_id=42 --grams=15.5
```
`--spool_id`* — Spoolman spool ID. `--grams`* — weight used in grams.

#### `SPOOL_UPDATE_LOCATION`
Update the location field of a spool.

```cfg
SPOOL_UPDATE_LOCATION --spool_id=42 --location="AMS1-Slot2"
```
`--spool_id`* — spool ID. `--location`* — new location string.

#### `spools` context variable
Inject all spools from Spoolman into the template.

```cfg
{% for spool in spools %}
; {{ spool.filament.name }} — {{ spool.remaining_weight }}g remaining
{% endfor %}
```
`spools` — list of Spoolman spool objects. Returns `[]` if Spoolman is unreachable.

#### `ams_spools` context variable
Inject current AMS tray → spool mappings.

```cfg
{% set tray = ams_spools[0] %}
; Slot 0: {{ tray.material }} {{ tray.color }}
```
`ams_spools` — list indexed by global tray id; each entry has `spool_id`, `material`,
`color`, `remain`, `tag_uid`.

---

### Print queue

**File:** `macro_integrations/queue.py`
**Service:** `print_scheduler` + DB queries

#### `queue` context variable (extended)
The existing `queue` variable (integer count) can be replaced with a richer object.

```cfg
{% if queue.pending > 0 %}
NOTIFY --message="There are {{ queue.pending }} jobs waiting."
{% endif %}
```
`queue.pending` — jobs waiting. `queue.running` — jobs currently printing.
`queue.next_job` — dict with `filename`, `printer_id`, `estimated_minutes`.

---

### Print archive

**File:** `macro_integrations/archive.py`
**Service:** DB query on `PrintArchive`

#### `last_print` context variable
Inject metadata about the most recent completed print for the targeted printer.

```cfg
{% if last_print %}
; Last print: {{ last_print.filename }} — {{ last_print.filament_used_g }}g
{% endif %}
```
`last_print` — dict: `filename`, `status`, `filament_used_g`, `print_time_s`,
`finished_at`, `failure_reason`. `None` if no history.

---

### Home Assistant

**File:** `macro_integrations/homeassistant.py`
**Service:** `homeassistant_service`

#### `HA_CALL_SERVICE`
Call a Home Assistant service (e.g. turn on a switch, trigger an automation).

```cfg
HA_CALL_SERVICE --domain=switch --service=turn_on --entity_id=switch.printer_room_light
```
`--domain`* — HA domain (`switch`, `light`, `automation`, etc.).
`--service`* — service name (`turn_on`, `turn_off`, `trigger`, etc.).
`--entity_id`* — full HA entity ID.

#### `ha_states` context variable
Inject a dict of HA entity states into the template.

```cfg
{% set room_temp = ha_states["sensor.printer_room_temperature"].state | float %}
{% if room_temp > 35 %}
NOTIFY --message="Room is too hot: {{ room_temp }}°C"
{% endif %}
```
`ha_states` — dict keyed by entity_id; values are dicts with `state`, `attributes`.
Only populated if Home Assistant is configured and reachable. Requires specifying which
entities to fetch (configured per integration file).

---

### Failure analysis

**File:** `macro_integrations/failure_analysis.py`
**Service:** `failure_analysis`

#### `failure_stats` context variable
Inject recent failure statistics into the template.

```cfg
{% if failure_stats.rate_7d > 20 %}
NOTIFY --message="High failure rate this week: {{ failure_stats.rate_7d }}%"
{% endif %}
```
`failure_stats` — dict: `rate_7d` (failure % last 7 days), `rate_30d`, `top_reason`,
`total_prints_7d`, `total_failures_7d`. Returns empty dict if no data.

---

### MQTT relay (external publish)

**File:** `macro_integrations/mqtt_relay.py`
**Service:** `mqtt_relay`

#### `MQTT_PUBLISH`
Publish a message to the external MQTT relay broker.

```cfg
MQTT_PUBLISH --topic="bambuddy/events/macro" --payload='{"event":"done"}'
```
`--topic`* — MQTT topic string. `--payload`* — message payload string.
`--qos` — QoS level `0`/`1`/`2` (default `0`). No-op if relay is not configured.

---

## Implementation guide

To expose any integration, create a file in `backend/app/services/macro_integrations/`:

```python
# backend/app/services/macro_integrations/smart_plugs.py
from backend.app.services.macro_functions import ArgSpec, FunctionContext, FunctionResult, macro_function

@macro_function(
    name="PLUG_ON",
    description="Turn a smart plug on by name.",
    args={"name": ArgSpec("Plug display name", required=True)},
    requires_printer=False,
    allowed_in_embed=True,
)
async def _plug_on(ctx: FunctionContext) -> FunctionResult:
    from backend.app.services.smart_plug_manager import smart_plug_manager
    from backend.app.core.database import async_session
    from sqlalchemy import select
    from backend.app.models.smart_plug import SmartPlug

    name = ctx.flags.get("name", "").strip('"\'')
    async with async_session() as db:
        result = await db.execute(select(SmartPlug).where(SmartPlug.name == name))
        plug = result.scalar_one_or_none()
    if not plug:
        msg = f"[PLUG_ON] Plug '{name}' not found\n"
        await ctx.log(ctx.run_id, msg)
        return FunctionResult(ok=False, message=msg)
    # ... call service to turn on
    return FunctionResult(ok=True)
```

The function is auto-discovered at startup — no other files need to change.
