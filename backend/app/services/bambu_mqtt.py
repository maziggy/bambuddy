"""Bambu Lab MQTT communication service.

IMPORTANT: Always use qos=1 for all MQTT publish calls!
The printer ignores qos=0 messages when busy broadcasting status updates.
Using qos=1 ensures the printer acknowledges and processes our commands immediately.
This was discovered when K-profile requests with qos=0 took 20-30 seconds,
but with qos=1 they respond instantly.
"""

import asyncio
import json
import logging
import ssl
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


@dataclass
class MQTTLogEntry:
    """Log entry for MQTT message debugging."""

    timestamp: str
    topic: str
    direction: str  # "in" or "out"
    payload: dict


@dataclass
class HMSError:
    """Health Management System error from printer."""

    code: str
    attr: int  # Attribute value for constructing wiki URL
    module: int
    severity: int  # 1=fatal, 2=serious, 3=common, 4=info
    message: str = ""


@dataclass
class KProfile:
    """Pressure advance (K) calibration profile from printer."""

    slot_id: int
    extruder_id: int
    nozzle_id: str
    nozzle_diameter: str
    filament_id: str
    name: str
    k_value: str
    n_coef: str = "0.000000"
    ams_id: int = 0
    tray_id: int = -1
    setting_id: str | None = None


@dataclass
class NozzleInfo:
    """Nozzle hardware configuration."""

    nozzle_type: str = ""  # "stainless_steel" or "hardened_steel"
    nozzle_diameter: str = ""  # e.g., "0.4"


@dataclass
class PrintOptions:
    """AI detection and print options from xcam data."""

    # Core AI detectors
    spaghetti_detector: bool = False
    print_halt: bool = False
    halt_print_sensitivity: str = "medium"  # Spaghetti sensitivity
    first_layer_inspector: bool = False
    printing_monitor: bool = False  # AI print quality monitoring
    buildplate_marker_detector: bool = False
    allow_skip_parts: bool = False
    # Additional AI detectors - decoded from cfg bitmask
    nozzle_clumping_detector: bool = True
    nozzle_clumping_sensitivity: str = "medium"
    pileup_detector: bool = True
    pileup_sensitivity: str = "medium"
    airprint_detector: bool = True
    airprint_sensitivity: str = "medium"
    auto_recovery_step_loss: bool = True  # Uses print.print_option command
    filament_tangle_detect: bool = False


@dataclass
class PrinterState:
    connected: bool = False
    state: str = "unknown"
    current_print: str | None = None
    subtask_name: str | None = None
    progress: float = 0.0
    remaining_time: int = 0
    layer_num: int = 0
    total_layers: int = 0
    temperatures: dict = field(default_factory=dict)
    raw_data: dict = field(default_factory=dict)
    gcode_file: str | None = None
    subtask_id: str | None = None
    hms_errors: list = field(default_factory=list)  # List of HMSError
    kprofiles: list = field(default_factory=list)  # List of KProfile
    sdcard: bool = False  # SD card inserted
    store_to_sdcard: bool = False  # Store sent files on SD card (home_flag bit 11)
    timelapse: bool = False  # Timelapse recording active
    ipcam: bool = False  # Live view / camera streaming enabled
    wifi_signal: int | None = None  # WiFi signal strength in dBm
    # Nozzle hardware info (for dual nozzle printers, index 0 = left, 1 = right)
    nozzles: list = field(default_factory=lambda: [NozzleInfo(), NozzleInfo()])
    # AI detection and print options
    print_options: PrintOptions = field(default_factory=PrintOptions)
    # Calibration stage tracking (from stg_cur and stg fields)
    stg_cur: int = -1  # Current stage index (-1 = not calibrating)
    stg: list = field(default_factory=list)  # List of stages to execute
    # Air conditioning mode (0=cooling, 1=heating)
    airduct_mode: int = 0
    # Print speed level (1=silent, 2=standard, 3=sport, 4=ludicrous)
    speed_level: int = 2
    # Chamber light on/off
    chamber_light: bool = False
    # Active extruder for dual nozzle (0=right, 1=left) - from device.extruder.info[X].hnow
    active_extruder: int = 0
    # Currently loaded tray (global ID): 254 = external spool, 255 = no filament
    tray_now: int = 255
    # Pending load target - used to track what tray we're loading for H2D disambiguation
    pending_tray_target: int | None = None
    # AMS status for filament change tracking (from print.ams.ams_status field)
    # ams_status is a combined value: lower 8 bits = sub status, bits 8-15 = main status
    # Main status: 0=idle, 1=filament_change, 2=rfid_identifying, 3=assist, 4=calibration, etc.
    ams_status: int = 0
    ams_status_main: int = 0  # (ams_status >> 8) & 0xFF
    ams_status_sub: int = 0  # ams_status & 0xFF
    # mc_print_sub_stage - filament change step indicator from print.mc_print_sub_stage
    # Used by OrcaSlicer/BambuStudio to track progress during filament load/unload
    mc_print_sub_stage: int = 0
    # AMS mapping for dual nozzle: which slot is active (from ams.ams_exist_bits/tray_exist_bits)
    ams_mapping: list = field(default_factory=list)
    # Per-AMS extruder map: {ams_id: extruder_id} where 0=right, 1=left
    ams_extruder_map: dict = field(default_factory=dict)
    # H2D per-extruder tray_now from snow field: {extruder_id: normalized_global_tray_id}
    # snow encodes AMS ID in high byte: ams_id = snow >> 8, slot = snow & 0xFF
    h2d_extruder_snow: dict = field(default_factory=dict)
    # H2C nozzle rack: full device.nozzle.info array for tool-changer printers (>2 nozzles)
    nozzle_rack: list = field(default_factory=list)
    # Timestamp of last AMS data update (for RFID refresh detection)
    last_ams_update: float = 0.0
    # Printable objects for skip object functionality: {identify_id: object_name}
    printable_objects: dict = field(default_factory=dict)
    # Objects that have been skipped during the current print
    skipped_objects: list = field(default_factory=list)
    # Fan speeds (0-100 percentage, None if not available for this model)
    cooling_fan_speed: int | None = None  # Part cooling fan
    big_fan1_speed: int | None = None  # Auxiliary fan
    big_fan2_speed: int | None = None  # Chamber/exhaust fan
    heatbreak_fan_speed: int | None = None  # Hotend heatbreak fan
    # Firmware version info (from info.module[name="ota"].sw_ver)
    firmware_version: str | None = None


# Stage name mapping from BambuStudio DeviceManager.cpp
STAGE_NAMES = {
    0: "Printing",
    1: "Auto bed leveling",
    2: "Heatbed preheating",
    3: "Vibration compensation",
    4: "Changing filament",
    5: "M400 pause",
    6: "Paused (filament ran out)",
    7: "Heating nozzle",
    8: "Calibrating dynamic flow",
    9: "Scanning bed surface",
    10: "Inspecting first layer",
    11: "Identifying build plate type",
    12: "Calibrating Micro Lidar",
    13: "Homing toolhead",
    14: "Cleaning nozzle tip",
    15: "Checking extruder temperature",
    16: "Paused by the user",
    17: "Pause (front cover fall off)",
    18: "Calibrating the micro lidar",
    19: "Calibrating flow ratio",
    20: "Pause (nozzle temperature malfunction)",
    21: "Pause (heatbed temperature malfunction)",
    22: "Filament unloading",
    23: "Pause (step loss)",
    24: "Filament loading",
    25: "Motor noise cancellation",
    26: "Pause (AMS offline)",
    27: "Pause (low speed of the heatbreak fan)",
    28: "Pause (chamber temperature control problem)",
    29: "Cooling chamber",
    30: "Pause (Gcode inserted by user)",
    31: "Motor noise showoff",
    32: "Pause (nozzle clumping)",
    33: "Pause (cutter error)",
    34: "Pause (first layer error)",
    35: "Pause (nozzle clog)",
    36: "Measuring motion precision",
    37: "Enhancing motion precision",
    38: "Measure motion accuracy",
    39: "Nozzle offset calibration",
    40: "High temperature auto bed leveling",
    41: "Auto Check: Quick Release Lever",
    42: "Auto Check: Door and Upper Cover",
    43: "Laser Calibration",
    44: "Auto Check: Platform",
    45: "Confirming BirdsEye Camera location",
    46: "Calibrating BirdsEye Camera",
    47: "Auto bed leveling - phase 1",
    48: "Auto bed leveling - phase 2",
    49: "Heating chamber",
    50: "Cooling heatbed",
    51: "Printing calibration lines",
    52: "Auto Check: Material",
    53: "Live View Camera Calibration",
    54: "Waiting for heatbed temperature",
    55: "Auto Check: Material Position",
    56: "Cutting Module Offset Calibration",
    57: "Measuring Surface",
    58: "Thermal Preconditioning",
    59: "Homing Blade Holder",
    60: "Calibrating Camera Offset",
    61: "Calibrating Blade Holder Position",
    62: "Hotend Pick and Place Test",
    63: "Waiting for Chamber temperature",
    64: "Preparing Hotend",
    65: "Calibrating nozzle clumping detection",
    66: "Purifying the chamber air",
}


def get_stage_name(stage: int) -> str:
    """Get human-readable stage name from stage number."""
    return STAGE_NAMES.get(stage, f"Unknown stage ({stage})")


