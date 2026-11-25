"""Home Assistant MQTT discovery payloads for the MQTT relay (#3724).

The relay already publishes a retained status per printer; these payloads tell
Home Assistant how to read it, so a printer shows up as a device without any
YAML. One retained device discovery message per printer, and one per AMS unit
that names the printer as its ``via_device``. Read-only: no command topics.

Only the payloads are built here. Publishing, re-sending and clearing them is
the relay's job (``mqtt_relay.py``).
"""

import re

from backend.app.core.config import APP_VERSION

DEFAULT_DISCOVERY_PREFIX = "homeassistant"

# Every device this builds starts with it, which is also how the relay tells its
# own retained configs on the broker from another integration's.
DEVICE_ID_PREFIX = "bambuddy_"

_ORIGIN = {
    "name": "Bambuddy",
    "sw_version": APP_VERSION,
    "support_url": "https://github.com/maziggy/bambuddy",
}

# gcode_state as the firmware sends it -> the option Home Assistant shows.
_STATES = {
    "IDLE": "idle",
    "RUNNING": "printing",
    "PAUSE": "paused",
    "FINISH": "finished",
    "FAILED": "failed",
    "PREPARE": "preparing",
    "SLICING": "slicing",
}
_SPEED_LEVELS = {1: "silent", 2: "standard", 3: "sport", 4: "ludicrous"}

# Models with a door sensor (home_flag bit 23). Elsewhere door_open is always
# False, and a door that never opens is worse than no entity.
_DOOR_SENSOR_MODELS = {"X1", "X1C", "X1E", "X2D", "P2S"}
_DOOR_SENSOR_MODEL_PREFIXES = ("H2", "H3")

_AMS_MODELS = {"ams": "AMS", "n3f": "AMS 2 Pro", "n3s": "AMS HT"}
# Units with a dryer: only they count a drying time down.
_AMS_DRYER_MODULES = {"n3f", "n3s"}


def normalize_prefix(prefix: str | None, default: str = "") -> str:
    """A topic prefix without blanks or leading and trailing slashes."""
    return (prefix or "").strip().strip("/") or default


def printer_device_id(serial: str) -> str:
    """The discovery id of a printer; also its device identifier in Home Assistant."""
    return DEVICE_ID_PREFIX + re.sub(r"[^A-Za-z0-9_-]", "_", serial or "")


def ams_device_id(serial: str, ams_id: int) -> str:
    return f"{printer_device_id(serial)}_ams_{ams_id}"


def discovery_topic(discovery_prefix: str, device_id: str) -> str:
    return f"{discovery_prefix}/device/{device_id}/config"


def device_id_from_topic(discovery_prefix: str, topic: str) -> str | None:
    """The device id of one of our discovery topics, or None for any other topic."""
    head, tail = f"{discovery_prefix}/device/", "/config"
    if not topic.startswith(head) or not topic.endswith(tail):
        return None
    device_id = topic[len(head) : -len(tail)]
    if "/" in device_id or not device_id.startswith(DEVICE_ID_PREFIX):
        return None
    return device_id


def belongs_to_printer(device_id: str, serial: str) -> bool:
    """Whether a device id is this printer or one of its AMS units."""
    printer = printer_device_id(serial)
    return device_id == printer or device_id.startswith(f"{printer}_ams_")


def _has_door_sensor(model: str | None) -> bool:
    normalized = (model or "").strip().upper().replace(" ", "")
    return normalized in _DOOR_SENSOR_MODELS or normalized.startswith(_DOOR_SENSOR_MODEL_PREFIXES)


def _shared(topic_prefix: str, serial: str) -> dict:
    """What every component of a printer or AMS device has in common."""
    status_topic = f"{topic_prefix}/printers/{serial}/status"
    return {
        "origin": _ORIGIN,
        "state_topic": status_topic,
        # Both must hold: Bambuddy is running (its Last Will says when it is
        # not), and the printer is connected to it.
        "availability_mode": "all",
        "availability": [
            {"topic": f"{topic_prefix}/status", "value_template": "{{ value_json.status }}"},
            {
                "topic": status_topic,
                "value_template": "{{ 'online' if value_json.connected else 'offline' }}",
            },
        ],
    }


def _sensor(device_id: str, key: str, name: str | None, template: str, **options) -> tuple[str, dict]:
    return key, {
        "platform": "sensor",
        "unique_id": f"{device_id}_{key}",
        "name": name,
        "value_template": template,
        **options,
    }


def _binary_sensor(device_id: str, key: str, name: str, condition: str, **options) -> tuple[str, dict]:
    return key, {
        "platform": "binary_sensor",
        "unique_id": f"{device_id}_{key}",
        "name": name,
        "value_template": f"{{{{ 'ON' if {condition} else 'OFF' }}}}",
        **options,
    }


