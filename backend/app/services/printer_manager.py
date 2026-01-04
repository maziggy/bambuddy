import asyncio
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.printer import Printer
from backend.app.services.bambu_mqtt import BambuMQTTClient, MQTTLogEntry, PrinterState, get_stage_name


class PrinterManager:
    """Manager for multiple printer connections."""

    def __init__(self):
        self._clients: dict[int, BambuMQTTClient] = {}
        self._on_print_start: Callable[[int, dict], None] | None = None
        self._on_print_complete: Callable[[int, dict], None] | None = None
        self._on_status_change: Callable[[int, PrinterState], None] | None = None
        self._on_ams_change: Callable[[int, list], None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the event loop for async callbacks."""
        self._loop = loop

    def set_print_start_callback(self, callback: Callable[[int, dict], None]):
        """Set callback for print start events."""
        self._on_print_start = callback

    def set_print_complete_callback(self, callback: Callable[[int, dict], None]):
        """Set callback for print completion events."""
        self._on_print_complete = callback

    def set_status_change_callback(self, callback: Callable[[int, PrinterState], None]):
        """Set callback for status change events."""
        self._on_status_change = callback

    def set_ams_change_callback(self, callback: Callable[[int, list], None]):
        """Set callback for AMS data change events."""
        self._on_ams_change = callback

    def _schedule_async(self, coro):
        """Schedule an async coroutine from a sync context.

        Captures exceptions from the coroutine and logs them to prevent
        silent failures in callbacks.
        """
        if self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)

            def handle_exception(f):
                try:
                    # This will re-raise any exception from the coroutine
                    f.result()
                except Exception as e:
                    import logging

                    logging.getLogger(__name__).error(f"Exception in scheduled callback: {e}", exc_info=True)

            future.add_done_callback(handle_exception)

    async def connect_printer(self, printer: Printer) -> bool:
        """Connect to a printer."""
        if printer.id in self._clients:
            self.disconnect_printer(printer.id)

        printer_id = printer.id

        def on_state_change(state: PrinterState):
            if self._on_status_change:
                self._schedule_async(self._on_status_change(printer_id, state))

        def on_print_start(data: dict):
            if self._on_print_start:
                self._schedule_async(self._on_print_start(printer_id, data))

        def on_print_complete(data: dict):
            if self._on_print_complete:
                self._schedule_async(self._on_print_complete(printer_id, data))

        def on_ams_change(ams_data: list):
            if self._on_ams_change:
                self._schedule_async(self._on_ams_change(printer_id, ams_data))

        client = BambuMQTTClient(
            ip_address=printer.ip_address,
            serial_number=printer.serial_number,
            access_code=printer.access_code,
            on_state_change=on_state_change,
            on_print_start=on_print_start,
            on_print_complete=on_print_complete,
            on_ams_change=on_ams_change,
        )

        client.connect()
        self._clients[printer_id] = client

        # Wait a moment for connection
        await asyncio.sleep(1)
        return client.state.connected

    def disconnect_printer(self, printer_id: int):
        """Disconnect from a printer."""
        if printer_id in self._clients:
            self._clients[printer_id].disconnect()
            del self._clients[printer_id]

    def disconnect_all(self):
        """Disconnect from all printers."""
        for printer_id in list(self._clients.keys()):
            self.disconnect_printer(printer_id)

    def get_status(self, printer_id: int) -> PrinterState | None:
        """Get the current status of a printer (checks for stale connections)."""
        if printer_id in self._clients:
            client = self._clients[printer_id]
            # Check staleness and update connected state if needed
            client.check_staleness()
            return client.state
        return None

    def get_all_statuses(self) -> dict[int, PrinterState]:
        """Get status of all connected printers (checks for stale connections)."""
        result = {}
        for printer_id, client in self._clients.items():
            # Check staleness and update connected state if needed
            client.check_staleness()
            result[printer_id] = client.state
        return result

    def is_connected(self, printer_id: int) -> bool:
        """Check if a printer is connected (checks for stale connections)."""
        if printer_id in self._clients:
            client = self._clients[printer_id]
            # Check staleness and update connected state if needed
            return client.check_staleness()
        return False

    def get_client(self, printer_id: int) -> BambuMQTTClient | None:
        """Get the MQTT client for a printer."""
        return self._clients.get(printer_id)

    def mark_printer_offline(self, printer_id: int):
        """Mark a printer as offline and trigger status callback.

        This is used when we know the printer power was cut (e.g., smart plug turned off)
        to immediately update the UI without waiting for MQTT timeout.
        """
        import logging

        logger = logging.getLogger(__name__)

        if printer_id in self._clients:
            client = self._clients[printer_id]
            if client.state.connected:
                logger.info(f"Marking printer {printer_id} as offline (smart plug power off)")
                client.state.connected = False
                client.state.state = "unknown"
                # Trigger the status change callback to broadcast via WebSocket
                if self._on_status_change:
                    self._schedule_async(self._on_status_change(printer_id, client.state))

    def start_print(self, printer_id: int, filename: str, plate_id: int = 1) -> bool:
        """Start a print on a connected printer."""
        if printer_id in self._clients:
            return self._clients[printer_id].start_print(filename, plate_id)
        return False

    def stop_print(self, printer_id: int) -> bool:
        """Stop the current print on a connected printer."""
        if printer_id in self._clients:
            return self._clients[printer_id].stop_print()
        return False

    async def wait_for_cooldown(
        self,
        printer_id: int,
        target_temp: float = 50.0,
        timeout: int = 600,
        check_interval: int = 10,
    ) -> bool:
        """Wait for the nozzle to cool down to a safe temperature.

        Args:
            printer_id: The printer to monitor
            target_temp: Target temperature to wait for (default 50°C)
            timeout: Maximum seconds to wait (default 600s = 10 min)
            check_interval: Seconds between temperature checks (default 10s)

        Returns:
            True if cooled down, False if timeout or not connected
        """
        import logging

        logger = logging.getLogger(__name__)

        elapsed = 0
        while elapsed < timeout:
            state = self.get_status(printer_id)
            if not state or not state.connected:
                logger.warning(f"Printer {printer_id} disconnected during cooldown wait")
                return False

            # Check nozzle temperature (and nozzle_2 for dual extruders)
            nozzle_temp = state.temperatures.get("nozzle", 0)
            nozzle_2_temp = state.temperatures.get("nozzle_2", 0)
            max_temp = max(nozzle_temp, nozzle_2_temp)

            if max_temp <= target_temp:
                logger.info(f"Printer {printer_id} cooled down to {max_temp}°C")
                return True

            logger.debug(f"Printer {printer_id} nozzle at {max_temp}°C, waiting for {target_temp}°C...")
            await asyncio.sleep(check_interval)
            elapsed += check_interval

        logger.warning(f"Printer {printer_id} cooldown timeout after {timeout}s")
        return False

    def enable_logging(self, printer_id: int, enabled: bool = True) -> bool:
        """Enable or disable MQTT logging for a printer."""
        if printer_id in self._clients:
            self._clients[printer_id].enable_logging(enabled)
            return True
        return False

    def get_logs(self, printer_id: int) -> list[MQTTLogEntry]:
        """Get MQTT logs for a printer."""
        if printer_id in self._clients:
            return self._clients[printer_id].get_logs()
        return []

    def clear_logs(self, printer_id: int) -> bool:
        """Clear MQTT logs for a printer."""
        if printer_id in self._clients:
            self._clients[printer_id].clear_logs()
            return True
        return False

    def is_logging_enabled(self, printer_id: int) -> bool:
        """Check if logging is enabled for a printer."""
        if printer_id in self._clients:
            return self._clients[printer_id].logging_enabled
        return False

    def request_status_update(self, printer_id: int) -> bool:
        """Request a full status update from the printer.

        This sends a 'pushall' command to get the latest data including nozzle info.
        """
        if printer_id in self._clients:
            return self._clients[printer_id].request_status_update()
        return False

    async def test_connection(
        self,
        ip_address: str,
        serial_number: str,
        access_code: str,
    ) -> dict:
        """Test connection to a printer without persisting."""
        client = BambuMQTTClient(
            ip_address=ip_address,
            serial_number=serial_number,
            access_code=access_code,
        )

        try:
            client.connect()
            await asyncio.sleep(2)

            result = {
                "success": client.state.connected,
                "state": client.state.state if client.state.connected else None,
                "model": client.state.raw_data.get("device_model"),
            }
        finally:
            client.disconnect()

        return result


def get_derived_status_name(state: PrinterState) -> str | None:
    """
    Compute a human-readable status name based on printer state.

    Uses stg_cur when available, otherwise derives status from temperature data
    when the printer is heating before a print starts.
    """
    # If we have a valid calibration stage, use it
    # X1 models use -1 for idle, A1/P1 models use 255 for idle
    # Valid stage numbers are 0-254
    if 0 <= state.stg_cur < 255:
        return get_stage_name(state.stg_cur)

    # If not in RUNNING state, no derived status needed
    if state.state != "RUNNING":
        return None

    # Check if we're in an early phase where temperatures are heating
    temps = state.temperatures or {}
    progress = state.progress or 0

    # Only derive heating status when progress is very low (< 2%)
    # This indicates we're in the preparation phase, not actually printing
    if progress >= 2:
        return None

    # Check bed temperature - if target is set and current is significantly below
    bed_temp = temps.get("bed", 0)
    bed_target = temps.get("bed_target", 0)

    # Check nozzle temperature
    nozzle_temp = temps.get("nozzle", 0)
    nozzle_target = temps.get("nozzle_target", 0)

    # Temperature thresholds: consider "heating" if more than 10°C below target
    TEMP_THRESHOLD = 10

    # Determine what's heating (prioritize bed since it takes longer)
    if bed_target > 30 and (bed_target - bed_temp) > TEMP_THRESHOLD:
        return "Heating heatbed"
    elif nozzle_target > 30 and (nozzle_target - nozzle_temp) > TEMP_THRESHOLD:
        return "Heating nozzle"

    # If targets are set but we're close to them, we might be in final prep
    if bed_target > 30 or nozzle_target > 30:
        if progress == 0 and state.layer_num == 0:
            return "Preparing"

    return None


def printer_state_to_dict(state: PrinterState, printer_id: int | None = None) -> dict:
    """Convert PrinterState to a JSON-serializable dict."""
    # Parse AMS data from raw_data
    ams_units = []
    vt_tray = None
    raw_data = state.raw_data or {}

    # Build K-profile lookup map: cali_idx -> k_value
    kprofile_map: dict[int, float] = {}
    for kp in state.kprofiles or []:
        if kp.slot_id is not None and kp.k_value:
            try:
                kprofile_map[kp.slot_id] = float(kp.k_value)
            except (ValueError, TypeError):
                pass

    if "ams" in raw_data and isinstance(raw_data["ams"], list):
        for ams_data in raw_data["ams"]:
            trays = []
            for tray in ams_data.get("tray", []):
                tag_uid = tray.get("tag_uid")
                if tag_uid in ("", "0000000000000000"):
                    tag_uid = None
                tray_uuid = tray.get("tray_uuid")
                if tray_uuid in ("", "00000000000000000000000000000000"):
                    tray_uuid = None

                # Get K value: first try tray's k field, then lookup from K-profiles
                k_value = tray.get("k")
                cali_idx = tray.get("cali_idx")
                if k_value is None and cali_idx is not None and cali_idx in kprofile_map:
                    k_value = kprofile_map[cali_idx]

                trays.append(
                    {
                        "id": tray.get("id", 0),
                        "tray_color": tray.get("tray_color"),
                        "tray_type": tray.get("tray_type"),
                        "tray_sub_brands": tray.get("tray_sub_brands"),
                        "tray_id_name": tray.get("tray_id_name"),
                        "tray_info_idx": tray.get("tray_info_idx"),
                        "remain": tray.get("remain", 0),
                        "k": k_value,
                        "cali_idx": cali_idx,
                        "tag_uid": tag_uid,
                        "tray_uuid": tray_uuid,
                        "nozzle_temp_min": tray.get("nozzle_temp_min"),
                        "nozzle_temp_max": tray.get("nozzle_temp_max"),
                    }
                )
            # Prefer humidity_raw (actual percentage) over humidity (index 1-5)
            humidity_raw = ams_data.get("humidity_raw")
            humidity_idx = ams_data.get("humidity")
            humidity_value = None

            if humidity_raw is not None:
                try:
                    humidity_value = int(humidity_raw)
                except (ValueError, TypeError):
                    pass
            # Fall back to index if no raw value (index is 1-5, not percentage)
            if humidity_value is None and humidity_idx is not None:
                try:
                    humidity_value = int(humidity_idx)
                except (ValueError, TypeError):
                    pass

            # AMS-HT has 1 tray, regular AMS has 4 trays
            is_ams_ht = len(trays) == 1

            ams_units.append(
                {
                    "id": ams_data.get("id", 0),
                    "humidity": humidity_value,
                    "temp": ams_data.get("temp"),
                    "is_ams_ht": is_ams_ht,
                    "tray": trays,
                }
            )

    # Parse virtual tray (external spool)
    if "vt_tray" in raw_data:
        vt_data = raw_data["vt_tray"]
        vt_tag_uid = vt_data.get("tag_uid")
        if vt_tag_uid in ("", "0000000000000000"):
            vt_tag_uid = None
        vt_tray_uuid = vt_data.get("tray_uuid")
        if vt_tray_uuid in ("", "00000000000000000000000000000000"):
            vt_tray_uuid = None

        # Get K value for vt_tray
        vt_k_value = vt_data.get("k")
        vt_cali_idx = vt_data.get("cali_idx")
        if vt_k_value is None and vt_cali_idx is not None and vt_cali_idx in kprofile_map:
            vt_k_value = kprofile_map[vt_cali_idx]

        vt_tray = {
            "id": 254,
            "tray_color": vt_data.get("tray_color"),
            "tray_type": vt_data.get("tray_type"),
            "tray_sub_brands": vt_data.get("tray_sub_brands"),
            "tray_id_name": vt_data.get("tray_id_name"),
            "tray_info_idx": vt_data.get("tray_info_idx"),
            "remain": vt_data.get("remain", 0),
            "k": vt_k_value,
            "cali_idx": vt_cali_idx,
            "tag_uid": vt_tag_uid,
            "tray_uuid": vt_tray_uuid,
            "nozzle_temp_min": vt_data.get("nozzle_temp_min"),
            "nozzle_temp_max": vt_data.get("nozzle_temp_max"),
        }

    # Get ams_extruder_map from raw_data (populated by MQTT handler from AMS info field)
    ams_extruder_map = raw_data.get("ams_extruder_map", {})

    result = {
        "connected": state.connected,
        "state": state.state,
        "current_print": state.current_print,
        "subtask_name": state.subtask_name,
        "gcode_file": state.gcode_file,
        "progress": state.progress,
        "remaining_time": state.remaining_time,
        "layer_num": state.layer_num,
        "total_layers": state.total_layers,
        "temperatures": state.temperatures,
        "hms_errors": [
            {"code": e.code, "attr": e.attr, "module": e.module, "severity": e.severity}
            for e in (state.hms_errors or [])
        ],
        # AMS data for filament colors
        "ams": ams_units if ams_units else None,
        "vt_tray": vt_tray,
        # AMS status for filament change tracking
        "ams_status_main": state.ams_status_main,
        "ams_status_sub": state.ams_status_sub,
        "tray_now": state.tray_now,
        # Per-AMS extruder map: {ams_id: extruder_id} where 0=right, 1=left
        "ams_extruder_map": ams_extruder_map,
        # WiFi signal strength
        "wifi_signal": state.wifi_signal,
        # Calibration stage tracking
        "stg_cur": state.stg_cur,
        "stg_cur_name": get_derived_status_name(state),
        # Printable objects count for skip objects feature
        "printable_objects_count": len(state.printable_objects),
        # Fan speeds (0-100 percentage, None if not available)
        "cooling_fan_speed": state.cooling_fan_speed,
        "big_fan1_speed": state.big_fan1_speed,
        "big_fan2_speed": state.big_fan2_speed,
        "heatbreak_fan_speed": state.heatbreak_fan_speed,
    }
    # Add cover URL if there's an active print and printer_id is provided
    # Include PAUSE/PAUSED states so skip objects modal can show cover
    if printer_id and state.state in ("RUNNING", "PAUSE", "PAUSED") and state.gcode_file:
        result["cover_url"] = f"/api/v1/printers/{printer_id}/cover"
    else:
        result["cover_url"] = None
    return result


# Global printer manager instance
printer_manager = PrinterManager()


async def init_printer_connections(db: AsyncSession):
    """Initialize connections to all active printers."""
    result = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
    printers = result.scalars().all()

    for printer in printers:
        await printer_manager.connect_printer(printer)
