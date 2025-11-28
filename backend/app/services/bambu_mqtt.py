import json
import ssl
import asyncio
from typing import Callable
from dataclasses import dataclass, field

import paho.mqtt.client as mqtt


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


class BambuMQTTClient:
    """MQTT client for Bambu Lab printer communication."""

    MQTT_PORT = 8883

    def __init__(
        self,
        ip_address: str,
        serial_number: str,
        access_code: str,
        on_state_change: Callable[[PrinterState], None] | None = None,
        on_print_start: Callable[[dict], None] | None = None,
        on_print_complete: Callable[[dict], None] | None = None,
    ):
        self.ip_address = ip_address
        self.serial_number = serial_number
        self.access_code = access_code
        self.on_state_change = on_state_change
        self.on_print_start = on_print_start
        self.on_print_complete = on_print_complete

        self.state = PrinterState()
        self._client: mqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._previous_gcode_state: str | None = None
        self._previous_gcode_file: str | None = None

    @property
    def topic_subscribe(self) -> str:
        return f"device/{self.serial_number}/report"

    @property
    def topic_publish(self) -> str:
        return f"device/{self.serial_number}/request"

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.state.connected = True
            client.subscribe(self.topic_subscribe)
            # Request full status update
            self._request_push_all()
        else:
            self.state.connected = False

    def _on_disconnect(self, client, userdata, disconnect_flags=None, rc=None, properties=None):
        self.state.connected = False
        if self.on_state_change:
            self.on_state_change(self.state)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            self._process_message(payload)
        except json.JSONDecodeError:
            pass

    def _process_message(self, payload: dict):
        """Process incoming MQTT message from printer."""
        if "print" in payload:
            print_data = payload["print"]
            self._update_state(print_data)

    def _update_state(self, data: dict):
        """Update printer state from message data."""
        previous_state = self.state.state

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
        if "layer_num" in data:
            self.state.layer_num = int(data["layer_num"])
        if "total_layer_num" in data:
            self.state.total_layers = int(data["total_layer_num"])

        # Temperature data
        temps = {}
        if "bed_temper" in data:
            temps["bed"] = float(data["bed_temper"])
        if "bed_target_temper" in data:
            temps["bed_target"] = float(data["bed_target_temper"])
        if "nozzle_temper" in data:
            temps["nozzle"] = float(data["nozzle_temper"])
        if "nozzle_target_temper" in data:
            temps["nozzle_target"] = float(data["nozzle_target_temper"])
        if "chamber_temper" in data:
            temps["chamber"] = float(data["chamber_temper"])
        if temps:
            self.state.temperatures = temps

        self.state.raw_data = data

        # Detect print start (state changes TO RUNNING with a file)
        current_file = self.state.gcode_file or self.state.current_print
        is_new_print = (
            self.state.state == "RUNNING"
            and self._previous_gcode_state != "RUNNING"
            and current_file
        )
        # Also detect if file changed while running (new print started)
        is_file_change = (
            self.state.state == "RUNNING"
            and current_file
            and current_file != self._previous_gcode_file
            and self._previous_gcode_file is not None
        )

        if (is_new_print or is_file_change) and self.on_print_start:
            self.on_print_start({
                "filename": current_file,
                "subtask_name": self.state.subtask_name,
                "raw_data": data,
            })

        # Detect print completion
        if (
            self._previous_gcode_state == "RUNNING"
            and self.state.state in ("FINISH", "FAILED")
            and self.on_print_complete
        ):
            self.on_print_complete({
                "status": "completed" if self.state.state == "FINISH" else "failed",
                "filename": self._previous_gcode_file or current_file,
                "raw_data": data,
            })

        self._previous_gcode_state = self.state.state
        if current_file:
            self._previous_gcode_file = current_file

        if self.on_state_change:
            self.on_state_change(self.state)

    def _request_push_all(self):
        """Request full status update from printer."""
        if self._client:
            message = {"pushing": {"command": "pushall"}}
            self._client.publish(self.topic_publish, json.dumps(message))

    def connect(self):
        """Connect to the printer MQTT broker."""
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"bambutrack_{self.serial_number}",
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

        self._client.connect_async(self.ip_address, self.MQTT_PORT)
        self._client.loop_start()

    def start_print(self, filename: str, plate_id: int = 1):
        """Start a print job on the printer."""
        if self._client and self.state.connected:
            # Bambu print command format
            command = {
                "print": {
                    "command": "project_file",
                    "param": f"Metadata/plate_{plate_id}.gcode",
                    "subtask_name": filename,
                    "url": f"ftp://{filename}",
                    "bed_type": "auto",
                    "timelapse": False,
                    "bed_leveling": True,
                    "flow_cali": True,
                    "vibration_cali": True,
                    "layer_inspect": False,
                    "use_ams": True,
                }
            }
            self._client.publish(self.topic_publish, json.dumps(command))
            return True
        return False

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
            self._client.publish(self.topic_publish, json.dumps(command))