def _temperature(device_id: str, key: str, name: str, field: str) -> tuple[str, dict]:
    return _sensor(
        device_id,
        key,
        name,
        f"{{{{ value_json.temperatures.get('{field}') }}}}",
        device_class="temperature",
        unit_of_measurement="°C",
        state_class="measurement",
        suggested_display_precision=0,
    )


def _fan(device_id: str, key: str, name: str, field: str, enabled: bool = True) -> tuple[str, dict]:
    options: dict = {"unit_of_measurement": "%", "state_class": "measurement", "icon": "mdi:fan"}
    if not enabled:
        options["enabled_by_default"] = False
    return _sensor(device_id, key, name, f"{{{{ value_json.{field} }}}}", **options)


def _tray_options(status_topic: str, tray_expression: str) -> dict:
    """State and attributes of a filament slot: the type, with colour and remaining."""
    return {
        "value_template": f"{tray_expression}{{{{ t.get('tray_type') or 'empty' }}}}",
        "json_attributes_topic": status_topic,
        "json_attributes_template": (
            f"{tray_expression}"
            "{{ {'color': ('#' ~ t.get('tray_color')[:6]) if t.get('tray_color') else none, "
            "'remaining': t.get('remain'), 'brand': t.get('tray_sub_brands'), "
            "'name': t.get('tray_id_name')} | tojson }}"
        ),
        "icon": "mdi:printer-3d-nozzle",
    }


def _trigger(event_topic: str, trigger_type: str) -> dict:
    return {
        "platform": "device_automation",
        "automation_type": "trigger",
        "topic": event_topic,
        "type": trigger_type,
        "subtype": "printer",
    }