class BambuMQTTClient:
    """MQTT client for Bambu Lab printer communication."""

    MQTT_PORT = 8883

    def __init__(
        self,
        ip_address: str,
        serial_number: str,
        access_code: str,
        model: str | None = None,
        on_state_change: Callable[[PrinterState], None] | None = None,
        on_print_start: Callable[[dict], None] | None = None,
        on_print_complete: Callable[[dict], None] | None = None,
        on_ams_change: Callable[[list], None] | None = None,
        on_layer_change: Callable[[int], None] | None = None,
    ):
        self.ip_address = ip_address
        self.serial_number = serial_number
        self.access_code = access_code
        self.model = model
        self.on_state_change = on_state_change
        self.on_print_start = on_print_start
        self.on_print_complete = on_print_complete
        self.on_ams_change = on_ams_change
        self.on_layer_change = on_layer_change

        self.state = PrinterState()
        self._client: mqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._previous_gcode_state: str | None = None
        self._previous_gcode_file: str | None = None
        self._was_running: bool = False  # Track if we've seen RUNNING state for current print
        self._completion_triggered: bool = False  # Prevent duplicate completion triggers
        self._timelapse_during_print: bool = False  # Track if timelapse was active during this print
        self._message_log: deque[MQTTLogEntry] = deque(maxlen=100)
        self._logging_enabled: bool = False
        self._last_message_time: float = 0.0  # Track when we last received a message
        self._previous_ams_hash: str | None = None  # Track AMS changes

        # K-profile command tracking
        self._sequence_id: int = 0
        self._pending_kprofile_response: asyncio.Event | None = None
        self._kprofile_response_data: list | None = None

        # Xcam hold timers - OrcaSlicer pattern: ignore incoming data for 3 seconds after command
        # Key: module_name, Value: timestamp when command was sent
        self._xcam_hold_start: dict[str, float] = {}
        self._xcam_hold_time: float = 3.0  # Ignore incoming data for 3 seconds after command

        # Track last requested tray ID for H2D dual-nozzle printers
        # H2D only reports slot number (0-3) in tray_now, not global tray ID
        # We use our tracked value to resolve the correct global ID
        self._last_load_tray_id: int | None = None

    @property
    def topic_subscribe(self) -> str:
        return f"device/{self.serial_number}/report"

    @property
    def topic_publish(self) -> str:
        return f"device/{self.serial_number}/request"

    # Maximum time (seconds) without a message before considering connection stale
    STALE_TIMEOUT = 60.0

    def is_stale(self) -> bool:
        """Check if the connection is stale (no messages for too long)."""
        if self._last_message_time == 0:
            return False  # Never received a message yet
        time_since_last = time.time() - self._last_message_time
        return time_since_last > self.STALE_TIMEOUT

    def check_staleness(self) -> bool:
        """Check staleness and update connected state if stale. Returns True if connected."""
        if self.state.connected and self.is_stale():
            logger.warning(
                f"[{self.serial_number}] Connection stale - no message for {time.time() - self._last_message_time:.1f}s"
            )
            self.state.connected = False
            if self.on_state_change:
                self.on_state_change(self.state)
        return self.state.connected

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.state.connected = True
            client.subscribe(self.topic_subscribe)
            # Request full status update (includes nozzle info in push_status response)
            self._request_push_all()
            # Request firmware version info
            self._request_version()
            # Note: get_accessories returns stale nozzle data on H2D, so we don't use it.
            # The correct nozzle data comes from push_status.
            # Prime K-profile request (Bambu printers often ignore first request)
            self._prime_kprofile_request()
            # Immediately broadcast connection state change
            if self.on_state_change:
                self.on_state_change(self.state)
        else:
            self.state.connected = False

    def _on_disconnect(self, client, userdata, disconnect_flags=None, rc=None, properties=None):
        # Ignore spurious disconnect callbacks if we've received a message recently
        # Paho-mqtt sometimes fires disconnect callbacks while the connection is still active
        time_since_last_message = time.time() - self._last_message_time
        if time_since_last_message < 30.0 and self._last_message_time > 0:
            logger.debug(
                f"[{self.serial_number}] Ignoring spurious disconnect (last message {time_since_last_message:.1f}s ago)"
            )
            return

        logger.warning("[%s] MQTT disconnected: rc=%s, flags=%s", self.serial_number, rc, disconnect_flags)
        self.state.connected = False
        if self.on_state_change:
            self.on_state_change(self.state)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            # Track last message time - receiving a message proves we're connected
            self._last_message_time = time.time()
            self.state.connected = True

            # TEMP: Dump full payload once to find extruder state field
            if not hasattr(self, "_payload_dumped"):
                self._payload_dumped = True
                logger.info("[%s] FULL MQTT PAYLOAD DUMP:\n%s", self.serial_number, json.dumps(payload, indent=2))
            # Log message if logging is enabled
            if self._logging_enabled:
                self._message_log.append(
                    MQTTLogEntry(
                        timestamp=datetime.now().isoformat(),
                        topic=msg.topic,
                        direction="in",
                        payload=payload,
                    )
                )
            self._process_message(payload)
        except json.JSONDecodeError:
            pass  # Ignore non-JSON MQTT messages (e.g. binary or malformed payloads)

    def _process_message(self, payload: dict):
        """Process incoming MQTT message from printer."""
        # Handle top-level AMS data (comes outside of "print" key)
        # Wrap in try/except to prevent breaking the MQTT connection
        if "ams" in payload:
            try:
                self._handle_ams_data(payload["ams"])
            except Exception as e:
                logger.error("[%s] Error handling AMS data: %s", self.serial_number, e)

        # Handle xcam data (camera settings and AI detection) at top level
        if "xcam" in payload:
            xcam_data = payload["xcam"]
            logger.info("[%s] Received xcam data at top level: %s", self.serial_number, xcam_data)
            self._parse_xcam_data(xcam_data)
            # Fire state change callback for top-level xcam (not nested in "print")
            if "print" not in payload and self.on_state_change:
                self.on_state_change(self.state)

        # Handle system responses (accessories info, etc.)
        if "system" in payload:
            system_data = payload["system"]
            logger.info("[%s] Received system data: %s", self.serial_number, system_data)
            self._handle_system_response(system_data)

        # Handle info responses (firmware version info from get_version command)
        if "info" in payload:
            info_data = payload["info"]
            if isinstance(info_data, dict) and info_data.get("command") == "get_version":
                self._handle_version_info(info_data)

        # Parse WiFi signal at top level (some printers send it here)
        if "wifi_signal" in payload:
            wifi_signal = payload["wifi_signal"]
            if isinstance(wifi_signal, (int, float)):
                self.state.wifi_signal = int(wifi_signal)
            elif isinstance(wifi_signal, str):
                try:
                    self.state.wifi_signal = int(wifi_signal.replace("dBm", "").strip())
                except ValueError:
                    pass  # Ignore unparseable wifi_signal strings; field is non-critical

        if "print" in payload:
            print_data = payload["print"]

            # Check if xcam is nested inside print data
            if "xcam" in print_data:
                logger.info("[%s] Found xcam inside print data: %s", self.serial_number, print_data["xcam"])
                self._parse_xcam_data(print_data["xcam"])

            # Log when we see gcode_state changes
            if "gcode_state" in print_data:
                logger.info(
                    f"[{self.serial_number}] Received gcode_state: {print_data.get('gcode_state')}, "
                    f"gcode_file: {print_data.get('gcode_file')}, subtask_name: {print_data.get('subtask_name')}"
                )

            # Handle AMS data that comes inside print key
            if "ams" in print_data:
                try:
                    self._handle_ams_data(print_data["ams"])
                except Exception as e:
                    logger.error("[%s] Error handling AMS data from print: %s", self.serial_number, e)

            # Handle vt_tray (virtual tray / external spool) data
            if "vt_tray" in print_data:
                vt_tray = print_data["vt_tray"]
                self.state.raw_data["vt_tray"] = vt_tray
                # Log vt_tray to investigate per-extruder data for H2D
                if not hasattr(self, "_vt_tray_logged") or not self._vt_tray_logged:
                    logger.info("[%s] vt_tray data: %s", self.serial_number, vt_tray)
                    self._vt_tray_logged = True

            # Parse ams_status directly from print data (NOT from print.ams)
            # ams_status is a combined value: lower 8 bits = sub status, bits 8-15 = main status
            # Main status: 0=idle, 1=filament_change, 2=rfid_identifying, 3=assist, 4=calibration
            # Sub status (when main=1): 2=heating, 3=AMS feeding, 4=retract, 6=push, 7=purge
            if "ams_status" in print_data:
                raw_ams_status = print_data["ams_status"]
                if isinstance(raw_ams_status, str):
                    try:
                        self.state.ams_status = int(raw_ams_status)
                    except ValueError:
                        self.state.ams_status = 0
                else:
                    self.state.ams_status = raw_ams_status if raw_ams_status is not None else 0

                # Compute main and sub status
                self.state.ams_status_sub = self.state.ams_status & 0xFF
                self.state.ams_status_main = (self.state.ams_status >> 8) & 0xFF

                # Log when ams_status changes (for filament change tracking debug)
                logger.debug(
                    f"[{self.serial_number}] ams_status: {self.state.ams_status} "
                    f"(main={self.state.ams_status_main}, sub={self.state.ams_status_sub})"
                )

            # Check for K-profile response (extrusion_cali)
            if "command" in print_data:
                logger.debug("[%s] Received command response: %s", self.serial_number, print_data.get("command"))
            if "command" in print_data and print_data.get("command") == "extrusion_cali_get":
                self._handle_kprofile_response(print_data)

            self._update_state(print_data)

    def _handle_system_response(self, data: dict):
        """Handle system responses including accessories info.

        Note: get_accessories returns stale/incorrect nozzle_type data on H2D.
        The correct nozzle data comes from push_status, so we don't update
        nozzle type/diameter from get_accessories. We just log the response
        for debugging purposes.
        """
        command = data.get("command")

        if command == "get_accessories":
            # Log response for debugging - but DON'T use it to update nozzle data
            # because it returns stale values (e.g., 'stainless_steel' when the
            # actual nozzle is 'HH01' hardened steel high-flow)
            logger.info("[%s] Accessories response (not used for nozzle data): %s", self.serial_number, data)

    def _handle_version_info(self, data: dict):
        """Handle version info response from get_version command.

        Parses firmware version from the 'ota' module in the module list.
        Message format:
        {
            "command": "get_version",
            "module": [
                {"name": "ota", "sw_ver": "01.08.05.00"},
                {"name": "rv1126", "sw_ver": "00.00.14.74"},
                ...
            ]
        }
        """
        modules = data.get("module", [])
        if not isinstance(modules, list):
            return

        for module in modules:
            if not isinstance(module, dict):
                continue
            if module.get("name") == "ota":
                version = module.get("sw_ver")
                if version:
                    old_version = self.state.firmware_version
                    self.state.firmware_version = version
                    if old_version != version:
                        logger.info("[%s] Firmware version: %s", self.serial_number, version)
                    # Trigger state change callback
                    if self.on_state_change:
                        self.on_state_change(self.state)
                break

    def _parse_xcam_data(self, xcam_data):
        """Parse xcam data for camera settings and AI detection options."""
        if not isinstance(xcam_data, dict):
            return

        current_time = time.time()

        # Helper to check if we should accept incoming value for a module
        # OrcaSlicer pattern: simple hold timer, ignore ALL data for 3 seconds after command
        def should_accept_value(module_name: str, incoming_value: bool) -> bool:
            """Check if we should accept an incoming xcam value.

            OrcaSlicer pattern: After sending a command, ignore incoming data
            for 3 seconds. After that, accept whatever the printer sends.
            """
            if module_name not in self._xcam_hold_start:
                return True  # No hold timer, accept incoming

            hold_start = self._xcam_hold_start[module_name]
            elapsed = current_time - hold_start

            if elapsed > self._xcam_hold_time:
                # Hold timer expired - accept incoming and clear hold
                del self._xcam_hold_start[module_name]
                logger.debug("[%s] Hold expired for %s, accepting %s", self.serial_number, module_name, incoming_value)
                return True

            # Within hold period - ignore incoming data
            logger.debug(
                f"[{self.serial_number}] Ignoring {module_name}={incoming_value} "
                f"(hold active, {elapsed:.1f}s < {self._xcam_hold_time}s)"
            )
            return False

        # Log all xcam fields for debugging
        logger.debug("[%s] Parsing xcam data - all fields: %s", self.serial_number, list(xcam_data.keys()))

        # The cfg bitmask contains the ACTUAL detector states - the individual boolean
        # fields (spaghetti_detector, etc.) are often stale/cached.
        # CFG bitmask structure (each detector uses 3 bits: [sens_low, sens_high, enabled]):
        # - Bits 5-7: spaghetti_detector (sens in 5-6, enabled in 7)
        # - Bits 8-10: pileup_detector (sens in 8-9, enabled in 10)
        # - Bits 11-13: clump_detector/nozzle_clumping (sens in 11-12, enabled in 13)
        # - Bits 14-16: airprint_detector (sens in 14-15, enabled in 16)
        # Sensitivity values: 0=low, 1=medium, 2=high
        if "cfg" in xcam_data:
            cfg = xcam_data["cfg"]
            logger.debug("[%s] xcam cfg bitmask: %s (binary: %s)", self.serial_number, cfg, bin(cfg))

            def decode_detector(start_bit):
                """Decode a detector from cfg: returns (enabled, sensitivity_str)"""
                sens_bits = (cfg >> start_bit) & 0x3
                enabled = bool((cfg >> (start_bit + 2)) & 1)
                sensitivity = {0: "low", 1: "medium", 2: "high"}.get(sens_bits, "medium")
                return enabled, sensitivity

            # Spaghetti detector (bits 5-7)
            cfg_spaghetti, cfg_sensitivity = decode_detector(5)
            if should_accept_value("spaghetti_detector", cfg_spaghetti):
                old_value = self.state.print_options.spaghetti_detector
                if cfg_spaghetti != old_value:
                    logger.info(
                        f"[{self.serial_number}] spaghetti_detector changed (from cfg): {old_value} -> {cfg_spaghetti}"
                    )
                self.state.print_options.spaghetti_detector = cfg_spaghetti

            # Check hold timer for sensitivity before accepting
            if "halt_print_sensitivity" not in self._xcam_hold_start:
                if cfg_sensitivity != self.state.print_options.halt_print_sensitivity:
                    logger.info(
                        f"[{self.serial_number}] Sensitivity changed (from cfg): "
                        f"{self.state.print_options.halt_print_sensitivity} -> {cfg_sensitivity}"
                    )
                    self.state.print_options.halt_print_sensitivity = cfg_sensitivity
            else:
                hold_start = self._xcam_hold_start["halt_print_sensitivity"]
                elapsed = current_time - hold_start
                if elapsed <= self._xcam_hold_time:
                    logger.debug(
                        f"[{self.serial_number}] Ignoring cfg sensitivity={cfg_sensitivity} "
                        f"(hold active, {elapsed:.1f}s < {self._xcam_hold_time}s)"
                    )
                else:
                    # Hold expired - accept from cfg
                    if cfg_sensitivity != self.state.print_options.halt_print_sensitivity:
                        logger.info(
                            f"[{self.serial_number}] Sensitivity synced (from cfg after hold): "
                            f"{self.state.print_options.halt_print_sensitivity} -> {cfg_sensitivity}"
                        )
                        self.state.print_options.halt_print_sensitivity = cfg_sensitivity
                    del self._xcam_hold_start["halt_print_sensitivity"]

            # Pileup detector (bits 8-10)
            cfg_pileup, cfg_pileup_sens = decode_detector(8)
            if should_accept_value("pileup_detector", cfg_pileup):
                if cfg_pileup != self.state.print_options.pileup_detector:
                    logger.info(
                        f"[{self.serial_number}] pileup_detector changed (from cfg): {self.state.print_options.pileup_detector} -> {cfg_pileup}"
                    )
                    self.state.print_options.pileup_detector = cfg_pileup
            # Pileup sensitivity with hold timer
            if "pileup_sensitivity" not in self._xcam_hold_start:
                if cfg_pileup_sens != self.state.print_options.pileup_sensitivity:
                    logger.info(
                        f"[{self.serial_number}] pileup_sensitivity changed (from cfg): {self.state.print_options.pileup_sensitivity} -> {cfg_pileup_sens}"
                    )
                    self.state.print_options.pileup_sensitivity = cfg_pileup_sens
            else:
                hold_start = self._xcam_hold_start["pileup_sensitivity"]
                elapsed = current_time - hold_start
                if elapsed > self._xcam_hold_time:
                    if cfg_pileup_sens != self.state.print_options.pileup_sensitivity:
                        logger.info(
                            f"[{self.serial_number}] pileup_sensitivity synced (from cfg after hold): {self.state.print_options.pileup_sensitivity} -> {cfg_pileup_sens}"
                        )
                        self.state.print_options.pileup_sensitivity = cfg_pileup_sens
                    del self._xcam_hold_start["pileup_sensitivity"]

            # Clump/nozzle clumping detector (bits 11-13)
            cfg_clump, cfg_clump_sens = decode_detector(11)
            if should_accept_value("clump_detector", cfg_clump):
                if cfg_clump != self.state.print_options.nozzle_clumping_detector:
                    logger.info(
                        f"[{self.serial_number}] nozzle_clumping_detector changed (from cfg): {self.state.print_options.nozzle_clumping_detector} -> {cfg_clump}"
                    )
                    self.state.print_options.nozzle_clumping_detector = cfg_clump
            # Clump sensitivity with hold timer
            if "nozzle_clumping_sensitivity" not in self._xcam_hold_start:
                if cfg_clump_sens != self.state.print_options.nozzle_clumping_sensitivity:
                    logger.info(
                        f"[{self.serial_number}] nozzle_clumping_sensitivity changed (from cfg): {self.state.print_options.nozzle_clumping_sensitivity} -> {cfg_clump_sens}"
                    )
                    self.state.print_options.nozzle_clumping_sensitivity = cfg_clump_sens
            else:
                hold_start = self._xcam_hold_start["nozzle_clumping_sensitivity"]
                elapsed = current_time - hold_start
                if elapsed > self._xcam_hold_time:
                    if cfg_clump_sens != self.state.print_options.nozzle_clumping_sensitivity:
                        logger.info(
                            f"[{self.serial_number}] nozzle_clumping_sensitivity synced (from cfg after hold): {self.state.print_options.nozzle_clumping_sensitivity} -> {cfg_clump_sens}"
                        )
                        self.state.print_options.nozzle_clumping_sensitivity = cfg_clump_sens
                    del self._xcam_hold_start["nozzle_clumping_sensitivity"]

            # Airprint detector (bits 14-16)
            cfg_airprint, cfg_airprint_sens = decode_detector(14)
            if should_accept_value("airprint_detector", cfg_airprint):
                if cfg_airprint != self.state.print_options.airprint_detector:
                    logger.info(
                        f"[{self.serial_number}] airprint_detector changed (from cfg): {self.state.print_options.airprint_detector} -> {cfg_airprint}"
                    )
                    self.state.print_options.airprint_detector = cfg_airprint
            # Airprint sensitivity with hold timer
            if "airprint_sensitivity" not in self._xcam_hold_start:
                if cfg_airprint_sens != self.state.print_options.airprint_sensitivity:
                    logger.info(
                        f"[{self.serial_number}] airprint_sensitivity changed (from cfg): {self.state.print_options.airprint_sensitivity} -> {cfg_airprint_sens}"
                    )
                    self.state.print_options.airprint_sensitivity = cfg_airprint_sens
            else:
                hold_start = self._xcam_hold_start["airprint_sensitivity"]
                elapsed = current_time - hold_start
                if elapsed > self._xcam_hold_time:
                    if cfg_airprint_sens != self.state.print_options.airprint_sensitivity:
                        logger.info(
                            f"[{self.serial_number}] airprint_sensitivity synced (from cfg after hold): {self.state.print_options.airprint_sensitivity} -> {cfg_airprint_sens}"
                        )
                        self.state.print_options.airprint_sensitivity = cfg_airprint_sens
                    del self._xcam_hold_start["airprint_sensitivity"]

        # Camera settings
        if "ipcam_record" in xcam_data:
            self.state.ipcam = xcam_data.get("ipcam_record") == "enable"
        if "timelapse" in xcam_data:
            self.state.timelapse = xcam_data.get("timelapse") == "enable"
            # Track if timelapse was ever active during this print
            if self.state.timelapse and self._was_running:
                self._timelapse_during_print = True

        # Skip spaghetti_detector boolean field - we read from cfg bitmask above
        if "print_halt" in xcam_data:
            self.state.print_options.print_halt = bool(xcam_data.get("print_halt"))
        # Skip halt_print_sensitivity field - it's always stale ("medium")
        # We read the actual sensitivity from cfg bits 5-6 above
        if "first_layer_inspector" in xcam_data:
            new_value = bool(xcam_data.get("first_layer_inspector"))
            if should_accept_value("first_layer_inspector", new_value):
                self.state.print_options.first_layer_inspector = new_value
        if "printing_monitor" in xcam_data:
            new_value = bool(xcam_data.get("printing_monitor"))
            if should_accept_value("printing_monitor", new_value):
                self.state.print_options.printing_monitor = new_value
        if "buildplate_marker_detector" in xcam_data:
            new_value = bool(xcam_data.get("buildplate_marker_detector"))
            if should_accept_value("buildplate_marker_detector", new_value):
                self.state.print_options.buildplate_marker_detector = new_value
        if "allow_skip_parts" in xcam_data:
            new_value = bool(xcam_data.get("allow_skip_parts"))
            if should_accept_value("allow_skip_parts", new_value):
                self.state.print_options.allow_skip_parts = new_value

        # Additional AI detectors - these are decoded from cfg bitmask above, not from
        # individual boolean fields (which are not sent by the printer)
        # pileup_detector, nozzle_clumping_detector, airprint_detector - from cfg
        # auto_recovery_step_loss and filament_tangle_detect - tracked locally only
        if "auto_recovery_step_loss" in xcam_data:
            self.state.print_options.auto_recovery_step_loss = bool(xcam_data.get("auto_recovery_step_loss"))
        if "filament_tangle_detect" in xcam_data:
            self.state.print_options.filament_tangle_detect = bool(xcam_data.get("filament_tangle_detect"))

    def _handle_ams_data(self, ams_data):
        """Handle AMS data changes for Spoolman integration.

        This is called when we receive top-level AMS data in MQTT messages.
        It detects changes and triggers the callback for Spoolman sync.
        """
        import hashlib

        # Handle nested ams structure: {"ams": {"ams": [...]}} or {"ams": [...]}
        # Also handle P1S partial updates: {"tray_now": ..., "tray_tar": ...} without "ams" key
        ams_list = None
        if isinstance(ams_data, dict):
            if "ams" in ams_data:
                ams_list = ams_data["ams"]
            # Log all AMS dict fields to debug tray_now for H2D dual-nozzle
            non_list_fields = {k: v for k, v in ams_data.items() if k != "ams"}
            if non_list_fields:
                logger.debug("[%s] AMS dict fields: %s", self.serial_number, non_list_fields)

            # IMPORTANT: Parse ams_status FIRST before tray_now, so we have fresh status
            # when checking if we're in filament change mode for tray_now disambiguation
            if "ams_status" in ams_data:
                raw_ams_status = ams_data["ams_status"]
                if isinstance(raw_ams_status, str):
                    try:
                        self.state.ams_status = int(raw_ams_status)
                    except ValueError:
                        self.state.ams_status = 0
                else:
                    self.state.ams_status = raw_ams_status if raw_ams_status is not None else 0
                # Compute main and sub status
                self.state.ams_status_sub = self.state.ams_status & 0xFF
                self.state.ams_status_main = (self.state.ams_status >> 8) & 0xFF
                logger.debug(
                    f"[{self.serial_number}] ams_status: {self.state.ams_status} "
                    f"(main={self.state.ams_status_main}, sub={self.state.ams_status_sub})"
                )

            # Parse tray_now from AMS dict - this is the currently loaded tray global ID
            # Note: tray_tar is also available but on H2D it's just slot number (0-3), not global ID
            if "tray_now" in ams_data:
                raw_tray_now = ams_data["tray_now"]
                # Convert string to int if needed
                if isinstance(raw_tray_now, str):
                    try:
                        parsed_tray_now = int(raw_tray_now)
                    except ValueError:
                        parsed_tray_now = 255
                else:
                    parsed_tray_now = raw_tray_now if raw_tray_now is not None else 255

                # H2D dual-nozzle printers report only slot number (0-3), not global tray ID
                # Use active_extruder + ams_extruder_map to determine which AMS the slot belongs to
                if parsed_tray_now >= 0 and parsed_tray_now <= 3:
                    # First, check if we have a pending target that matches this slot
                    pending_target = self.state.pending_tray_target
                    if pending_target is not None:
                        pending_slot = pending_target % 4
                        if pending_slot == parsed_tray_now:
                            # Slot matches our pending target - use the full global ID
                            logger.info(
                                f"[{self.serial_number}] H2D tray_now disambiguation: "
                                f"slot {parsed_tray_now} matches pending_tray_target {pending_target} -> using global ID {pending_target}"
                            )
                            self.state.tray_now = pending_target
                            # Clear pending target now that load is confirmed
                            self.state.pending_tray_target = None
                        else:
                            # Slot doesn't match our pending target - something changed, use slot as-is
                            logger.warning(
                                f"[{self.serial_number}] H2D tray_now: slot {parsed_tray_now} doesn't match "
                                f"pending_tray_target {pending_target} (slot {pending_slot}) - using slot as global ID"
                            )
                            self.state.tray_now = parsed_tray_now
                            # Clear pending target since it's stale
                            self.state.pending_tray_target = None
                    else:
                        # No pending target - use h2d_extruder_snow for accurate disambiguation
                        # H2D sends snow field in device.extruder.info with AMS ID in high byte
                        active_ext = self.state.active_extruder  # 0=right, 1=left

                        # Best source: use snow value from device.extruder.info if available
                        snow_tray = self.state.h2d_extruder_snow.get(active_ext)
                        if snow_tray is not None and snow_tray != 255:
                            # snow_tray is already normalized to global ID
                            # Verify the slot matches what we see in tray_now
                            snow_slot = snow_tray % 4 if snow_tray < 128 else -1
                            if snow_slot == parsed_tray_now:
                                if self.state.tray_now != snow_tray:
                                    logger.info(
                                        f"[{self.serial_number}] H2D tray_now from snow: "
                                        f"extruder[{active_ext}] snow={snow_tray} (slot {snow_slot})"
                                    )
                                self.state.tray_now = snow_tray
                            else:
                                # Slot mismatch - snow field may not have updated yet, trust snow
                                logger.debug(
                                    f"[{self.serial_number}] H2D tray_now: ams.tray_now slot {parsed_tray_now} "
                                    f"!= snow slot {snow_slot}, using snow value {snow_tray}"
                                )
                                self.state.tray_now = snow_tray
                        else:
                            # Fallback: snow not available, use ams_extruder_map (less reliable)
                            ams_map = self.state.ams_extruder_map
                            # Find ALL AMS units on the active extruder
                            ams_on_extruder = []
                            for ams_id_str, ext_id in ams_map.items():
                                if ext_id == active_ext:
                                    try:
                                        ams_on_extruder.append(int(ams_id_str))
                                    except ValueError:
                                        pass  # Skip AMS IDs that aren't valid integers

                            if len(ams_on_extruder) == 1:
                                # Single AMS on this extruder - unambiguous
                                active_ams_id = ams_on_extruder[0]
                                global_tray_id = active_ams_id * 4 + parsed_tray_now
                                logger.info(
                                    f"[{self.serial_number}] H2D tray_now fallback: "
                                    f"slot {parsed_tray_now} + single AMS {active_ams_id} -> global ID {global_tray_id}"
                                )
                                self.state.tray_now = global_tray_id
                            elif len(ams_on_extruder) > 1:
                                # Multiple AMS on this extruder - keep current if valid, else use slot as-is
                                current_tray = self.state.tray_now
                                current_ams = current_tray // 4 if current_tray < 128 else -1
                                if current_ams in ams_on_extruder and (current_tray % 4) == parsed_tray_now:
                                    # Current is valid and matches slot - keep it
                                    logger.debug(
                                        f"[{self.serial_number}] H2D tray_now: multiple AMS {ams_on_extruder}, "
                                        f"keeping current {current_tray} (matches slot {parsed_tray_now})"
                                    )
                                else:
                                    # Can't disambiguate - use slot as-is (will be wrong for non-first AMS)
                                    logger.warning(
                                        f"[{self.serial_number}] H2D tray_now: multiple AMS {ams_on_extruder} on extruder {active_ext}, "
                                        f"no snow field, using slot {parsed_tray_now} (may be incorrect)"
                                    )
                                    self.state.tray_now = parsed_tray_now
                            else:
                                # No AMS on this extruder - use slot as-is
                                logger.warning(
                                    f"[{self.serial_number}] H2D tray_now: no AMS on extruder {active_ext}, "
                                    f"using slot {parsed_tray_now}"
                                )
                                self.state.tray_now = parsed_tray_now
                else:
                    # tray_now > 3 means it's already a global ID, or 255 means unloaded
                    # Note: Do NOT clear pending_tray_target on tray_now=255 here.
                    # During filament change, the printer sends 255 first (unload), then the slot.
                    # We only clear pending_tray_target explicitly in ams_unload_filament().
                    # Trust the printer's reported value.
                    self.state.tray_now = parsed_tray_now

                logger.debug("[%s] tray_now updated: %s", self.serial_number, self.state.tray_now)

            # NOTE: ams_status is parsed BEFORE tray_now (see above) to ensure correct
            # state when checking filament change mode for H2D disambiguation

            # P1S/P1P send partial updates without "ams" key - this is valid, not an error
            # We've already processed the status fields above, so just return if no ams list
            if ams_list is None:
                logger.debug("[%s] AMS partial update (no tray data)", self.serial_number)
                return
        elif isinstance(ams_data, list):
            ams_list = ams_data
        else:
            logger.warning("[%s] Unexpected AMS data format: %s", self.serial_number, type(ams_data))
            return

        # Merge AMS data instead of replacing, to handle partial updates
        # During prints, the printer may only send updates for active AMS units
        # We need deep merging at the tray level to preserve fields like tray_sub_brands
        existing_ams = self.state.raw_data.get("ams", [])
        existing_by_id = {ams.get("id"): ams for ams in existing_ams if ams.get("id") is not None}

        # Update existing units with new data, add new units
        for ams_unit in ams_list:
            ams_id = ams_unit.get("id")
            if ams_id is not None:
                existing_unit = existing_by_id.get(ams_id)
                if existing_unit and "tray" in ams_unit:
                    # Deep merge trays to preserve fields from previous updates
                    existing_trays = {t.get("id"): t for t in existing_unit.get("tray", []) if t.get("id") is not None}
                    merged_trays = []
                    for new_tray in ams_unit.get("tray", []):
                        tray_id = new_tray.get("id")
                        if tray_id is not None and tray_id in existing_trays:
                            # Merge: start with existing, update with new non-empty values
                            merged_tray = existing_trays[tray_id].copy()
                            for key, value in new_tray.items():
                                # Fields that should always be updated (even with empty/zero values):
                                # - remain, k, id, cali_idx: status indicators where 0 is valid
                                # - tray_type, tray_sub_brands, tag_uid, tray_uuid, tray_info_idx,
                                #   tray_color, tray_id_name: slot content indicators that must be
                                #   cleared when a spool is removed (fixes #147 - old AMS empty slot)
                                always_update_fields = (
                                    "remain",
                                    "k",
                                    "id",
                                    "cali_idx",
                                    "tray_type",
                                    "tray_sub_brands",
                                    "tag_uid",
                                    "tray_uuid",
                                    "tray_info_idx",
                                    "tray_color",
                                    "tray_id_name",
                                )
                                if key in always_update_fields or value not in (
                                    None,
                                    "",
                                    "0000000000000000",
                                    "00000000000000000000000000000000",
                                ):
                                    merged_tray[key] = value
                            merged_trays.append(merged_tray)
                        else:
                            merged_trays.append(new_tray)
                    # Update ams_unit with merged trays
                    ams_unit = {**ams_unit, "tray": merged_trays}
                existing_by_id[ams_id] = ams_unit

        # Convert back to list, sorted by ID for consistent ordering
        merged_ams = sorted(existing_by_id.values(), key=lambda x: x.get("id", 0))

        # Check tray_exist_bits to clear empty slots (Issue #147)
        # New AMS models don't send empty tray data - they just update tray_exist_bits
        # Each bit in tray_exist_bits represents a slot: bit=0 means empty, bit=1 means has spool
        tray_exist_bits_str = ams_data.get("tray_exist_bits") if isinstance(ams_data, dict) else None
        if tray_exist_bits_str:
            try:
                tray_exist_bits = int(tray_exist_bits_str, 16)
                for ams_unit in merged_ams:
                    ams_id_raw = ams_unit.get("id")
                    if ams_id_raw is None:
                        continue
                    # Convert to int (may be string from JSON)
                    ams_id = int(ams_id_raw) if isinstance(ams_id_raw, str) else ams_id_raw
                    if ams_id >= 128:  # Skip HT AMS (id >= 128)
                        continue
                    # Bits for this AMS unit: bits (ams_id*4) to (ams_id*4 + 3)
                    for tray in ams_unit.get("tray", []):
                        tray_id_raw = tray.get("id")
                        if tray_id_raw is None:
                            continue
                        # Convert to int (may be string from JSON)
                        tray_id = int(tray_id_raw) if isinstance(tray_id_raw, str) else tray_id_raw
                        global_bit = ams_id * 4 + tray_id
                        slot_exists = (tray_exist_bits >> global_bit) & 1
                        if not slot_exists and tray.get("tray_type"):
                            # Slot is marked empty but has data - clear it
                            logger.info(
                                f"[{self.serial_number}] Clearing empty slot: AMS {ams_id} slot {tray_id} "
                                f"(tray_exist_bits bit {global_bit} = 0)"
                            )
                            tray["tray_type"] = ""
                            tray["tray_sub_brands"] = ""
                            tray["tray_color"] = ""
                            tray["tray_id_name"] = ""
                            tray["tag_uid"] = "0000000000000000"
                            tray["tray_uuid"] = "00000000000000000000000000000000"
                            tray["tray_info_idx"] = ""
                            tray["remain"] = 0
            except (ValueError, TypeError) as e:
                logger.debug("[%s] Could not parse tray_exist_bits: %s", self.serial_number, e)

        self.state.raw_data["ams"] = merged_ams

        # Update timestamp for RFID refresh detection (frontend can detect "new data arrived")
        self.state.last_ams_update = time.time()
        logger.debug("[%s] Merged AMS data: %s new units, %s total", self.serial_number, len(ams_list), len(merged_ams))

        # Extract ams_extruder_map from each AMS unit's info field
        # According to OpenBambuAPI: info field bit 8 indicates which extruder (0=right, 1=left)

        ams_extruder_map = {}
        for ams_unit in ams_list:
            ams_id = ams_unit.get("id")
            info = ams_unit.get("info")
            if ams_id is not None and info is not None:
                try:
                    info_val = int(info) if isinstance(info, str) else info
                    # Extract bit 8 for extruder assignment
                    # Bit 8 = 0 means LEFT extruder (id 1), bit 8 = 1 means RIGHT extruder (id 0)
                    # So we invert: extruder_id = 1 - bit8
                    bit8 = (info_val >> 8) & 0x1
                    extruder_id = 1 - bit8  # 0=right, 1=left
                    ams_extruder_map[str(ams_id)] = extruder_id
                    logger.debug(
                        f"[{self.serial_number}] AMS {ams_id} info={info_val} (bit8={bit8}) -> extruder {extruder_id}"
                    )
                except (ValueError, TypeError):
                    pass  # Skip AMS units with unparseable info bitmask values
        if ams_extruder_map:
            self.state.raw_data["ams_extruder_map"] = ams_extruder_map
            self.state.ams_extruder_map = ams_extruder_map  # Also set on state for inference logic
            logger.debug("[%s] ams_extruder_map: %s", self.serial_number, ams_extruder_map)

        # Create a hash of relevant AMS data to detect changes
        ams_hash_data = []
        for ams_unit in ams_list:
            for tray in ams_unit.get("tray", []):
                # Include fields that matter for filament tracking
                ams_hash_data.append(
                    f"{ams_unit.get('id')}:{tray.get('id')}:"
                    f"{tray.get('tray_type')}:{tray.get('tag_uid')}:{tray.get('remain')}"
                )
        ams_hash = hashlib.md5(":".join(ams_hash_data).encode(), usedforsecurity=False).hexdigest()

        # Only trigger callback if AMS data actually changed
        if ams_hash != self._previous_ams_hash:
            self._previous_ams_hash = ams_hash
            if self.on_ams_change:
                logger.info("[%s] AMS data changed, triggering sync callback", self.serial_number)
                self.on_ams_change(ams_list)

    def _update_state(self, data: dict):
        """Update printer state from message data."""
        _previous_state = self.state.state

        # Update state fields
        if "gcode_state" in data:
            self.state.state = data["gcode_state"]
        if "gcode_file" in data:
            self.state.gcode_file = data["gcode_file"]
            self.state.current_print = data["gcode_file"]
        if "subtask_name" in data:
            self.state.subtask_name = data["subtask_name"]
            # Prefer subtask_name as current_print if available
            if data["subtask_name"]:
                self.state.current_print = data["subtask_name"]
        if "subtask_id" in data:
            self.state.subtask_id = data["subtask_id"]
        if "mc_percent" in data:
            self.state.progress = float(data["mc_percent"])
        if "mc_remaining_time" in data:
            self.state.remaining_time = int(data["mc_remaining_time"])
        if "mc_print_sub_stage" in data:
            new_sub_stage = int(data["mc_print_sub_stage"])
            if new_sub_stage != self.state.mc_print_sub_stage:
                logger.debug(
                    f"[{self.serial_number}] mc_print_sub_stage changed: "
                    f"{self.state.mc_print_sub_stage} -> {new_sub_stage}"
                )
            self.state.mc_print_sub_stage = new_sub_stage
        if "layer_num" in data:
            new_layer = int(data["layer_num"])
            old_layer = self.state.layer_num
            self.state.layer_num = new_layer
            # Trigger layer change callback if layer increased
            if new_layer > old_layer and self.on_layer_change:
                self.on_layer_change(new_layer)
        if "total_layer_num" in data:
            self.state.total_layers = int(data["total_layer_num"])

        # Fan speeds (MQTT sends as string "0"-"15" representing speed levels, or percentage)
        # Convert to 0-100 percentage for display
        def parse_fan_speed(value: str | int | None) -> int | None:
            if value is None:
                return None
            try:
                speed = int(value)
                # MQTT reports 0-15 speed levels, convert to percentage (0-100)
                # 15 = 100%, so multiply by 100/15 ≈ 6.67
                if speed <= 15:
                    return round(speed * 100 / 15)
                # If already a percentage (0-255 scale from some printers), convert
                elif speed <= 255:
                    return round(speed * 100 / 255)
                return speed
            except (ValueError, TypeError):
                return None

        # Log fan fields once for debugging
        if not hasattr(self, "_fan_fields_logged"):
            fan_fields = {k: v for k, v in data.items() if "fan" in k.lower()}
            if fan_fields:
                logger.info("[%s] Fan fields in MQTT data: %s", self.serial_number, fan_fields)
                self._fan_fields_logged = True

        if "cooling_fan_speed" in data:
            self.state.cooling_fan_speed = parse_fan_speed(data["cooling_fan_speed"])
        if "big_fan1_speed" in data:
            self.state.big_fan1_speed = parse_fan_speed(data["big_fan1_speed"])
        if "big_fan2_speed" in data:
            self.state.big_fan2_speed = parse_fan_speed(data["big_fan2_speed"])
        if "heatbreak_fan_speed" in data:
            self.state.heatbreak_fan_speed = parse_fan_speed(data["heatbreak_fan_speed"])

        # Calibration stage tracking
        if "stg_cur" in data:
            new_stg = data["stg_cur"]
            # Always log ANY stg_cur change for debugging filament operations
            if new_stg != self.state.stg_cur:
                logger.info(
                    f"[{self.serial_number}] stg_cur changed: {self.state.stg_cur} -> {new_stg} ({get_stage_name(new_stg)})"
                )
            self.state.stg_cur = new_stg
        if "stg" in data:
            self.state.stg = data["stg"] if isinstance(data["stg"], list) else []

        # Temperature data
        temps = {}
        # Log all fields for debugging dual-nozzle temperature discovery (only once)
        if "bed_temper" in data and not hasattr(self, "_temp_fields_logged"):
            temp_fields = {k: v for k, v in data.items() if "temp" in k.lower() or "chamber" in k.lower()}
            logger.info("[%s] Temperature-related fields: %s", self.serial_number, temp_fields)
            # Log ALL keys in print data for H2D temperature discovery
            all_keys = sorted(data.keys())
            logger.info("[%s] ALL print data keys (%s): %s", self.serial_number, len(all_keys), all_keys)
            self._temp_fields_logged = True

        # Log vir_slot data (once) - this may contain per-extruder slot mapping for H2D
        if "vir_slot" in data and not hasattr(self, "_vir_slot_logged"):
            logger.info("[%s] vir_slot data: %s", self.serial_number, data["vir_slot"])
            self._vir_slot_logged = True

        # Log nozzle hardware info fields (once)
        nozzle_fields = {
            k: v
            for k, v in data.items()
            if "nozzle" in k.lower() or "hw" in k.lower() or "extruder" in k.lower() or "upgrade" in k.lower()
        }
        if nozzle_fields and not hasattr(self, "_nozzle_fields_logged"):
            logger.info("[%s] Nozzle/hardware fields in MQTT data: %s", self.serial_number, nozzle_fields)
            self._nozzle_fields_logged = True
        # Parse active extruder from device.extruder.state bit 8
        # bit 8 = 0 → RIGHT extruder (active_extruder=0)
        # bit 8 = 1 → LEFT extruder (active_extruder=1)
        if "device" in data and isinstance(data.get("device"), dict):
            device = data["device"]
            if "extruder" in device and "state" in device["extruder"]:
                state_val = device["extruder"]["state"]
                # Extract bit 8 for extruder position
                new_extruder = (state_val >> 8) & 0x1
                if new_extruder != self.state.active_extruder:
                    logger.info(
                        f"[{self.serial_number}] ACTIVE EXTRUDER CHANGED (state bit 8): {self.state.active_extruder} -> {new_extruder} (0=right, 1=left) [state={state_val}]"
                    )
                    self.state.active_extruder = new_extruder

        # Log device.extruder structure for active extruder
        if "device" in data and isinstance(data.get("device"), dict):
            device = data["device"]
            if "extruder" in device:
                ext_data = device["extruder"]
                # Log 'state' field - OrcaSlicer uses bits 12-14 for switch state
                if "state" in ext_data:
                    state_val = ext_data["state"]
                    # Extract bits 12-14 (3 bits) for switch state
                    switch_state = (state_val >> 12) & 0x7
                    logger.info(
                        f"[{self.serial_number}] device.extruder.state={state_val} (switch_state bits 12-14: {switch_state})"
                    )
                # Log 'cur' field if present (might indicate current/active extruder)
                if "cur" in ext_data:
                    logger.info("[%s] device.extruder.cur: %s", self.serial_number, ext_data["cur"])
        if "bed_temper" in data:
            temps["bed"] = float(data["bed_temper"])
        if "bed_target_temper" in data:
            temps["bed_target"] = float(data["bed_target_temper"])
        # Check if this is H2D (has device.extruder.info with 2 extruders)
        has_h2d_extruder_info = (
            "device" in data
            and isinstance(data.get("device"), dict)
            and "extruder" in data["device"]
            and isinstance(data["device"]["extruder"].get("info"), list)
            and len(data["device"]["extruder"]["info"]) >= 2
        )

        # Standard nozzle fields: these are for the RIGHT/default nozzle on H2D
        # For H2D, we use these for nozzle_2 (RIGHT), for others use as nozzle (primary)
        # NOTE: On H2D, nozzle_temper seems to mirror left nozzle - we override with extruder_info[0] later
        if "nozzle_temper" in data:
            if has_h2d_extruder_info:
                temps["nozzle_2"] = float(data["nozzle_temper"])  # Will be overridden by extruder_info[0]
            else:
                temps["nozzle"] = float(data["nozzle_temper"])
        if "nozzle_target_temper" in data:
            if has_h2d_extruder_info:
                temps["nozzle_2_target"] = float(data["nozzle_target_temper"])  # RIGHT target on H2D
            else:
                temps["nozzle_target"] = float(data["nozzle_target_temper"])
        # Second nozzle for dual-extruder printers - skip for H2D (uses device.extruder.info instead)
        if not has_h2d_extruder_info:
            # Try multiple possible field names used by different firmware versions
            if "nozzle_temper_2" in data:
                val = float(data["nozzle_temper_2"])
                if -50 < val < 500:  # Valid temp range
                    temps["nozzle_2"] = val
                else:
                    logger.debug("[%s] nozzle_temper_2=%s out of range", self.serial_number, val)
            elif "right_nozzle_temper" in data:
                val = float(data["right_nozzle_temper"])
                if -50 < val < 500:  # Valid temp range
                    temps["nozzle_2"] = val
                else:
                    logger.debug("[%s] right_nozzle_temper=%s out of range", self.serial_number, val)
            if "nozzle_target_temper_2" in data:
                val = float(data["nozzle_target_temper_2"])
                if 0 <= val < 500:  # Valid temp range
                    temps["nozzle_2_target"] = val
                else:
                    logger.debug("[%s] nozzle_target_temper_2=%s out of range", self.serial_number, val)
            elif "right_nozzle_target_temper" in data:
                val = float(data["right_nozzle_target_temper"])
                if 0 <= val < 500:  # Valid temp range
                    temps["nozzle_2_target"] = val
                else:
                    logger.debug("[%s] right_nozzle_target_temper=%s out of range", self.serial_number, val)
            # Also check for left nozzle as primary (some H2 models)
            if "left_nozzle_temper" in data and "nozzle" not in temps:
                temps["nozzle"] = float(data["left_nozzle_temper"])
            if "left_nozzle_target_temper" in data and "nozzle_target" not in temps:
                temps["nozzle_target"] = float(data["left_nozzle_target_temper"])
        if "chamber_temper" in data:
            chamber_val = float(data["chamber_temper"])
            logger.debug("[%s] chamber_temper raw value: %s", self.serial_number, chamber_val)
            # Check if we recently set the target locally (within 5 seconds)
            local_set_time = self.state.temperatures.get("_chamber_target_set_time", 0)
            respect_local = (time.time() - local_set_time) < 5.0
            # H2D protocol: chamber_temper encoding indicates heater state
            # - When > 500: encoded as (target * 65536 + current) - heater is ON
            # - When < 500: direct Celsius current temp only - heater is OFF
            if -50 < chamber_val < 100:
                # Direct value = heater is OFF
                temps["chamber"] = chamber_val
                if not respect_local:
                    temps["chamber_target"] = 0.0  # Heater off means target = 0
                    logger.debug("[%s] chamber_temper direct value: %s°C (heater OFF)", self.serial_number, chamber_val)
            else:
                logger.debug("[%s] chamber_temper %s out of direct range", self.serial_number, chamber_val)
                # Try to decode if it looks like an encoded value
                if chamber_val > 500:
                    mqtt_target = int(chamber_val) // 65536
                    current = int(chamber_val) % 65536
                    logger.debug(
                        f"[{self.serial_number}] chamber_temper decoded: mqtt_target={mqtt_target}, current={current}, respect_local={respect_local}"
                    )
                    if -50 < current < 100:
                        temps["chamber"] = float(current)
                    # Store decoded target for later use, but DON'T set chamber_heating here!
                    # Heating state will be calculated later after parsing ctc.info.target (explicit target)
                    # which is the authoritative source the slicer uses.
                    if not respect_local:
                        if 0 <= mqtt_target <= 60:
                            # Store as "decoded" target - may be overridden by explicit target fields
                            temps["_chamber_decoded_target"] = float(mqtt_target)
        # Chamber target temperature (set by print file or display)
        if "mc_target_cham" in data:
            mc_target = float(data["mc_target_cham"])
            logger.debug("[%s] mc_target_cham raw value: %s", self.serial_number, mc_target)
            # Filter out encoded/invalid values - valid chamber target is 0-60°C
            if 0 <= mc_target <= 60:
                temps["chamber_target"] = mc_target
        # H2D series: Chamber temp is in info.temp (may be encoded or direct °C)
        # NOTE: Don't set chamber_heating here - let ctc.info.target or fallback logic handle it
        # The encoded target in info.temp may be stale (slicer uses ctc.info.target as source of truth)
        try:
            if "info" in data and isinstance(data["info"], dict):
                info_temp = data["info"].get("temp")
                if info_temp is not None and "chamber" not in temps:
                    # Check for encoded value (target * 65536 + current)
                    if info_temp > 500:
                        # Decode: extract current temperature and target
                        target = info_temp // 65536
                        current = info_temp % 65536
                        temps["chamber"] = float(current)
                        # Store decoded target as fallback (may be overridden by ctc.info.target)
                        if "_chamber_decoded_target" not in temps:
                            temps["_chamber_decoded_target"] = float(target)
                        logger.debug(
                            f"[{self.serial_number}] info.temp encoded: {info_temp} -> current={current}, decoded_target={target}"
                        )
                    elif -50 < info_temp < 100:
                        # Valid direct temperature - heater is OFF
                        temps["chamber"] = float(info_temp)
                        temps["chamber_target"] = 0.0  # Direct value means heater off
                        logger.debug("[%s] info.temp direct: %s°C (heater OFF)", self.serial_number, info_temp)
            # H2D series: Dual extruder temps are in device.extruder.info array
            # Temperature values are encoded as fixed-point (value / 65536 = °C)
            if "device" in data and isinstance(data["device"], dict):
                device = data["device"]
                # Parse dual extruder temperatures
                extruder_data = device.get("extruder", {})
                extruder_info = extruder_data.get("info", [])
                if isinstance(extruder_info, list) and len(extruder_info) >= 1:
                    # H2D nozzle mapping: id=0 is RIGHT nozzle (default), id=1 is LEFT nozzle
                    # Only parse dual nozzle temps if this is actually a dual nozzle printer (H2D)
                    # has_h2d_extruder_info requires len(extruder_info) >= 2
                    if has_h2d_extruder_info:
                        # Right nozzle (extruder 0) - use extruder_info for actual temp, not nozzle_temper
                        # nozzle_temper field seems to mirror left nozzle on H2D, so use extruder_info[0]
                        if "temp" in extruder_info[0]:
                            temp_val = extruder_info[0]["temp"]
                            if temp_val > 500:
                                # Encoded format: temp = target * 65536 + current
                                target = temp_val // 65536
                                current = temp_val % 65536
                                if -50 < current < 500:
                                    temps["nozzle_2"] = float(current)
                                if 0 < target < 500:
                                    temps["nozzle_2_target"] = float(target)
                                temps["nozzle_2_heating"] = target > 0 and current < target
                            elif -50 < temp_val < 500:
                                # Direct Celsius value = heater is OFF
                                temps["nozzle_2"] = float(temp_val)
                                temps["nozzle_2_target"] = 0.0
                                temps["nozzle_2_heating"] = False
                    # Left nozzle (extruder 1) - only for dual nozzle printers
                    # H2D protocol: temp field encoding depends on value
                    # - When > 500: encoded as (target * 65536 + current) - heater is ON
                    # - When < 500: direct Celsius current temp only - heater is OFF
                    if len(extruder_info) >= 2 and "temp" in extruder_info[1]:
                        ext1 = extruder_info[1]
                        temp_val = ext1["temp"]

                        # Check if we recently set the target locally (within 5 seconds)
                        # If so, don't let MQTT data overwrite it
                        local_set_time = self.state.temperatures.get("_nozzle_target_set_time", 0)
                        respect_local_target = (time.time() - local_set_time) < 5.0

                        if temp_val > 500:
                            # Encoded format: temp = target * 65536 + current
                            target = temp_val // 65536
                            current = temp_val % 65536
                            if 0 < target < 500 and not respect_local_target:
                                temps["nozzle_target"] = float(target)
                            if -50 < current < 500:
                                temps["nozzle"] = float(current)
                            # Heating = encoded AND we're using the MQTT target (not local override)
                            # If local target is being respected, use local target to determine heating
                            if respect_local_target:
                                local_target = self.state.temperatures.get("nozzle_target", 0)
                                temps["nozzle_heating"] = local_target > 0 and current < local_target
                            else:
                                temps["nozzle_heating"] = target > 0 and current < target
                        elif -50 < temp_val < 500:
                            # Direct Celsius = heater is OFF (or at target with heater off)
                            temps["nozzle"] = float(temp_val)
                            if not respect_local_target:
                                temps["nozzle_target"] = 0.0
                            temps["nozzle_heating"] = False  # Direct = not heating
                    # Parse H2D snow field (slot now) for accurate tray_now disambiguation
                    # snow encodes AMS ID in high byte: ams_id = snow >> 8, slot = snow & 0xFF
                    if has_h2d_extruder_info:
                        for ext_info in extruder_info:
                            ext_id = ext_info.get("id")
                            snow = ext_info.get("snow")
                            if ext_id is not None and snow is not None and ext_id <= 1:
                                # Normalize H2D snow value to global tray ID
                                ams_id = snow >> 8
                                slot = snow & 0xFF
                                if 0 <= ams_id <= 3:
                                    # Regular AMS slot
                                    global_tray = ams_id * 4 + (slot & 0x03)
                                    old_val = self.state.h2d_extruder_snow.get(ext_id)
                                    if old_val != global_tray:
                                        logger.info(
                                            f"[{self.serial_number}] H2D extruder[{ext_id}] snow: "
                                            f"raw={snow} (AMS {ams_id} slot {slot}) -> global tray {global_tray}"
                                        )
                                    self.state.h2d_extruder_snow[ext_id] = global_tray
                                elif ams_id == 254 or ams_id == 255:
                                    # External spool or unloaded
                                    normalized = 254 if slot != 255 else 255
                                    old_val = self.state.h2d_extruder_snow.get(ext_id)
                                    if old_val != normalized:
                                        logger.info(
                                            f"[{self.serial_number}] H2D extruder[{ext_id}] snow: "
                                            f"raw={snow} -> {'external' if normalized == 254 else 'unloaded'}"
                                        )
                                    self.state.h2d_extruder_snow[ext_id] = normalized
                                elif 128 <= ams_id <= 135:
                                    # External spool with hub mapping
                                    old_val = self.state.h2d_extruder_snow.get(ext_id)
                                    if old_val != ams_id:
                                        logger.info(
                                            f"[{self.serial_number}] H2D extruder[{ext_id}] snow: "
                                            f"raw={snow} -> external hub {ams_id}"
                                        )
                                    self.state.h2d_extruder_snow[ext_id] = ams_id
                # Parse bed heating state from device.bed.info.temp encoding
                # temp > 500 means encoded (target*65536+current), heating = target > 0 AND current < target
                bed_data = device.get("bed", {})
                bed_info = bed_data.get("info", {})
                if "temp" in bed_info:
                    temp_val = bed_info["temp"]
                    if temp_val > 500:
                        target = temp_val // 65536
                        current = temp_val % 65536
                        temps["bed_heating"] = target > 0 and current < target
                    else:
                        temps["bed_heating"] = False
                # Parse chamber temp from device.ctc.info.temp if not already set
                ctc_data = device.get("ctc", {})
                ctc_info = ctc_data.get("info", {})
                # Parse airduct mode (0=cooling, 1=heating)
                airduct_data = device.get("airduct", {})
                if "modeCur" in airduct_data:
                    new_mode = airduct_data["modeCur"]
                    if new_mode != self.state.airduct_mode:
                        logger.info(
                            f"[{self.serial_number}] airduct_mode changed: {self.state.airduct_mode} -> {new_mode}"
                        )
                    self.state.airduct_mode = new_mode
                # Parse chamber temp - may be encoded as (target*65536+current) when > 500
                # Check if we recently set the target locally (within 5 seconds)
                local_set_time = self.state.temperatures.get("_chamber_target_set_time", 0)
                respect_local_target = (time.time() - local_set_time) < 5.0

                # Log ctc_info contents for debugging
                if ctc_info:
                    logger.debug("[%s] ctc_info keys: %s", self.serial_number, list(ctc_info.keys()))

                # FIRST: Parse explicit ctc.info.target if available - this is the authoritative target
                # (what the slicer shows). This OVERRIDES any previously decoded target.
                explicit_target = None
                if "target" in ctc_info:
                    target_val = ctc_info["target"]
                    logger.debug(
                        f"[{self.serial_number}] ctc_info.target explicit value: {target_val}, respect_local={respect_local_target}"
                    )
                    # Filter out invalid values (valid chamber target is 0-60°C)
                    if 0 <= target_val <= 60 and not respect_local_target:
                        explicit_target = float(target_val)
                        temps["chamber_target"] = explicit_target  # Override any previous value
                        logger.debug(
                            f"[{self.serial_number}] Setting chamber_target from ctc_info.target: {explicit_target}"
                        )

                # Parse chamber temp from ctc.info.temp - may be encoded
                if "temp" in ctc_info and "chamber" not in temps:
                    temp_val = ctc_info["temp"]
                    logger.debug("[%s] ctc_info.temp raw value: %s", self.serial_number, temp_val)
                    if temp_val > 500:
                        # Encoded value: decode target and current
                        decoded_target = temp_val // 65536
                        current = temp_val % 65536
                        temps["chamber"] = float(current)
                        logger.debug(
                            f"[{self.serial_number}] ctc_info.temp decoded: target={decoded_target}, current={current}, explicit_target={explicit_target}"
                        )

                        # Determine which target to use for heating state:
                        # Priority: local target > explicit target > decoded target
                        if respect_local_target:
                            local_target = self.state.temperatures.get("chamber_target", 0)
                            temps["chamber_heating"] = local_target > 0 and current < local_target
                        elif explicit_target is not None:
                            # Use explicit ctc.info.target - this is what slicer sees
                            temps["chamber_heating"] = explicit_target > 0 and current < explicit_target
                        else:
                            # Fallback to decoded target only if no explicit target available
                            if not respect_local_target and "chamber_target" not in temps:
                                temps["chamber_target"] = float(decoded_target)
                            temps["chamber_heating"] = decoded_target > 0 and current < decoded_target
                    else:
                        # Direct value (not encoded) - heater is OFF
                        temps["chamber"] = float(temp_val)
                        temps["chamber_heating"] = False
        except Exception as e:
            logger.warning("[%s] Error parsing H2D temperatures: %s", self.serial_number, e)
        if temps:
            # Handle chamber_target: prefer explicit over decoded
            if "_chamber_decoded_target" in temps and "chamber_target" not in temps:
                # No explicit target available, use decoded target from chamber_temper
                temps["chamber_target"] = temps["_chamber_decoded_target"]
            # Remove internal temp key before merging
            temps.pop("_chamber_decoded_target", None)

            # Merge new temps into existing, preserving valid values when new ones are filtered out
            for key, value in temps.items():
                self.state.temperatures[key] = value

            # Calculate chamber_heating after all targets are known
            # Priority: local target (if recent) > explicit target (chamber_target) > 0
            if "chamber" in temps and "chamber_heating" not in temps:
                current = self.state.temperatures.get("chamber", 0)
                local_set_time = self.state.temperatures.get("_chamber_target_set_time", 0)
                respect_local = (time.time() - local_set_time) < 5.0

                if respect_local:
                    # Use locally-set target
                    target = self.state.temperatures.get("chamber_target", 0)
                else:
                    # Use explicit/decoded target from MQTT
                    target = self.state.temperatures.get("chamber_target", 0)

                self.state.temperatures["chamber_heating"] = target > 0 and current < target
                logger.debug(
                    f"[{self.serial_number}] Chamber heating calculated: target={target}, current={current}, heating={self.state.temperatures['chamber_heating']}, respect_local={respect_local}"
                )

            # Debug: log chamber value if it was updated
            if "chamber" in temps:
                logger.debug(
                    f"[{self.serial_number}] Chamber temp updated to: {self.state.temperatures.get('chamber')}, target: {self.state.temperatures.get('chamber_target')}, heating: {self.state.temperatures.get('chamber_heating')}"
                )

            # Calculate nozzle_heating for single nozzle printers (not set by H2D parsing)
            # For H2D, nozzle_heating is set in temps dict; for single nozzle, calculate here
            if "nozzle" in temps and "nozzle_heating" not in temps:
                current = self.state.temperatures.get("nozzle", 0)
                target = self.state.temperatures.get("nozzle_target", 0)
                self.state.temperatures["nozzle_heating"] = target > 0 and current < target

        # Parse HMS (Health Management System) errors
        if "hms" in data:
            hms_list = data["hms"]
            logger.info("[%s] HMS data received: %s", self.serial_number, hms_list)
            self.state.hms_errors = []
            if isinstance(hms_list, list):
                for hms in hms_list:
                    if isinstance(hms, dict):
                        # HMS format: {"attr": attribute_code, "code": error_code}
                        # attr contains module/severity info, code contains error number
                        # Both are needed to construct the wiki URL
                        attr = hms.get("attr", 0)
                        code = hms.get("code", 0)
                        if isinstance(attr, str):
                            attr = int(attr.replace("0x", ""), 16) if attr else 0
                        if isinstance(code, str):
                            code = int(code.replace("0x", ""), 16) if code else 0
                        # Severity is in attr byte 1 (bits 8-15)
                        severity = (attr >> 8) & 0xF
                        # Module is in attr byte 3 (bits 24-31)
                        module = (attr >> 24) & 0xFF
                        self.state.hms_errors.append(
                            HMSError(
                                code=f"0x{code:x}" if code else "0x0",
                                attr=attr,
                                module=module,
                                severity=severity if severity > 0 else 2,
                            )
                        )

        # Parse print_error - this is a different error format than HMS
        # print_error is a 32-bit integer where:
        #   - High 16 bits contain module info (e.g., 0x0500)
        #   - Low 16 bits contain error code (e.g., 0x8061)
        # Format on printer screen: [0500-8061] -> short code: 0500_8061
        if "print_error" in data:
            print_error = data["print_error"]
            if print_error and print_error != 0:
                # Extract components: MMMMEEEE -> MMMM_EEEE
                module = (print_error >> 16) & 0xFFFF  # High 16 bits (e.g., 0x0500)
                error = print_error & 0xFFFF  # Low 16 bits (e.g., 0x8061)

                # Store in a format that matches the community error database
                # attr stores the full 32-bit value for reconstruction
                # code stores the short format string for lookup
                short_code = f"{module:04X}_{error:04X}"

                logger.info(
                    f"[{self.serial_number}] print_error: {print_error} (0x{print_error:08x}) -> short_code={short_code}"
                )

                # Only add if not already in HMS errors (avoid duplicates)
                existing_short_codes = set()
                for e in self.state.hms_errors:
                    # Extract short code from existing errors
                    e_module = (e.attr >> 16) & 0xFFFF
                    e_error = int(e.code.replace("0x", ""), 16) if e.code else 0
                    existing_short_codes.add(f"{e_module:04X}_{e_error:04X}")

                if short_code not in existing_short_codes:
                    self.state.hms_errors.append(
                        HMSError(
                            code=f"0x{error:x}",
                            attr=print_error,  # Store full value for display
                            module=module >> 8,  # High byte of module (e.g., 0x05)
                            severity=3,  # Warning level for print_error
                        )
                    )

        # Parse SD card status
        if "sdcard" in data:
            self.state.sdcard = data["sdcard"] is True

        # Parse home_flag for "Store Sent Files on External Storage" setting (bit 11)
        if "home_flag" in data:
            home_flag = data["home_flag"]
            # Bit 11 controls "Store Sent Files on External Storage"
            # Convert to unsigned 32-bit if negative
            if home_flag < 0:
                home_flag = home_flag & 0xFFFFFFFF
            store_to_sdcard = bool((home_flag >> 11) & 1)
            if store_to_sdcard != self.state.store_to_sdcard:
                logger.info(
                    f"[{self.serial_number}] store_to_sdcard changed: {self.state.store_to_sdcard} -> {store_to_sdcard}"
                )
            self.state.store_to_sdcard = store_to_sdcard

        # Parse timelapse status (recording active during print)
        if "timelapse" in data:
            logger.debug("[%s] timelapse field: %s", self.serial_number, data["timelapse"])
            self.state.timelapse = data["timelapse"] is True
            # Track if timelapse was ever active during this print
            if self.state.timelapse and self._was_running:
                self._timelapse_during_print = True

        # Parse ipcam/live view status
        if "ipcam" in data:
            ipcam_data = data["ipcam"]
            logger.debug("[%s] ipcam field: %s", self.serial_number, ipcam_data)
            if isinstance(ipcam_data, dict):
                # Check ipcam_record field for live view status
                self.state.ipcam = ipcam_data.get("ipcam_record") == "enable"
                # Check timelapse field (H2D sends it here, not in xcam)
                if "timelapse" in ipcam_data:
                    timelapse_enabled = ipcam_data.get("timelapse") == "enable"
                    if timelapse_enabled != self.state.timelapse:
                        logger.info(
                            f"[{self.serial_number}] timelapse changed (from ipcam): {self.state.timelapse} -> {timelapse_enabled}"
                        )
                    self.state.timelapse = timelapse_enabled
                    # Track if timelapse was ever active during this print
                    if self.state.timelapse and self._was_running:
                        self._timelapse_during_print = True
                        logger.info("[%s] Timelapse detected during print (from ipcam)", self.serial_number)
            else:
                self.state.ipcam = ipcam_data is True

        # Parse WiFi signal strength (dBm)
        if "wifi_signal" in data:
            wifi_signal = data["wifi_signal"]
            logger.info("[%s] wifi_signal received: %s", self.serial_number, wifi_signal)
            if isinstance(wifi_signal, (int, float)):
                self.state.wifi_signal = int(wifi_signal)
            elif isinstance(wifi_signal, str):
                # Handle string format like "-52dBm"
                try:
                    self.state.wifi_signal = int(wifi_signal.replace("dBm", "").strip())
                except ValueError:
                    pass  # Ignore unparseable wifi_signal strings; field is non-critical

        # Parse print speed level (1=silent, 2=standard, 3=sport, 4=ludicrous)
        if "spd_lvl" in data:
            new_speed = data["spd_lvl"]
            if new_speed != self.state.speed_level:
                logger.info("[%s] speed_level changed: %s -> %s", self.serial_number, self.state.speed_level, new_speed)
            self.state.speed_level = new_speed

        # Parse skipped objects from printer status (s_obj field)
        # This allows us to restore skipped objects state after reconnection
        if "s_obj" in data:
            s_obj = data["s_obj"]
            if isinstance(s_obj, list):
                # Update skipped objects from printer's list
                new_skipped = [int(oid) for oid in s_obj if isinstance(oid, (int, str))]
                if new_skipped != self.state.skipped_objects:
                    logger.info("[%s] skipped_objects updated from printer: %s", self.serial_number, new_skipped)
                    self.state.skipped_objects = new_skipped

        # Parse chamber light status from lights_report
        if "lights_report" in data:
            lights = data["lights_report"]
            logger.debug("[%s] lights_report: %s", self.serial_number, lights)
            if isinstance(lights, list):
                for light in lights:
                    if isinstance(light, dict) and light.get("node") == "chamber_light":
                        new_light_state = light.get("mode") == "on"
                        if new_light_state != self.state.chamber_light:
                            logger.info(
                                f"[{self.serial_number}] chamber_light changed: {self.state.chamber_light} -> {new_light_state}"
                            )
                        self.state.chamber_light = new_light_state
                        break

        # Parse nozzle hardware info (single nozzle printers)
        if "nozzle_type" in data:
            self.state.nozzles[0].nozzle_type = str(data["nozzle_type"])
        if "nozzle_diameter" in data:
            self.state.nozzles[0].nozzle_diameter = str(data["nozzle_diameter"])

        # Parse nozzle hardware info (dual nozzle printers - H2D series)
        # Left nozzle
        if "left_nozzle_type" in data:
            self.state.nozzles[0].nozzle_type = str(data["left_nozzle_type"])
        if "left_nozzle_diameter" in data:
            self.state.nozzles[0].nozzle_diameter = str(data["left_nozzle_diameter"])
        # Right nozzle
        if "right_nozzle_type" in data:
            self.state.nozzles[1].nozzle_type = str(data["right_nozzle_type"])
        if "right_nozzle_diameter" in data:
            self.state.nozzles[1].nozzle_diameter = str(data["right_nozzle_diameter"])

        # Alternative format for dual nozzle (nozzle_type_2, etc.)
        if "nozzle_type_2" in data:
            self.state.nozzles[1].nozzle_type = str(data["nozzle_type_2"])
        if "nozzle_diameter_2" in data:
            self.state.nozzles[1].nozzle_diameter = str(data["nozzle_diameter_2"])

        # H2D/H2C series: Nozzle hardware info is in device.nozzle.info array
        if "device" in data and isinstance(data["device"], dict):
            device = data["device"]
            nozzle_data = device.get("nozzle", {})
            nozzle_info = nozzle_data.get("info", [])
            if isinstance(nozzle_info, list):
                # H2 series: nozzle_info contains extended nozzle data (wear, serial,
                # max_temp, etc.) for all nozzles: L/R hotend (IDs 0,1) and rack slots
                # (IDs 16-21 on H2C). Store ALL entries so the frontend can use them
                # for hover cards on both the L/R indicator and the nozzle rack card.
                if nozzle_info:
                    self.state.nozzle_rack = sorted(
                        [
                            {
                                "id": n.get("id", i),
                                "type": str(n.get("type", "")),
                                "diameter": str(n.get("diameter", "")),
                                "wear": n.get("wear"),
                                "stat": n.get("stat"),
                                # H2C uses "tm", H2D uses "max_temp"
                                "max_temp": n.get("max_temp") or n.get("tm", 0),
                                # H2C uses "sn", H2D uses "serial_number"
                                "serial_number": str(n.get("serial_number") or n.get("sn", "")),
                                # H2C uses "color_m", H2D uses "filament_colour"
                                "filament_color": str(n.get("filament_colour") or n.get("color_m", "")),
                                # H2C uses "fila_id", H2D uses "filament_id"
                                "filament_id": str(n.get("filament_id") or n.get("fila_id", "")),
                                "filament_type": str(n.get("tray_type", "") or n.get("filament_type", "")),
                            }
                            for i, n in enumerate(nozzle_info)
                        ],
                        key=lambda x: x["id"],
                    )
                    if not hasattr(self, "_nozzle_rack_logged") and nozzle_info:
                        self._nozzle_rack_logged = True
                        logger.info(
                            "[%s] Nozzle info: %d entries, IDs: %s",
                            self.serial_number,
                            len(nozzle_info),
                            [n.get("id") for n in nozzle_info],
                        )
                for nozzle in nozzle_info:
                    idx = nozzle.get("id", 0)
                    if idx < len(self.state.nozzles):
                        if "type" in nozzle and nozzle["type"]:
                            self.state.nozzles[idx].nozzle_type = str(nozzle["type"])
                        if "diameter" in nozzle:
                            self.state.nozzles[idx].nozzle_diameter = str(nozzle["diameter"])

        # Preserve AMS, vt_tray, and ams_extruder_map data when updating raw_data
        ams_data = self.state.raw_data.get("ams")
        vt_tray_data = self.state.raw_data.get("vt_tray")
        ams_extruder_map_data = self.state.raw_data.get("ams_extruder_map")
        self.state.raw_data = data
        if ams_data is not None:
            self.state.raw_data["ams"] = ams_data
        if vt_tray_data is not None:
            self.state.raw_data["vt_tray"] = vt_tray_data
        if ams_extruder_map_data is not None:
            self.state.raw_data["ams_extruder_map"] = ams_extruder_map_data

        # Log state transitions for debugging
        if "gcode_state" in data:
            logger.debug(
                f"[{self.serial_number}] gcode_state: {self._previous_gcode_state} -> {self.state.state}, "
                f"file: {self.state.gcode_file}, subtask: {self.state.subtask_name}"
            )

        # Detect print start (state changes TO RUNNING with a file)
        current_file = self.state.gcode_file or self.state.current_print
        is_new_print = (
            self.state.state == "RUNNING"
            and self._previous_gcode_state != "RUNNING"
            and current_file
            and not self._was_running  # Prevent duplicates when resuming from PAUSE
        )
        # Also detect if file changed while running (new print started)
        is_file_change = (
            self.state.state == "RUNNING"
            and current_file
            and current_file != self._previous_gcode_file
            and self._previous_gcode_file is not None
        )

        # Track RUNNING state for more robust completion detection
        if self.state.state == "RUNNING" and current_file:
            if not self._was_running:
                logger.info("[%s] Now tracking RUNNING state for %s", self.serial_number, current_file)
                # Check if timelapse was enabled in the same message (xcam parsed before this)
                if self.state.timelapse:
                    self._timelapse_during_print = True
                    logger.info("[%s] Timelapse detected when entering RUNNING state", self.serial_number)
            self._was_running = True
            self._completion_triggered = False

        if is_new_print or is_file_change:
            # Clear any old HMS errors when a new print starts
            self.state.hms_errors = []
            # Reset layer tracking for new print (needed for layer-based timelapse)
            self.state.layer_num = 0
            # Reset completion tracking for new print
            self._was_running = True
            self._completion_triggered = False
            # Initialize timelapse tracking based on current state
            # NOTE: xcam data is parsed BEFORE this code runs in _process_message,
            # so self.state.timelapse may already be set from this message.
            # We preserve that value instead of blindly resetting to False.
            if self.state.timelapse:
                self._timelapse_during_print = True
                logger.info("[%s] Timelapse detected at print start", self.serial_number)
            else:
                self._timelapse_during_print = False

        if (is_new_print or is_file_change) and self.on_print_start:
            logger.info(
                f"[{self.serial_number}] PRINT START detected - file: {current_file}, "
                f"subtask: {self.state.subtask_name}, is_new: {is_new_print}, is_file_change: {is_file_change}"
            )
            self.on_print_start(
                {
                    "filename": current_file,
                    "subtask_name": self.state.subtask_name,
                    "remaining_time": self.state.remaining_time * 60
                    if self.state.remaining_time > 0
                    else None,  # Convert minutes to seconds
                    "raw_data": data,
                }
            )

        # Detect print completion (FINISH = success, FAILED = error, IDLE = aborted)
        # Use _was_running flag in addition to _previous_gcode_state for more robust detection
        # This handles cases where server restarts during a print
        should_trigger_completion = (
            self.state.state in ("FINISH", "FAILED")
            and not self._completion_triggered
            and self.on_print_complete
            and (
                self._previous_gcode_state == "RUNNING"  # Normal transition
                or (self._was_running and self._previous_gcode_state != self.state.state)  # After server restart
            )
        )
        # For IDLE, only trigger if we just came from RUNNING (explicit abort/cancel)
        if (
            self.state.state == "IDLE"
            and self._previous_gcode_state == "RUNNING"
            and not self._completion_triggered
            and self.on_print_complete
        ):
            should_trigger_completion = True

        if should_trigger_completion:
            if self.state.state == "FINISH":
                status = "completed"
            elif self.state.state == "FAILED":
                status = "failed"
            else:
                status = "aborted"
            logger.info(
                f"[{self.serial_number}] PRINT COMPLETE detected - state: {self.state.state}, "
                f"status: {status}, file: {self._previous_gcode_file or current_file}, "
                f"subtask: {self.state.subtask_name}, was_running: {self._was_running}, "
                f"timelapse_during_print: {self._timelapse_during_print}"
            )
            timelapse_was_active = self._timelapse_during_print
            self._completion_triggered = True
            self._was_running = False
            self._timelapse_during_print = False  # Reset for next print
            # Include HMS errors for failure reason detection
            hms_errors_data = (
                [
                    {"code": e.code, "attr": e.attr, "module": e.module, "severity": e.severity}
                    for e in self.state.hms_errors
                ]
                if self.state.hms_errors
                else []
            )
            self.on_print_complete(
                {
                    "status": status,
                    "filename": self._previous_gcode_file or current_file,
                    "subtask_name": self.state.subtask_name,
                    "raw_data": data,
                    "timelapse_was_active": timelapse_was_active,
                    "hms_errors": hms_errors_data,
                }
            )

        self._previous_gcode_state = self.state.state
        if current_file:
            self._previous_gcode_file = current_file

        if self.on_state_change:
            self.on_state_change(self.state)

    def _request_push_all(self):
        """Request full status update from printer."""
        if self._client:
            message = {"pushing": {"command": "pushall"}}
            self._client.publish(self.topic_publish, json.dumps(message), qos=1)

    def _request_version(self):
        """Request firmware version info from printer."""
        if self._client:
            self._sequence_id += 1
            message = {
                "info": {
                    "sequence_id": str(self._sequence_id),
                    "command": "get_version",
                }
            }
            logger.debug("[%s] Requesting firmware version info", self.serial_number)
            self._client.publish(self.topic_publish, json.dumps(message), qos=1)

    def request_status_update(self) -> bool:
        """Request a full status update from the printer (public API).

        Sends both pushall and get_accessories commands to refresh all data
        including nozzle hardware info.

        Returns:
            True if the request was sent, False if not connected.
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] request_status_update: not connected", self.serial_number)
            return False
        logger.info("[%s] Requesting status update (pushall)", self.serial_number)
        self._request_push_all()
        # Note: get_accessories returns stale nozzle data on H2D.
        # The correct nozzle data comes from push_status response.
        return True

    def _request_accessories(self):
        """Request accessories info (nozzle type, etc.) from printer."""
        if self._client:
            self._sequence_id += 1
            message = {
                "system": {
                    "sequence_id": str(self._sequence_id),
                    "command": "get_accessories",
                    "accessory_type": "none",
                }
            }
            logger.debug("[%s] Requesting accessories info", self.serial_number)
            self._client.publish(self.topic_publish, json.dumps(message), qos=1)

    def _prime_kprofile_request(self):
        """Send a priming K-profile request on connect.

        Bambu printers often ignore the first K-profile request after connection,
        so we send a dummy request on connect to 'prime' the system.
        """
        if self._client:
            self._sequence_id += 1
            command = {
                "print": {
                    "command": "extrusion_cali_get",
                    "filament_id": "",
                    "nozzle_diameter": "0.4",
                    "sequence_id": str(self._sequence_id),
                }
            }
            logger.debug("[%s] Sending K-profile priming request", self.serial_number)
            self._client.publish(self.topic_publish, json.dumps(command), qos=1)

    def connect(self, loop: asyncio.AbstractEventLoop | None = None):
        """Connect to the printer MQTT broker.

        Args:
            loop: The asyncio event loop to use for thread-safe callbacks.
                  If not provided, will try to get the running loop.
        """
        self._loop = loop
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"bambuddy_{self.serial_number}",
            protocol=mqtt.MQTTv311,
        )

        self._client.username_pw_set("bblp", self.access_code)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        # TLS setup - Bambu uses self-signed certs
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        self._client.tls_set_context(ssl_context)

        # Use shorter keepalive (15s) for faster disconnect detection
        # Paho considers connection lost after 1.5x keepalive with no response
        self._client.connect_async(self.ip_address, self.MQTT_PORT, keepalive=15)
        self._client.loop_start()

    def start_print(
        self,
        filename: str,
        plate_id: int = 1,
        ams_mapping: list[int] | None = None,
        bed_levelling: bool = True,
        flow_cali: bool = False,
        vibration_cali: bool = True,
        layer_inspect: bool = False,
        timelapse: bool = False,
        use_ams: bool = True,
    ) -> bool:
        """Start a print job on the printer.

        The file should already be uploaded to the printer's root directory via FTP.

        Args:
            filename: Name of the uploaded file
            plate_id: Plate number to print (default 1)
            ams_mapping: List of tray IDs for each filament slot in the 3MF.
                         Global tray ID = (ams_id * 4) + slot_id, external = 254
            timelapse: Record timelapse video
            bed_levelling: Auto bed levelling before print
            flow_cali: Flow/pressure advance calibration
            vibration_cali: Vibration compensation calibration
            layer_inspect: First layer AI inspection
            use_ams: Use AMS for automatic filament changes
        """
        if self._client and self.state.connected:
            # Bambu print command format - matches Bambu Studio's format
            # Build ams_mapping2 from ams_mapping (detailed format with ams_id/slot_id)
            ams_mapping2 = []
            if ams_mapping is not None:
                for tray_id in ams_mapping:
                    # Ensure tray_id is an integer (may be string from JSON)
                    tray_id = int(tray_id) if tray_id is not None else -1
                    if tray_id == -1:
                        # Unmapped filament slot
                        ams_mapping2.append({"ams_id": 255, "slot_id": 255})
                    elif tray_id >= 254:
                        # External spool: 254 = main nozzle, 255 = deputy nozzle
                        # For ams_mapping2, slot_id is 0 (main) or 1 (deputy), not the tray_id
                        external_slot = 0 if tray_id == 254 else 1
                        ams_mapping2.append({"ams_id": 255, "slot_id": external_slot})
                    elif tray_id >= 128:
                        # AMS-HT: global tray ID IS the ams_id (single tray per unit)
                        ams_mapping2.append({"ams_id": tray_id, "slot_id": 0})
                    else:
                        # Regular AMS tray: Global tray ID = (ams_id * 4) + slot_id
                        ams_id = tray_id // 4
                        slot_id = tray_id % 4
                        ams_mapping2.append({"ams_id": ams_id, "slot_id": slot_id})

            # H2D series requires integer values (0/1) for calibration/leveling fields
            # but use_ams MUST remain boolean — H2D Pro firmware interprets integer
            # values as nozzle index (1 = deputy nozzle), causing wrong extruder routing
            # Other printers (X1C, P1S, A1, etc.) require actual booleans for all fields
            is_h2d = self.model and self.model.upper().strip() in ("H2D", "H2D PRO", "H2DPRO", "H2C", "H2S")

            command = {
                "print": {
                    "sequence_id": "20000",
                    "command": "project_file",
                    "param": f"Metadata/plate_{plate_id}.gcode",
                    "url": f"ftp://{filename}",
                    "file": filename,
                    "md5": "",
                    "bed_type": "auto",
                    "timelapse": (1 if timelapse else 0) if is_h2d else timelapse,
                    "bed_leveling": (1 if bed_levelling else 0) if is_h2d else bed_levelling,
                    "auto_bed_leveling": (1 if bed_levelling else 0) if is_h2d else bed_levelling,
                    "flow_cali": (1 if flow_cali else 0) if is_h2d else flow_cali,
                    "vibration_cali": (1 if vibration_cali else 0) if is_h2d else vibration_cali,
                    "layer_inspect": (1 if layer_inspect else 0) if is_h2d else layer_inspect,
                    "use_ams": use_ams,
                    "cfg": "0",
                    "extrude_cali_flag": 0,
                    "extrude_cali_manual_mode": 0,
                    "nozzle_offset_cali": 2,
                    "subtask_name": filename.replace(".3mf", "").replace(".gcode", ""),
                    "profile_id": "0",
                    "project_id": "0",
                    "subtask_id": "0",
                    "task_id": "0",
                }
            }

            if is_h2d:
                logger.info(
                    "[%s] H2D series detected: using integer format for calibration fields (use_ams stays boolean)",
                    self.serial_number,
                )

            # P2S-specific parameter adjustments
            # P2S printer doesn't support vibration calibration like X1/P1 series
            if self.model and self.model.upper().strip() in ("P2S", "N7"):
                command["print"]["vibration_cali"] = False
                logger.info("[%s] P2S detected: disabling vibration_cali", self.serial_number)

            # Add AMS mapping if provided
            if ams_mapping is not None:
                command["print"]["ams_mapping"] = ams_mapping
                command["print"]["ams_mapping2"] = ams_mapping2

            logger.info("[%s] Sending print command: %s", self.serial_number, json.dumps(command))
            try:
                result = self._client.publish(self.topic_publish, json.dumps(command), qos=1)
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    logger.error(
                        "[%s] Failed to publish print command: rc=%d (%s)",
                        self.serial_number,
                        result.rc,
                        mqtt.error_string(result.rc),
                    )
                    return False
            except Exception as e:
                logger.error("[%s] Exception publishing print command: %s", self.serial_number, e, exc_info=True)
                return False
            return True
        else:
            # Log why we couldn't send the command
            if not self._client:
                logger.error("[%s] Cannot start print: MQTT client not initialized", self.serial_number)
            elif not self.state.connected:
                logger.error(
                    f"[{self.serial_number}] Cannot start print: Printer not connected (client exists but disconnected). "
                    f"Connection state: {self.state.connected}, Last message: {self._last_message_time}"
                )
            return False

    def stop_print(self) -> bool:
        """Stop the current print job."""
        if self._client and self.state.connected:
            command = {"print": {"command": "stop", "sequence_id": "0"}}
            try:
                result = self._client.publish(self.topic_publish, json.dumps(command), qos=1)
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    logger.error(
                        "[%s] Failed to publish stop command: rc=%d (%s)",
                        self.serial_number,
                        result.rc,
                        mqtt.error_string(result.rc),
                    )
                    return False
            except Exception as e:
                logger.error("[%s] Exception publishing stop command: %s", self.serial_number, e, exc_info=True)
                return False
            logger.info("[%s] Sent stop print command", self.serial_number)
            return True
        return False

    def set_xcam_option(
        self, module_name: str, enabled: bool, print_halt: bool = True, sensitivity: str = "medium"
    ) -> bool:
        """Set an xcam (AI detection) option on the printer.

        Args:
            module_name: The xcam module to control (e.g., "spaghetti_detector",
                        "first_layer_inspector", "printing_monitor", "buildplate_marker_detector")
            enabled: Whether to enable or disable the feature
            print_halt: Whether to halt print on detection (only applies to some detectors)
            sensitivity: Sensitivity level ("low", "medium", "high", or "never_halt")

        Returns:
            True if command was sent, False if not connected
        """
        if not self._client or not self.state.connected:
            return False

        # auto_recovery_step_loss uses a different command format (print.print_option)
        if module_name == "auto_recovery_step_loss":
            return self._set_print_option("auto_recovery", enabled)

        self._sequence_id += 1

        # Build the xcam control command (exact OrcaSlicer format)
        # Key findings from OrcaSlicer source:
        # - Uses "xcam" wrapper (not "print")
        # - print_halt is ALWAYS true (legacy protocol requirement)
        # - Both "control" and "enable" are set to the same value
        # - halt_print_sensitivity controls actual halt behavior
        command = {
            "xcam": {
                "command": "xcam_control_set",
                "sequence_id": str(self._sequence_id),
                "module_name": module_name,
                "control": enabled,
                "enable": enabled,  # old protocol compatibility
                "print_halt": True,  # ALWAYS true per OrcaSlicer
            }
        }

        # Only add sensitivity if not "never_halt"
        # OrcaSlicer uses halt_print_sensitivity for ALL detectors
        # The module_name field determines which detector's sensitivity is being set
        if sensitivity and sensitivity != "never_halt":
            command["xcam"]["halt_print_sensitivity"] = sensitivity

        command_json = json.dumps(command)
        self._client.publish(self.topic_publish, command_json, qos=1)
        logger.info(
            "[%s] Set xcam option: %s=%s, sensitivity=%s", self.serial_number, module_name, enabled, sensitivity
        )
        logger.debug("[%s] MQTT command sent: %s", self.serial_number, command_json)

        # OrcaSlicer pattern: Set hold timer to ignore incoming data for 3 seconds
        # This prevents stale MQTT data from immediately overwriting our change
        self._xcam_hold_start[module_name] = time.time()

        # Update local state immediately for responsive UI
        # NOTE: Spaghetti and Pileup sensitivities are linked in firmware
        # When spaghetti_detector sensitivity is changed, pileup also changes
        if module_name == "spaghetti_detector":
            self.state.print_options.spaghetti_detector = enabled
            self.state.print_options.print_halt = print_halt
            if sensitivity and sensitivity != "never_halt":
                # spaghetti_detector controls BOTH spaghetti and pileup sensitivities
                self.state.print_options.halt_print_sensitivity = sensitivity
                self.state.print_options.pileup_sensitivity = sensitivity
                self._xcam_hold_start["halt_print_sensitivity"] = time.time()
                self._xcam_hold_start["pileup_sensitivity"] = time.time()
        elif module_name == "first_layer_inspector":
            self.state.print_options.first_layer_inspector = enabled
        elif module_name == "printing_monitor":
            self.state.print_options.printing_monitor = enabled
        elif module_name == "buildplate_marker_detector":
            self.state.print_options.buildplate_marker_detector = enabled
        elif module_name == "allow_skip_parts":
            self.state.print_options.allow_skip_parts = enabled
        elif module_name == "pileup_detector":
            self.state.print_options.pileup_detector = enabled
            # Pileup sensitivity is linked to spaghetti - both are set via spaghetti_detector
        elif module_name == "clump_detector":
            self.state.print_options.nozzle_clumping_detector = enabled
            if sensitivity and sensitivity != "never_halt":
                self.state.print_options.nozzle_clumping_sensitivity = sensitivity
                self._xcam_hold_start["nozzle_clumping_sensitivity"] = time.time()
        elif module_name == "airprint_detector":
            self.state.print_options.airprint_detector = enabled
            if sensitivity and sensitivity != "never_halt":
                self.state.print_options.airprint_sensitivity = sensitivity
                self._xcam_hold_start["airprint_sensitivity"] = time.time()
        elif module_name == "auto_recovery_step_loss":
            self.state.print_options.auto_recovery_step_loss = enabled

        return True

    def _set_print_option(self, option_name: str, enabled: bool) -> bool:
        """Set a print option using the print.print_option command.

        This is different from xcam_control_set and is used for options like:
        - auto_recovery
        - air_print_detect
        - filament_tangle_detect
        - nozzle_blob_detect
        - sound_enable

        Args:
            option_name: The option to control (e.g., "auto_recovery")
            enabled: Whether to enable or disable the option

        Returns:
            True if command was sent, False if not connected
        """
        if not self._client or not self.state.connected:
            return False

        self._sequence_id += 1

        command = {
            "print": {
                "command": "print_option",
                "sequence_id": str(self._sequence_id),
                option_name: enabled,
            }
        }

        command_json = json.dumps(command)
        self._client.publish(self.topic_publish, command_json, qos=1)
        logger.info("[%s] Set print option: %s=%s", self.serial_number, option_name, enabled)

        # Set hold timer
        hold_key = f"print_option_{option_name}"
        self._xcam_hold_start[hold_key] = time.time()

        # Update local state immediately
        if option_name == "auto_recovery":
            self.state.print_options.auto_recovery_step_loss = enabled

        return True

    def start_calibration(
        self,
        bed_leveling: bool = False,
        vibration: bool = False,
        motor_noise: bool = False,
        nozzle_offset: bool = False,
        high_temp_heatbed: bool = False,
    ) -> bool:
        """Start printer calibration with selected options.

        Args:
            bed_leveling: Run bed leveling calibration
            vibration: Run vibration compensation calibration
            motor_noise: Run motor noise cancellation calibration
            nozzle_offset: Run nozzle offset calibration (dual nozzle printers)
            high_temp_heatbed: Run high-temperature heatbed calibration

        Returns:
            True if command was sent, False if not connected
        """
        if not self._client or not self.state.connected:
            return False

        # Build calibration bitmask based on OrcaSlicer DeviceManager.cpp
        # Bit 0: xcam_cali (not exposed in UI)
        # Bit 1: bed_leveling
        # Bit 2: vibration
        # Bit 3: motor_noise
        # Bit 4: nozzle_cali
        # Bit 5: bed_cali (high-temp heatbed)
        # Bit 6: clumppos_cali (not exposed in UI)
        option = 0
        if bed_leveling:
            option |= 1 << 1
        if vibration:
            option |= 1 << 2
        if motor_noise:
            option |= 1 << 3
        if nozzle_offset:
            option |= 1 << 4
        if high_temp_heatbed:
            option |= 1 << 5

        if option == 0:
            logger.warning("[%s] No calibration options selected", self.serial_number)
            return False

        self._sequence_id += 1

        command = {
            "print": {
                "command": "calibration",
                "sequence_id": str(self._sequence_id),
                "option": option,
            }
        }

        command_json = json.dumps(command)
        self._client.publish(self.topic_publish, command_json, qos=1)
        logger.info(
            f"[{self.serial_number}] Starting calibration: "
            f"bed_leveling={bed_leveling}, vibration={vibration}, "
            f"motor_noise={motor_noise}, nozzle_offset={nozzle_offset}, "
            f"high_temp_heatbed={high_temp_heatbed} (option={option})"
        )

        return True

    def disconnect(self):
        """Disconnect from the printer."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            self.state.connected = False

    def send_command(self, command: dict):
        """Send a command to the printer."""
        if self._client and self.state.connected:
            # Log outgoing message if logging is enabled
            if self._logging_enabled:
                self._message_log.append(
                    MQTTLogEntry(
                        timestamp=datetime.now().isoformat(),
                        topic=self.topic_publish,
                        direction="out",
                        payload=command,
                    )
                )
            self._client.publish(self.topic_publish, json.dumps(command), qos=1)

    def enable_logging(self, enabled: bool = True):
        """Enable or disable MQTT message logging."""
        self._logging_enabled = enabled
        # Don't clear logs when stopping - user can manually clear with clear_logs()

    def get_logs(self) -> list[MQTTLogEntry]:
        """Get all logged MQTT messages."""
        return list(self._message_log)

    def clear_logs(self):
        """Clear the message log."""
        self._message_log.clear()

    @property
    def logging_enabled(self) -> bool:
        """Check if logging is enabled."""
        return self._logging_enabled

    def _handle_kprofile_response(self, data: dict):
        """Handle K-profile response from printer."""
        response_nozzle = data.get("nozzle_diameter")
        _response_seq_id = data.get("sequence_id", "?")
        filaments = data.get("filaments", [])
        expected_nozzle = getattr(self, "_expected_kprofile_nozzle", None)
        has_pending_request = self._pending_kprofile_response is not None

        # Log all incoming responses when we have a pending request (for debugging)
        if has_pending_request:
            logger.info(
                f"[{self.serial_number}] K-profile response: nozzle={response_nozzle}, {len(filaments)} profiles, expected={expected_nozzle}"
            )

        # If we have a pending request, only accept responses with matching nozzle_diameter
        # The printer broadcasts 0.4mm profiles constantly - we need to wait for the actual response
        if has_pending_request and expected_nozzle and response_nozzle != expected_nozzle:
            # Ignore this broadcast, keep waiting for matching response
            logger.debug(
                f"[{self.serial_number}] Ignoring broadcast: got nozzle={response_nozzle}, waiting for {expected_nozzle}"
            )
            return

        # If no pending request, this is just a broadcast - update state silently and return early
        if not has_pending_request:
            # Still parse profiles to keep state updated, but don't log
            profiles = []
            for f in filaments:
                if isinstance(f, dict):
                    try:
                        cali_idx = f.get("cali_idx", 0)
                        profiles.append(
                            KProfile(
                                slot_id=cali_idx,
                                extruder_id=int(f.get("extruder_id", 0)),
                                nozzle_id=str(f.get("nozzle_id", "")),
                                nozzle_diameter=str(f.get("nozzle_diameter", "0.4")),
                                filament_id=str(f.get("filament_id", "")),
                                name=str(f.get("name", "")),
                                k_value=str(f.get("k_value", "0.000000")),
                                n_coef=str(f.get("n_coef", "0.000000")),
                                ams_id=int(f.get("ams_id", 0)),
                                tray_id=int(f.get("tray_id", -1)),
                                setting_id=f.get("setting_id"),
                            )
                        )
                    except (ValueError, TypeError):
                        pass  # Skip malformed K-profile entries; remaining profiles still usable
            self.state.kprofiles = profiles
            return

        profiles = []

        for i, f in enumerate(filaments):
            if isinstance(f, dict):
                try:
                    # cali_idx is the actual slot/calibration index from the printer
                    cali_idx = f.get("cali_idx", i)
                    profiles.append(
                        KProfile(
                            slot_id=cali_idx,
                            extruder_id=int(f.get("extruder_id", 0)),
                            nozzle_id=str(f.get("nozzle_id", "")),
                            nozzle_diameter=str(f.get("nozzle_diameter", "0.4")),
                            filament_id=str(f.get("filament_id", "")),
                            name=str(f.get("name", "")),
                            k_value=str(f.get("k_value", "0.000000")),
                            n_coef=str(f.get("n_coef", "0.000000")),
                            ams_id=int(f.get("ams_id", 0)),
                            tray_id=int(f.get("tray_id", -1)),
                            setting_id=f.get("setting_id"),
                        )
                    )
                except (ValueError, TypeError) as e:
                    logger.warning("Failed to parse K-profile: %s", e)

        self.state.kprofiles = profiles
        self._kprofile_response_data = profiles

        # Signal that we received the response (only if we were waiting for one)
        # Use thread-safe method since MQTT callbacks run in a different thread
        if self._pending_kprofile_response:
            logger.info("[%s] Got %s K-profiles for nozzle=%s", self.serial_number, len(profiles), response_nozzle)
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._pending_kprofile_response.set)
            else:
                # Fallback for when loop is not available
                self._pending_kprofile_response.set()

    async def get_kprofiles(
        self, nozzle_diameter: str = "0.4", timeout: float = 5.0, max_retries: int = 3
    ) -> list[KProfile]:
        """Request K-profiles from the printer with retry logic.

        Bambu printers sometimes ignore the first K-profile request, so we
        implement retry logic to ensure reliable retrieval.

        Args:
            nozzle_diameter: Filter by nozzle diameter (e.g., "0.4")
            timeout: Timeout in seconds to wait for each response attempt
            max_retries: Maximum number of retry attempts

        Returns:
            List of KProfile objects
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot get K-profiles: not connected", self.serial_number)
            return []

        # Capture current event loop for thread-safe callback
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[%s] No running event loop", self.serial_number)
            return []

        for attempt in range(max_retries):
            # Set up response event for this attempt
            self._sequence_id += 1
            self._pending_kprofile_response = asyncio.Event()
            self._kprofile_response_data = None
            self._expected_kprofile_nozzle = nozzle_diameter  # Track which nozzle response we expect

            # Send the command with nozzle_diameter filter
            command = {
                "print": {
                    "command": "extrusion_cali_get",
                    "filament_id": "",
                    "nozzle_diameter": nozzle_diameter,
                    "sequence_id": str(self._sequence_id),
                }
            }

            logger.info(
                f"[{self.serial_number}] Requesting K-profiles for nozzle_diameter={nozzle_diameter} (attempt {attempt + 1}/{max_retries})"
            )
            logger.debug("[%s] K-profile request JSON: %s", self.serial_number, json.dumps(command))
            self._client.publish(self.topic_publish, json.dumps(command), qos=1)

            # Wait for response (response handler already filters by nozzle_diameter)
            try:
                await asyncio.wait_for(self._pending_kprofile_response.wait(), timeout=timeout)
                profiles = self._kprofile_response_data or []
                logger.info(
                    f"[{self.serial_number}] Got {len(profiles)} K-profiles for nozzle={nozzle_diameter} on attempt {attempt + 1}"
                )
                return profiles
            except TimeoutError:
                logger.warning(
                    f"[{self.serial_number}] Timeout on K-profiles request attempt {attempt + 1}/{max_retries}"
                )
                if attempt < max_retries - 1:
                    # Brief delay before retry
                    await asyncio.sleep(0.5)
            finally:
                self._pending_kprofile_response = None
                self._expected_kprofile_nozzle = None

        logger.error("[%s] Failed to get K-profiles after %s attempts", self.serial_number, max_retries)
        return []

    def set_kprofile(
        self,
        filament_id: str,
        name: str,
        k_value: str,
        nozzle_diameter: str = "0.4",
        nozzle_id: str = "HS00-0.4",
        extruder_id: int = 0,
        setting_id: str | None = None,
        slot_id: int = 0,
        cali_idx: int | None = None,
    ) -> bool:
        """Set/update a K-profile on the printer.

        Args:
            filament_id: Bambu filament identifier
            name: Profile name
            k_value: Pressure advance value (e.g., "0.020000")
            nozzle_diameter: Nozzle diameter (e.g., "0.4")
            nozzle_id: Nozzle identifier (e.g., "HS00-0.4")
            extruder_id: Extruder ID (0 or 1 for dual nozzle)
            setting_id: Existing setting ID for updates, None for new
            slot_id: Calibration index (cali_idx) for the profile
            cali_idx: For edits, the existing slot being edited (enables in-place edit)

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set K-profile: not connected", self.serial_number)
            return False

        self._sequence_id += 1

        # Build the filament entry - printer uses cali_idx for profile identification
        # For new profiles (slot_id=0), use cali_idx=-1 to tell printer to create new slot
        # For edits, use the provided cali_idx or slot_id
        if cali_idx is not None:
            effective_cali_idx = cali_idx
        else:
            effective_cali_idx = -1 if slot_id == 0 else slot_id

        # Generate a setting_id for new profiles (required by printer)
        # Format: "PF" + 17 random digits
        import random

        if not setting_id and slot_id == 0:
            setting_id = f"PF{random.randint(10000000000000000, 99999999999999999)}"

        filament_entry = {
            "ams_id": 0,
            "cali_idx": effective_cali_idx,
            "extruder_id": extruder_id,
            "filament_id": filament_id,
            "k_value": k_value,
            "n_coef": "0.000000",
            "name": name,
            "nozzle_diameter": nozzle_diameter,
            "nozzle_id": nozzle_id,
            "setting_id": setting_id if setting_id else "",
            "tray_id": -1,
        }

        command = {
            "print": {
                "command": "extrusion_cali_set",
                "filaments": [filament_entry],
                "nozzle_diameter": nozzle_diameter,
                "sequence_id": str(self._sequence_id),
            }
        }

        command_json = json.dumps(command)
        logger.info(
            f"[{self.serial_number}] Setting K-profile: {name} = {k_value} (cali_idx={effective_cali_idx}, new={slot_id == 0})"
        )
        logger.info("[%s] K-profile SET command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        return True

    def set_kprofiles_batch(
        self,
        profiles: list[dict],
        nozzle_diameter: str = "0.4",
    ) -> bool:
        """Set multiple K-profiles in a single command (for dual-nozzle).

        Args:
            profiles: List of profile dicts, each with:
                - filament_id, name, k_value, nozzle_id, extruder_id, setting_id (optional), slot_id
            nozzle_diameter: Common nozzle diameter for all profiles

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set K-profiles batch: not connected", self.serial_number)
            return False

        import random

        self._sequence_id += 1

        filament_entries = []
        for p in profiles:
            slot_id = p.get("slot_id", 0)
            cali_idx = p.get("cali_idx")

            if cali_idx is not None:
                effective_cali_idx = cali_idx
            else:
                effective_cali_idx = -1 if slot_id == 0 else slot_id

            setting_id = p.get("setting_id")
            if not setting_id and slot_id == 0:
                setting_id = f"PF{random.randint(10000000000000000, 99999999999999999)}"

            filament_entries.append(
                {
                    "ams_id": 0,
                    "cali_idx": effective_cali_idx,
                    "extruder_id": p.get("extruder_id", 0),
                    "filament_id": p.get("filament_id", ""),
                    "k_value": p.get("k_value", "0.020000"),
                    "n_coef": "0.000000",
                    "name": p.get("name", ""),
                    "nozzle_diameter": nozzle_diameter,
                    "nozzle_id": p.get("nozzle_id", f"HS00-{nozzle_diameter}"),
                    "setting_id": setting_id if setting_id else "",
                    "tray_id": -1,
                }
            )

        command = {
            "print": {
                "command": "extrusion_cali_set",
                "filaments": filament_entries,
                "nozzle_diameter": nozzle_diameter,
                "sequence_id": str(self._sequence_id),
            }
        }

        command_json = json.dumps(command)
        logger.info("[%s] Setting %s K-profiles in batch", self.serial_number, len(filament_entries))
        logger.info("[%s] K-profile SET batch command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        return True

    def delete_kprofile(
        self,
        cali_idx: int,
        filament_id: str,
        nozzle_id: str,
        nozzle_diameter: str = "0.4",
        extruder_id: int = 0,
        setting_id: str | None = None,
    ) -> bool:
        """Delete a K-profile from the printer.

        Args:
            cali_idx: The calibration index (slot_id) of the profile to delete
            filament_id: Bambu filament identifier
            nozzle_id: Nozzle identifier (e.g., "HH00-0.4")
            nozzle_diameter: Nozzle diameter (e.g., "0.4")
            extruder_id: Extruder ID (0 or 1 for dual nozzle)
            setting_id: Unique setting identifier (for X1C series)

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot delete K-profile: not connected", self.serial_number)
            return False

        self._sequence_id += 1

        # Detect printer type by serial number prefix
        # H2D series (dual nozzle): serial starts with "094"
        is_dual_nozzle = self.serial_number.startswith("094")

        if is_dual_nozzle:
            # H2D format: uses extruder_id, nozzle_id, nozzle_diameter
            command = {
                "print": {
                    "command": "extrusion_cali_del",
                    "sequence_id": str(self._sequence_id),
                    "extruder_id": extruder_id,
                    "nozzle_id": nozzle_id,
                    "filament_id": filament_id,
                    "cali_idx": cali_idx,
                    "nozzle_diameter": nozzle_diameter,
                }
            }
        else:
            # X1C/P1/A1 format: include all fields like the set command
            # The delete command structure should match what set uses
            command = {
                "print": {
                    "command": "extrusion_cali_del",
                    "sequence_id": str(self._sequence_id),
                    "filament_id": filament_id,
                    "cali_idx": cali_idx,
                    "setting_id": setting_id if setting_id else "",
                    "nozzle_diameter": nozzle_diameter,
                    "nozzle_id": nozzle_id,
                    "extruder_id": extruder_id,
                }
            }

        command_json = json.dumps(command)
        logger.info(
            f"[{self.serial_number}] Deleting K-profile: cali_idx={cali_idx}, filament={filament_id}, setting_id={setting_id}, dual={is_dual_nozzle}"
        )
        logger.info("[%s] K-profile DELETE command: %s", self.serial_number, command_json)
        # Use QoS 1 for reliable delivery (at least once)
        self._client.publish(self.topic_publish, command_json, qos=1)
        return True

    # =========================================================================
    # Printer Control Commands
    # =========================================================================

    def pause_print(self) -> bool:
        """Pause the current print job."""
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot pause print: not connected", self.serial_number)
            return False

        command = {"print": {"command": "pause", "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Sent pause print command", self.serial_number)
        return True

    def resume_print(self) -> bool:
        """Resume a paused print job."""
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot resume print: not connected", self.serial_number)
            return False

        command = {"print": {"command": "resume", "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Sent resume print command", self.serial_number)
        return True

    def skip_objects(self, object_ids: list[int]) -> bool:
        """Skip specific objects during a print.

        This command tells the printer to skip printing the specified objects.
        The object IDs come from the slice_info.config file in the 3MF.

        Args:
            object_ids: List of identify_id values from slice_info.config

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot skip objects: not connected", self.serial_number)
            return False

        if self.state.state != "RUNNING" and self.state.state != "PAUSE":
            logger.warning(
                f"[{self.serial_number}] Cannot skip objects: printer not printing (state={self.state.state})"
            )
            return False

        if not object_ids:
            logger.warning("[%s] Cannot skip objects: no object IDs provided", self.serial_number)
            return False

        # Validate all IDs are integers
        try:
            obj_list = [int(oid) for oid in object_ids]
        except (ValueError, TypeError) as e:
            logger.warning("[%s] Invalid object IDs: %s", self.serial_number, e)
            return False

        self._sequence_id += 1
        command = {"print": {"sequence_id": str(self._sequence_id), "command": "skip_objects", "obj_list": obj_list}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Sent skip_objects command: %s", self.serial_number, obj_list)

        # Track skipped objects in state
        for oid in obj_list:
            if oid not in self.state.skipped_objects:
                self.state.skipped_objects.append(oid)

        return True

    def send_gcode(self, gcode: str) -> bool:
        """Send G-code command(s) to the printer.

        Multiple commands can be separated by newlines.

        Args:
            gcode: G-code command(s) to send

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot send G-code: not connected", self.serial_number)
            return False

        self._sequence_id += 1
        command = {"print": {"command": "gcode_line", "param": gcode, "sequence_id": str(self._sequence_id)}}
        # Use QoS 1 for reliable delivery (at least once)
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.debug("[%s] Sent G-code: %s...", self.serial_number, gcode[:50])
        return True

    def set_bed_temperature(self, target: int) -> bool:
        """Set the bed target temperature.

        Args:
            target: Target temperature in Celsius (0 to turn off)

        Returns:
            True if command was sent, False otherwise
        """
        return self.send_gcode(f"M140 S{target}")

    def set_nozzle_temperature(self, target: int, nozzle: int = 0) -> bool:
        """Set the nozzle target temperature.

        Args:
            target: Target temperature in Celsius (0 to turn off)
            nozzle: Nozzle index (0 for right/default, 1 for left on H2D)

        Returns:
            True if command was sent, False otherwise
        """
        # Use M104 for non-blocking
        # Always use T parameter for H2D compatibility
        result = self.send_gcode(f"M104 T{nozzle} S{target}")
        # H2D quirk: left nozzle (nozzle=1) target isn't reported in MQTT
        # Track it locally so we can display it correctly
        if result and nozzle == 1:
            self.state.temperatures["nozzle_target"] = float(target)
            self.state.temperatures["_nozzle_target_set_time"] = time.time()
            logger.info("[%s] Tracking LEFT nozzle target locally: %s°C", self.serial_number, target)
        return result

    def set_chamber_temperature(self, target: int) -> bool:
        """Set the chamber target temperature.

        Args:
            target: Target temperature in Celsius (0 to turn off heating)

        Returns:
            True if command was sent, False otherwise
        """
        # M141 sets chamber temperature
        result = self.send_gcode(f"M141 S{target}")
        # Track chamber target locally (MQTT reports encoded values that need filtering)
        if result:
            self.state.temperatures["chamber_target"] = float(target)
            self.state.temperatures["_chamber_target_set_time"] = time.time()
            # Update heating state immediately based on new target
            current_temp = self.state.temperatures.get("chamber", 0)
            self.state.temperatures["chamber_heating"] = target > 0 and current_temp < target
            logger.info(
                f"[{self.serial_number}] Tracking chamber target locally: {target}°C (heating={self.state.temperatures['chamber_heating']})"
            )
        return result

    def set_print_speed(self, mode: int) -> bool:
        """Set the print speed mode.

        Args:
            mode: Speed mode (1=silent, 2=standard, 3=sport, 4=ludicrous)

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set print speed: not connected", self.serial_number)
            return False

        if mode not in (1, 2, 3, 4):
            logger.warning("[%s] Invalid speed mode: %s", self.serial_number, mode)
            return False

        command = {"print": {"command": "print_speed", "param": str(mode), "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Set print speed mode to %s", self.serial_number, mode)
        return True

    def set_fan_speed(self, fan: int, speed: int) -> bool:
        """Set fan speed.

        Args:
            fan: Fan index (1=part cooling, 2=auxiliary, 3=chamber)
            speed: Speed 0-255 (0=off, 255=full)

        Returns:
            True if command was sent, False otherwise
        """
        if fan not in (1, 2, 3):
            logger.warning("[%s] Invalid fan index: %s", self.serial_number, fan)
            return False

        speed = max(0, min(255, speed))  # Clamp to 0-255
        return self.send_gcode(f"M106 P{fan} S{speed}")

    def set_part_fan(self, speed: int) -> bool:
        """Set part cooling fan speed (0-255)."""
        return self.set_fan_speed(1, speed)

    def set_aux_fan(self, speed: int) -> bool:
        """Set auxiliary fan speed (0-255)."""
        return self.set_fan_speed(2, speed)

    def set_chamber_fan(self, speed: int) -> bool:
        """Set chamber fan speed (0-255)."""
        return self.set_fan_speed(3, speed)

    def set_airduct_mode(self, mode: str) -> bool:
        """Set air conditioning mode (cooling or heating).

        Args:
            mode: "cooling" (modeId=0) or "heating" (modeId=1)
                - Cooling: Suitable for PLA/PETG/TPU, filters and cools chamber air
                - Heating: Suitable for ABS/ASA/PC/PA, circulates and heats chamber air,
                           closes top exhaust flap

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set airduct mode: not connected", self.serial_number)
            return False

        self._sequence_id += 1
        mode_id = 0 if mode == "cooling" else 1
        command = {
            "print": {"command": "set_airduct", "modeId": mode_id, "sequence_id": str(self._sequence_id), "submode": -1}
        }
        # Use QoS 1 for reliable delivery
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info(
            "[%s] Set airduct mode to %s (modeId=%s, seq=%s)", self.serial_number, mode, mode_id, self._sequence_id
        )
        return True

    def set_chamber_light(self, on: bool) -> bool:
        """Turn chamber light on or off.

        Args:
            on: True to turn on, False to turn off

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set chamber light: not connected", self.serial_number)
            return False

        mode = "on" if on else "off"
        # Control both chamber lights (some printers like H2D have two)
        for led_node in ["chamber_light", "chamber_light2"]:
            self._sequence_id += 1
            command = {
                "system": {
                    "command": "ledctrl",
                    "led_node": led_node,
                    "led_mode": mode,
                    "led_on_time": 500,
                    "led_off_time": 500,
                    "loop_times": 0,
                    "interval_time": 0,
                    "sequence_id": str(self._sequence_id),
                }
            }
            self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Set chamber lights %s (seq=%s)", self.serial_number, "on" if on else "off", self._sequence_id)
        return True

    def select_extruder(self, extruder: int) -> bool:
        """Select the active extruder for dual-nozzle printers (H2D).

        Args:
            extruder: Extruder index (0=right, 1=left for H2D)

        Returns:
            True if command was sent, False otherwise
        """
        if extruder not in (0, 1):
            logger.warning("[%s] Invalid extruder: %s", self.serial_number, extruder)
            return False

        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot switch extruder: not connected", self.serial_number)
            return False

        # H2D extruder switching via select_extruder command
        # Command format captured from OrcaSlicer:
        # {"print": {"command": "select_extruder", "extruder_index": 0, "sequence_id": "..."}}
        # extruder_index: 0 = RIGHT, 1 = LEFT
        self._sequence_id += 1
        command = {
            "print": {"command": "select_extruder", "extruder_index": extruder, "sequence_id": str(self._sequence_id)}
        }
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info(
            "[%s] Sent select_extruder command: extruder_index=%s (0=right, 1=left)", self.serial_number, extruder
        )
        return True

    def home_axes(self, axes: str = "XYZ") -> bool:
        """Home the specified axes.

        Args:
            axes: Axes to home (e.g., "XYZ", "X", "XY", "Z")

        Returns:
            True if command was sent, False otherwise
        """
        # G28 homes all axes, G28 X Y Z homes specific axes
        axes_param = " ".join(axes.upper())
        return self.send_gcode(f"G28 {axes_param}")

    def move_axis(self, axis: str, distance: float, speed: int = 3000) -> bool:
        """Move an axis by a relative distance.

        Args:
            axis: Axis to move ("X", "Y", or "Z")
            distance: Distance to move in mm (positive or negative)
            speed: Movement speed in mm/min

        Returns:
            True if command was sent, False otherwise
        """
        axis = axis.upper()
        if axis not in ("X", "Y", "Z"):
            logger.warning("[%s] Invalid axis: %s", self.serial_number, axis)
            return False

        # G91 = relative mode, G0 = rapid move, G90 = back to absolute
        gcode = f"G91\nG0 {axis}{distance:.2f} F{speed}\nG90"
        return self.send_gcode(gcode)

    def disable_motors(self) -> bool:
        """Disable all stepper motors.

        Warning: This will cause the printer to lose its position.
        A homing operation will be required before printing.

        Returns:
            True if command was sent, False otherwise
        """
        return self.send_gcode("M18")

    def enable_motors(self) -> bool:
        """Enable all stepper motors.

        Returns:
            True if command was sent, False otherwise
        """
        return self.send_gcode("M17")

    def ams_load_filament(self, tray_id: int, extruder_id: int | None = None) -> bool:
        """Load filament from a specific AMS tray.

        Args:
            tray_id: Global tray ID (0-15 for AMS slots, or 254 for external spool)
            extruder_id: Unused - kept for API compatibility

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot load filament: not connected", self.serial_number)
            return False

        # Calculate ams_id and slot_id for logging
        if tray_id == 254:
            ams_id = 255  # External spool
            slot_id = 254
        else:
            ams_id = tray_id // 4  # AMS unit (0, 1, 2, 3...)
            slot_id = tray_id % 4  # Slot within AMS (0, 1, 2, 3)

        # Command format from BambuStudio traffic capture:
        # - No extruder_id field
        # - curr_temp and tar_temp are -1 (not 0)
        self._sequence_id += 1
        command = {
            "print": {
                "command": "ams_change_filament",
                "sequence_id": str(self._sequence_id),
                "ams_id": ams_id,
                "slot_id": slot_id,
                "target": tray_id,
                "curr_temp": -1,
                "tar_temp": -1,
            }
        }

        command_json = json.dumps(command)
        logger.info("[%s] Publishing ams_change_filament command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        logger.info("[%s] Loading filament from tray %s (AMS %s slot %s)", self.serial_number, tray_id, ams_id, slot_id)

        # Track this load request for H2D dual-nozzle disambiguation
        # H2D reports only slot number (0-3) in tray_now, so we use our tracked value
        self._last_load_tray_id = tray_id
        self.state.pending_tray_target = tray_id
        logger.info("[%s] Set pending_tray_target=%s for H2D disambiguation", self.serial_number, tray_id)

        return True

    def ams_unload_filament(self) -> bool:
        """Unload the currently loaded filament.

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot unload filament: not connected", self.serial_number)
            return False

        # Get the currently loaded tray info
        tray_now = self.state.tray_now
        logger.info("[%s] Unload requested, tray_now=%s", self.serial_number, tray_now)

        # Determine source ams_id for the unload command
        if tray_now == 255 or tray_now == 254:
            ams_id = 255  # No filament or external spool
        else:
            ams_id = tray_now // 4  # Source AMS

        # Command format from BambuStudio traffic capture:
        # - No extruder_id field
        # - For UNLOAD: curr_temp and tar_temp are the actual nozzle temp (e.g., 210)
        # - slot_id=255 and target=255 for unload
        # Get current nozzle temperature for the unload command
        nozzle_temp = int(self.state.temperatures.get("nozzle", 210))
        if nozzle_temp < 180:
            nozzle_temp = 210  # Default to PLA temp if nozzle is cold

        self._sequence_id += 1
        command = {
            "print": {
                "command": "ams_change_filament",
                "sequence_id": str(self._sequence_id),
                "ams_id": ams_id,
                "slot_id": 255,  # 255 = unload marker
                "target": 255,  # 255 = unload destination
                "curr_temp": nozzle_temp,
                "tar_temp": nozzle_temp,
            }
        }

        command_json = json.dumps(command)
        logger.info("[%s] Publishing ams_change_filament (unload) command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        logger.info("[%s] Unloading filament (tray_now was %s)", self.serial_number, tray_now)

        # Clear tracked load request since we're unloading
        self._last_load_tray_id = None
        self.state.pending_tray_target = None
        logger.info("[%s] Cleared pending_tray_target (unload)", self.serial_number)

        return True

    def ams_control(self, action: str) -> bool:
        """Control AMS operations.

        Args:
            action: "resume", "reset", or "pause"

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot control AMS: not connected", self.serial_number)
            return False

        if action not in ("resume", "reset", "pause"):
            logger.warning("[%s] Invalid AMS action: %s", self.serial_number, action)
            return False

        command = {"print": {"command": "ams_control", "param": action, "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] AMS control: %s", self.serial_number, action)
        return True

    def ams_refresh_tray(self, ams_id: int, tray_id: int) -> tuple[bool, str]:
        """Trigger RFID re-read for a specific AMS tray.

        Args:
            ams_id: AMS unit ID (0-3, or 128 for H2D external tray)
            tray_id: Tray ID within the AMS (0-3)

        Returns:
            Tuple of (success, message)
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot refresh AMS tray: not connected", self.serial_number)
            return False, "Printer not connected"

        # Check if filament is currently loaded (tray_now != 255)
        # RFID refresh requires the AMS to move filament, which can't happen if one is loaded
        tray_now = self.state.tray_now
        if tray_now != 255:
            # Decode which tray is loaded for the message
            if tray_now == 254:
                loaded_tray = "external spool"
            elif tray_now >= 0 and tray_now < 128:
                loaded_ams = tray_now // 4
                loaded_slot = tray_now % 4
                loaded_tray = f"AMS {loaded_ams + 1} slot {loaded_slot + 1}"
            else:
                loaded_tray = f"tray {tray_now}"
            logger.warning("[%s] Cannot refresh AMS tray: filament loaded from %s", self.serial_number, loaded_tray)
            return False, f"Please unload filament first. Currently loaded: {loaded_tray}"

        # Use ams_get_rfid command to trigger RFID re-read
        # This command is used by Bambu Studio to re-read the RFID tag
        command = {"print": {"command": "ams_get_rfid", "ams_id": ams_id, "slot_id": tray_id, "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Triggering RFID re-read: AMS %s, slot %s", self.serial_number, ams_id, tray_id)
        return True, f"Refreshing AMS {ams_id} tray {tray_id}"

    def ams_set_filament_setting(
        self,
        ams_id: int,
        tray_id: int,
        tray_info_idx: str,
        tray_type: str,
        tray_sub_brands: str,
        tray_color: str,
        nozzle_temp_min: int,
        nozzle_temp_max: int,
        setting_id: str = "",
    ) -> bool:
        """Set AMS tray filament settings (type, color, temperature).

        Note: K value is set separately via extrusion_cali_sel command.

        Args:
            ams_id: AMS unit ID (0-3 for regular AMS, 128-135 for HT AMS)
            tray_id: Tray ID within the AMS (0-3)
            tray_info_idx: Filament ID short format (e.g., "GFL05")
            tray_type: Filament type (e.g., "PLA", "PETG")
            tray_sub_brands: Sub-brand name (e.g., "PLA Basic", "PETG HF")
            tray_color: Color in RRGGBBAA hex format (e.g., "FFFF00FF")
            nozzle_temp_min: Minimum nozzle temperature
            nozzle_temp_max: Maximum nozzle temperature
            setting_id: Full setting ID with version (e.g., "GFSL05_07") - optional

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set AMS filament setting: not connected", self.serial_number)
            return False

        # Calculate slot_id based on AMS type
        if ams_id <= 3:
            slot_id = tray_id
        else:
            # AMS-HT or external: slot_id = 0
            slot_id = 0

        command = {
            "print": {
                "command": "ams_filament_setting",
                "ams_id": ams_id,
                "tray_id": tray_id,
                "slot_id": slot_id,
                "tray_info_idx": tray_info_idx,
                "tray_type": tray_type,
                "tray_sub_brands": tray_sub_brands,
                "tray_color": tray_color,
                "nozzle_temp_min": nozzle_temp_min,
                "nozzle_temp_max": nozzle_temp_max,
                "sequence_id": "0",
            }
        }

        # Include setting_id if provided (helps slicer show correct profile)
        if setting_id:
            command["print"]["setting_id"] = setting_id

        command_json = json.dumps(command)
        logger.info(
            f"[{self.serial_number}] Publishing ams_filament_setting: AMS {ams_id}, tray {tray_id}, tray_info_idx={tray_info_idx}, setting_id={setting_id}"
        )
        logger.debug("[%s] ams_filament_setting command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        return True

    def reset_ams_slot(self, ams_id: int, tray_id: int) -> bool:
        """Reset an AMS slot to empty/unconfigured state.

        Args:
            ams_id: AMS unit ID (0-3 for regular AMS, 128-135 for HT AMS)
            tray_id: Tray ID within the AMS (0-3)

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot reset AMS slot: not connected", self.serial_number)
            return False

        # Calculate slot_id based on AMS type
        if ams_id <= 3:
            slot_id = tray_id
        else:
            slot_id = 0

        command = {
            "print": {
                "command": "ams_filament_setting",
                "ams_id": ams_id,
                "tray_id": tray_id,
                "slot_id": slot_id,
                "tray_info_idx": "",
                "tray_type": "",
                "tray_sub_brands": "",
                "tray_color": "00000000",
                "nozzle_temp_min": 0,
                "nozzle_temp_max": 0,
                "sequence_id": "0",
            }
        }

        command_json = json.dumps(command)
        logger.info("[%s] Resetting AMS slot: AMS %s, tray %s", self.serial_number, ams_id, tray_id)
        logger.debug("[%s] reset_ams_slot command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        return True

    def extrusion_cali_sel(
        self,
        ams_id: int,
        tray_id: int,
        cali_idx: int,
        filament_id: str,
        nozzle_diameter: str = "0.4",
        setting_id: str | None = None,
    ) -> bool:
        """Set calibration profile (K value) for an AMS slot.

        This command selects a K profile from the printer's calibration list.
        Use cali_idx=-1 to use the default K value (0.020).

        Args:
            ams_id: AMS unit ID (0-3 for regular AMS, 128-135 for HT AMS)
            tray_id: Tray ID within the AMS (0-3)
            cali_idx: Calibration profile index (-1 for default)
            filament_id: Filament preset ID (same as tray_info_idx)
            nozzle_diameter: Nozzle diameter string (e.g., "0.4")
            setting_id: Full setting ID with version (e.g., "GFSL05_07") - optional

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set calibration: not connected", self.serial_number)
            return False

        # Calculate slot_id based on AMS type
        # tray_id in the command should be the local tray index (0-3)
        if ams_id <= 3:
            slot_id = tray_id
        elif ams_id >= 128 and ams_id <= 135:
            slot_id = 0
        else:
            slot_id = 0

        command = {
            "print": {
                "command": "extrusion_cali_sel",
                "cali_idx": cali_idx,
                "filament_id": filament_id,
                "nozzle_diameter": nozzle_diameter,
                "ams_id": ams_id,
                "tray_id": tray_id,  # Local tray index (0-3), not global
                "slot_id": slot_id,
                "sequence_id": "0",
            }
        }

        # Include setting_id if provided (helps slicer show correct K profile)
        if setting_id:
            command["print"]["setting_id"] = setting_id

        command_json = json.dumps(command)
        logger.info(
            f"[{self.serial_number}] Publishing extrusion_cali_sel: AMS {ams_id}, tray {tray_id}, cali_idx={cali_idx}, setting_id={setting_id}"
        )
        logger.debug("[%s] extrusion_cali_sel command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        return True

    def extrusion_cali_set(
        self,
        tray_id: int,
        k_value: float,
        n_coef: float = 0.0,
        nozzle_diameter: str = "0.4",
        bed_temp: int = 60,
        nozzle_temp: int = 220,
        max_volumetric_speed: float = 20.0,
    ) -> bool:
        """Directly set K value (pressure advance) for a tray.

        This command sets the K value directly without selecting from stored profiles.
        Use this when you want to apply a specific K value to a tray.

        Args:
            tray_id: Global tray ID (ams_id * 4 + slot)
            k_value: Pressure advance K value (e.g., 0.020)
            n_coef: N coefficient (usually 0.0 for manual, 1.4 for auto-calibration)
            nozzle_diameter: Nozzle diameter string (e.g., "0.4")
            bed_temp: Bed temperature for calibration reference
            nozzle_temp: Nozzle temperature for calibration reference
            max_volumetric_speed: Max volumetric speed for calibration reference

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set K value: not connected", self.serial_number)
            return False

        command = {
            "print": {
                "command": "extrusion_cali_set",
                "tray_id": tray_id,
                "k_value": k_value,
                "n_coef": n_coef,
                "nozzle_diameter": nozzle_diameter,
                "bed_temp": bed_temp,
                "nozzle_temp": nozzle_temp,
                "max_volumetric_speed": max_volumetric_speed,
                "sequence_id": "0",
            }
        }

        command_json = json.dumps(command)
        logger.info("[%s] Publishing extrusion_cali_set: tray %s, k_value=%s", self.serial_number, tray_id, k_value)
        logger.debug("[%s] extrusion_cali_set command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        return True

    def set_timelapse(self, enable: bool) -> bool:
        """Enable or disable timelapse recording.

        Args:
            enable: True to enable, False to disable

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set timelapse: not connected", self.serial_number)
            return False

        command = {"pushing": {"command": "pushall", "sequence_id": "0"}}
        # First send the timelapse setting
        timelapse_cmd = {
            "print": {"command": "gcode_line", "param": f"M981 S{1 if enable else 0} P20000", "sequence_id": "0"}
        }
        self._client.publish(self.topic_publish, json.dumps(timelapse_cmd), qos=1)
        # Request status update
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Set timelapse %s", self.serial_number, "enabled" if enable else "disabled")
        return True

    def set_liveview(self, enable: bool) -> bool:
        """Enable or disable live view / camera streaming.

        Args:
            enable: True to enable, False to disable

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set liveview: not connected", self.serial_number)
            return False

        command = {
            "xcam": {"command": "ipcam_record_set", "control": "enable" if enable else "disable", "sequence_id": "0"}
        }
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        # Request status update
        pushall = {"pushing": {"command": "pushall", "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(pushall), qos=1)
        logger.info("[%s] Set liveview %s", self.serial_number, "enabled" if enable else "disabled")
        return True
