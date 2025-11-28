import asyncio
from typing import Callable
from dataclasses import asdict

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.models.printer import Printer
from backend.app.services.bambu_mqtt import BambuMQTTClient, PrinterState
from backend.app.services.bambu_ftp import BambuFTPClient


class PrinterManager:
    """Manager for multiple printer connections."""

    def __init__(self):
        self._clients: dict[int, BambuMQTTClient] = {}
        self._on_print_start: Callable[[int, dict], None] | None = None
        self._on_print_complete: Callable[[int, dict], None] | None = None
        self._on_status_change: Callable[[int, PrinterState], None] | None = None
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

    def _schedule_async(self, coro):
        """Schedule an async coroutine from a sync context."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def connect_printer(self, printer: Printer) -> bool:
        """Connect to a printer."""
        if printer.id in self._clients:
            self.disconnect_printer(printer.id)

        printer_id = printer.id

        def on_state_change(state: PrinterState):
            if self._on_status_change:
                self._schedule_async(
                    self._on_status_change(printer_id, state)
                )

        def on_print_start(data: dict):
            if self._on_print_start:
                self._schedule_async(
                    self._on_print_start(printer_id, data)
                )

        def on_print_complete(data: dict):
            if self._on_print_complete:
                self._schedule_async(
                    self._on_print_complete(printer_id, data)
                )

        client = BambuMQTTClient(
            ip_address=printer.ip_address,
            serial_number=printer.serial_number,
            access_code=printer.access_code,
            on_state_change=on_state_change,
            on_print_start=on_print_start,
            on_print_complete=on_print_complete,
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
        """Get the current status of a printer."""
        if printer_id in self._clients:
            return self._clients[printer_id].state
        return None

    def get_all_statuses(self) -> dict[int, PrinterState]:
        """Get status of all connected printers."""
        return {
            printer_id: client.state
            for printer_id, client in self._clients.items()
        }

    def is_connected(self, printer_id: int) -> bool:
        """Check if a printer is connected."""
        if printer_id in self._clients:
            return self._clients[printer_id].state.connected
        return False

    def start_print(self, printer_id: int, filename: str) -> bool:
        """Start a print on a connected printer."""
        if printer_id in self._clients:
            return self._clients[printer_id].start_print(filename)
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


def printer_state_to_dict(state: PrinterState, printer_id: int | None = None) -> dict:
    """Convert PrinterState to a JSON-serializable dict."""
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
    }
    # Add cover URL if there's an active print and printer_id is provided
    if printer_id and state.state == "RUNNING" and state.gcode_file:
        result["cover_url"] = f"/api/v1/printers/{printer_id}/cover"
    else:
        result["cover_url"] = None
    return result


# Global printer manager instance
printer_manager = PrinterManager()


async def init_printer_connections(db: AsyncSession):
    """Initialize connections to all active printers."""
    result = await db.execute(
        select(Printer).where(Printer.is_active == True)
    )
    printers = result.scalars().all()

    for printer in printers:
        await printer_manager.connect_printer(printer)