def build_printer_config(
    *,
    topic_prefix: str,
    serial: str,
    name: str,
    model: str | None,
    firmware_version: str | None,
    temperatures: dict | None,
    vt_tray: list[dict] | None,
    external_url: str | None = None,
) -> dict:
    """The device discovery payload of a printer.

    ``temperatures`` and ``vt_tray`` are the printer's current readings: they
    decide which optional entities it gets (second nozzle, external spools).
    """
    # Lazy: printer_manager imports the relay.
    from backend.app.services.printer_manager import supports_chamber_temp

    device_id = printer_device_id(serial)
    shared = _shared(topic_prefix, serial)
    status_topic = shared["state_topic"]
    events = f"{topic_prefix}/printers/{serial}"
    temperatures = temperatures or {}

    device: dict = {
        "identifiers": [device_id],
        "name": name,
        "manufacturer": "Bambu Lab",
        "serial_number": serial,
    }
    if model:
        device["model"] = model
    if firmware_version:
        device["sw_version"] = firmware_version
    if external_url:
        device["configuration_url"] = external_url

    components = dict(
        [
            # name None makes it the device's main entity: it carries the device name.
            _sensor(
                device_id,
                "state",
                None,
                f"{{{{ {_STATES!r}.get(value_json.state) }}}}",
                device_class="enum",
                options=list(_STATES.values()),
                icon="mdi:printer-3d",
            ),
            _sensor(device_id, "stage", "Stage", "{{ value_json.stg_cur_name }}", icon="mdi:progress-wrench"),
            _sensor(
                device_id,
                "progress",
                "Progress",
                "{{ value_json.progress }}",
                unit_of_measurement="%",
                state_class="measurement",
                suggested_display_precision=0,
                icon="mdi:progress-clock",
            ),
            _sensor(
                device_id,
                "remaining_time",
                "Remaining time",
                "{{ value_json.remaining_time }}",
                device_class="duration",
                unit_of_measurement="min",
            ),
            # To the minute, so it does not change with every status message.
            _sensor(
                device_id,
                "end_time",
                "End time",
                "{{ (now() + timedelta(minutes=value_json.remaining_time)).replace(second=0, microsecond=0).isoformat()"
                " if value_json.state == 'RUNNING' and value_json.remaining_time else none }}",
                device_class="timestamp",
            ),
            _sensor(device_id, "layer", "Layer", "{{ value_json.layer_num }}", icon="mdi:layers"),
            _sensor(device_id, "total_layers", "Total layers", "{{ value_json.total_layers }}", icon="mdi:layers"),
            _sensor(
                device_id,
                "job",
                "Job",
                "{{ value_json.subtask_name or value_json.current_print or none }}",
                icon="mdi:file-cad",
            ),
            _temperature(device_id, "nozzle_temperature", "Nozzle temperature", "nozzle"),
            _temperature(device_id, "nozzle_target_temperature", "Nozzle target temperature", "nozzle_target"),
            _temperature(device_id, "bed_temperature", "Bed temperature", "bed"),
            _temperature(device_id, "bed_target_temperature", "Bed target temperature", "bed_target"),
            _fan(device_id, "part_fan", "Part cooling fan", "cooling_fan_speed"),
            _fan(device_id, "aux_fan", "Auxiliary fan", "big_fan1_speed", enabled=False),
            _fan(device_id, "chamber_fan", "Chamber fan", "big_fan2_speed", enabled=False),
            _fan(device_id, "heatbreak_fan", "Heatbreak fan", "heatbreak_fan_speed", enabled=False),
            _sensor(
                device_id,
                "speed_level",
                "Speed level",
                f"{{{{ {_SPEED_LEVELS!r}.get(value_json.speed_level) }}}}",
                device_class="enum",
                options=list(_SPEED_LEVELS.values()),
                icon="mdi:speedometer",
            ),
            _sensor(
                device_id,
                "wifi_signal",
                "Wi-Fi signal",
                "{{ value_json.wifi_signal }}",
                device_class="signal_strength",
                unit_of_measurement="dBm",
                state_class="measurement",
                entity_category="diagnostic",
            ),
            _sensor(
                device_id,
                "hms_errors",
                "HMS errors",
                "{{ (value_json.hms_errors or []) | count }}",
                state_class="measurement",
                entity_category="diagnostic",
                json_attributes_topic=status_topic,
                json_attributes_template="{{ {'errors': value_json.hms_errors or []} | tojson }}",
                icon="mdi:alert-circle-outline",
            ),
            # Of everything here only this one must stay available while the
            # printer is offline, so it answers to Bambuddy's status alone.
            _binary_sensor(
                device_id,
                "online",
                "Online",
                "value_json.connected",
                device_class="connectivity",
                entity_category="diagnostic",
                availability=shared["availability"][:1],
            ),
            _binary_sensor(device_id, "problem", "Problem", "value_json.hms_errors", device_class="problem"),
            _binary_sensor(
                device_id, "chamber_light", "Chamber light", "value_json.chamber_light", device_class="light"
            ),
            _binary_sensor(
                device_id,
                "plate_needs_clearing",
                "Plate needs clearing",
                "value_json.awaiting_plate_clear",
                icon="mdi:tray-alert",
            ),
        ]
    )

    def add(component: tuple[str, dict]) -> None:
        components[component[0]] = component[1]

    if "nozzle_2" in temperatures:
        add(_temperature(device_id, "nozzle_2_temperature", "Nozzle 2 temperature", "nozzle_2"))
        add(_temperature(device_id, "nozzle_2_target_temperature", "Nozzle 2 target temperature", "nozzle_2_target"))
    if supports_chamber_temp(model):
        add(_temperature(device_id, "chamber_temperature", "Chamber temperature", "chamber"))
        add(_temperature(device_id, "chamber_target_temperature", "Chamber target temperature", "chamber_target"))
    if _has_door_sensor(model):
        add(_binary_sensor(device_id, "door", "Door", "value_json.door_open", device_class="door"))

    external = [tray for tray in (vt_tray or []) if isinstance(tray, dict) and "id" in tray]
    for index, tray in enumerate(external, start=1):
        tray_id = int(tray["id"])
        expression = (
            f"{{% set t = (value_json.vt_tray or []) | selectattr('id', 'eq', {tray_id}) | first | default({{}}) %}}"
        )
        add(
            (
                f"external_spool_{tray_id}",
                {
                    "platform": "sensor",
                    "unique_id": f"{device_id}_external_spool_{tray_id}",
                    "name": "External spool" if len(external) == 1 else f"External spool {index}",
                    **_tray_options(status_topic, expression),
                },
            )
        )

    # Events. The print topics differ only in their last level, so one entity
    # reads all three and takes the event type from the payload: only the two
    # endings carry a status.
    add(
        (
            "print_event",
            {
                "platform": "event",
                "unique_id": f"{device_id}_print_event",
                "name": "Print",
                "state_topic": f"{events}/print/+",
                "event_types": ["started", "completed", "failed"],
                "value_template": (
                    "{% set s = value_json.status | default('') %}"
                    "{{ {'event_type': 'started' if not s else ('completed' if s == 'completed' else 'failed'), "
                    "'filename': value_json.filename, 'subtask_name': value_json.subtask_name, "
                    "'status': s or 'started'} | tojson }}"
                ),
            },
        )
    )
    add(
        (
            "error_event",
            {
                "platform": "event",
                "unique_id": f"{device_id}_error_event",
                "name": "Printer error",
                "state_topic": f"{events}/error",
                "event_types": ["error"],
                "value_template": "{{ {'event_type': 'error', 'errors': value_json.errors} | tojson }}",
            },
        )
    )
    add(
        (
            "filament_low_event",
            {
                "platform": "event",
                "unique_id": f"{device_id}_filament_low_event",
                "name": "Filament low",
                "state_topic": f"{events}/filament/low",
                "event_types": ["low"],
                "value_template": (
                    "{{ {'event_type': 'low', 'slot': value_json.slot, "
                    "'remaining_percent': value_json.remaining_percent, 'color': value_json.color} | tojson }}"
                ),
            },
        )
    )
    # Device triggers for the automation editor, straight from the event topics.
    components["trigger_print_started"] = _trigger(f"{events}/print/started", "print_started")
    components["trigger_print_completed"] = _trigger(f"{events}/print/completed", "print_completed")
    components["trigger_print_failed"] = _trigger(f"{events}/print/failed", "print_failed")
    components["trigger_error"] = _trigger(f"{events}/error", "error")
    components["trigger_filament_low"] = _trigger(f"{events}/filament/low", "filament_low")

    return {"device": device, **shared, "components": components}


def ams_unit_name(unit: dict) -> str:
    """ "AMS 1", "AMS HT 1": the unit's number as the printer and the slicer count it."""
    ams_id = int(unit.get("id", 0))
    if unit.get("is_ams_ht"):
        return f"AMS HT {ams_id - 127}"
    return f"AMS {ams_id + 1}"


def build_ams_config(*, topic_prefix: str, serial: str, printer_name: str, unit: dict) -> dict:
    """The device discovery payload of one AMS unit, read from the printer's status.

    ``unit`` is the unit as the status topic carries it (``ams[]``).
    """
    ams_id = int(unit.get("id", 0))
    device_id = ams_device_id(serial, ams_id)
    shared = _shared(topic_prefix, serial)
    status_topic = shared["state_topic"]
    module = str(unit.get("module_type") or "").lower()
    unit_expression = (
        f"{{% set a = (value_json.ams or []) | selectattr('id', 'eq', {ams_id}) | first | default({{}}) %}}"
    )

    device: dict = {
        "identifiers": [device_id],
        # With the printer's name: two printers each have an "AMS 1".
        "name": f"{printer_name} {ams_unit_name(unit)}",
        "manufacturer": "Bambu Lab",
        "model": "AMS HT" if unit.get("is_ams_ht") else _AMS_MODELS.get(module, "AMS"),
        "via_device": printer_device_id(serial),
    }
    if unit.get("serial_number"):
        device["serial_number"] = unit["serial_number"]
    if unit.get("sw_ver"):
        device["sw_version"] = unit["sw_ver"]

    components = dict(
        [
            _sensor(
                device_id,
                "humidity",
                "Humidity",
                f"{unit_expression}{{{{ a.get('humidity') }}}}",
                device_class="humidity",
                unit_of_measurement="%",
                state_class="measurement",
            ),
            _sensor(
                device_id,
                "temperature",
                "Temperature",
                f"{unit_expression}{{{{ a.get('temp') }}}}",
                device_class="temperature",
                unit_of_measurement="°C",
                state_class="measurement",
            ),
        ]
    )
    if unit.get("is_ams_ht") or module in _AMS_DRYER_MODULES:
        key, config = _sensor(
            device_id,
            "drying_time",
            "Drying time remaining",
            f"{unit_expression}{{{{ a.get('dry_time') }}}}",
            device_class="duration",
            unit_of_measurement="min",
        )
        components[key] = config

    for tray in unit.get("tray") or []:
        tray_id = int(tray.get("id", 0))
        expression = (
            f"{unit_expression}"
            f"{{% set t = (a.get('tray') or []) | selectattr('id', 'eq', {tray_id}) | first | default({{}}) %}}"
        )
        components[f"slot_{tray_id + 1}"] = {
            "platform": "sensor",
            "unique_id": f"{device_id}_slot_{tray_id + 1}",
            "name": f"Slot {tray_id + 1}",
            **_tray_options(status_topic, expression),
        }

    return {"device": device, **shared, "components": components}


def build_configs(
    *,
    discovery_prefix: str,
    topic_prefix: str,
    serial: str,
    name: str,
    model: str | None,
    snapshot: dict,
    temperatures: dict | None,
    external_url: str | None = None,
) -> dict[str, dict]:
    """Every discovery message of a printer, by topic: the printer and its AMS units.

    ``snapshot`` is ``printer_state_to_dict`` of the printer's state.
    """
    configs = {
        discovery_topic(discovery_prefix, printer_device_id(serial)): build_printer_config(
            topic_prefix=topic_prefix,
            serial=serial,
            name=name,
            model=model,
            firmware_version=snapshot.get("firmware_version"),
            temperatures=temperatures,
            vt_tray=snapshot.get("vt_tray"),
            external_url=external_url,
        )
    }
    for unit in snapshot.get("ams") or []:
        topic = discovery_topic(discovery_prefix, ams_device_id(serial, int(unit.get("id", 0))))
        configs[topic] = build_ams_config(topic_prefix=topic_prefix, serial=serial, printer_name=name, unit=unit)
    return configs
