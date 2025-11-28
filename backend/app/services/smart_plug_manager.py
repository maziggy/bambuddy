"""Manager for smart plug automation and delayed turn-off."""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.services.tasmota import tasmota_service
from backend.app.services.printer_manager import printer_manager

if TYPE_CHECKING:
    from backend.app.models.smart_plug import SmartPlug

logger = logging.getLogger(__name__)


class SmartPlugManager:
    """Manages smart plug automation and delayed turn-off."""

    def __init__(self):
        self._pending_off: dict[int, asyncio.Task] = {}  # plug_id -> task
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the event loop for async operations."""
        self._loop = loop

    async def _get_plug_for_printer(
        self, printer_id: int, db: AsyncSession
    ) -> "SmartPlug | None":
        """Get the smart plug linked to a printer."""
        from backend.app.models.smart_plug import SmartPlug

        result = await db.execute(
            select(SmartPlug).where(SmartPlug.printer_id == printer_id)
        )
        return result.scalar_one_or_none()

    async def on_print_start(self, printer_id: int, db: AsyncSession):
        """Called when a print starts - turn on plug if configured."""
        plug = await self._get_plug_for_printer(printer_id, db)

        if not plug:
            return

        if not plug.enabled:
            logger.debug(f"Smart plug '{plug.name}' is disabled, skipping auto-on")
            return

        if not plug.auto_on:
            logger.debug(f"Smart plug '{plug.name}' auto_on is disabled")
            return

        # Cancel any pending off task
        self._cancel_pending_off(plug.id)

        # Turn on the plug
        logger.info(f"Print started on printer {printer_id}, turning on plug '{plug.name}'")
        success = await tasmota_service.turn_on(plug)

        if success:
            # Update last state
            plug.last_state = "ON"
            plug.last_checked = datetime.utcnow()
            await db.commit()

    async def on_print_complete(
        self, printer_id: int, status: str, db: AsyncSession
    ):
        """Called when a print completes - schedule turn off if configured."""
        plug = await self._get_plug_for_printer(printer_id, db)

        if not plug:
            return

        if not plug.enabled:
            logger.debug(f"Smart plug '{plug.name}' is disabled, skipping auto-off")
            return

        if not plug.auto_off:
            logger.debug(f"Smart plug '{plug.name}' auto_off is disabled")
            return

        logger.info(
            f"Print completed on printer {printer_id} (status: {status}), "
            f"scheduling turn-off for plug '{plug.name}'"
        )

        if plug.off_delay_mode == "time":
            self._schedule_delayed_off(plug, plug.off_delay_minutes * 60)
        elif plug.off_delay_mode == "temperature":
            self._schedule_temp_based_off(plug, printer_id, plug.off_temp_threshold)

    def _schedule_delayed_off(self, plug: "SmartPlug", delay_seconds: int):
        """Schedule turn-off after delay."""
        # Cancel any existing task for this plug
        self._cancel_pending_off(plug.id)

        logger.info(
            f"Scheduling turn-off for plug '{plug.name}' in {delay_seconds} seconds"
        )

        task = asyncio.create_task(
            self._delayed_off(plug.id, plug.ip_address, plug.username, plug.password, delay_seconds)
        )
        self._pending_off[plug.id] = task

    async def _delayed_off(
        self,
        plug_id: int,
        ip_address: str,
        username: str | None,
        password: str | None,
        delay_seconds: int,
    ):
        """Wait and turn off."""
        try:
            await asyncio.sleep(delay_seconds)

            # Create a minimal plug-like object for the tasmota service
            class PlugInfo:
                def __init__(self):
                    self.ip_address = ip_address
                    self.username = username
                    self.password = password
                    self.name = f"plug_{plug_id}"

            plug_info = PlugInfo()
            await tasmota_service.turn_off(plug_info)
            logger.info(f"Turned off plug {plug_id} after time delay")

        except asyncio.CancelledError:
            logger.debug(f"Delayed turn-off cancelled for plug {plug_id}")
        finally:
            self._pending_off.pop(plug_id, None)

    def _schedule_temp_based_off(
        self, plug: "SmartPlug", printer_id: int, temp_threshold: int
    ):
        """Monitor temperature and turn off when below threshold."""
        # Cancel any existing task for this plug
        self._cancel_pending_off(plug.id)

        logger.info(
            f"Scheduling temperature-based turn-off for plug '{plug.name}' "
            f"(threshold: {temp_threshold}°C)"
        )

        task = asyncio.create_task(
            self._temp_based_off(
                plug.id,
                plug.ip_address,
                plug.username,
                plug.password,
                printer_id,
                temp_threshold,
            )
        )
        self._pending_off[plug.id] = task

    async def _temp_based_off(
        self,
        plug_id: int,
        ip_address: str,
        username: str | None,
        password: str | None,
        printer_id: int,
        temp_threshold: int,
    ):
        """Poll temperature until below threshold, then turn off.

        For dual-extruder printers (H2 series), checks both nozzles.
        """
        try:
            check_interval = 10  # seconds
            max_wait = 3600  # 1 hour max
            elapsed = 0

            while elapsed < max_wait:
                status = printer_manager.get_status(printer_id)

                if status:
                    temps = status.temperatures or {}
                    nozzle_temp = temps.get("nozzle", 999)
                    # Check second nozzle for dual-extruder printers (H2 series)
                    nozzle_2_temp = temps.get("nozzle_2")

                    # Get the maximum temperature across all nozzles
                    max_nozzle_temp = nozzle_temp
                    if nozzle_2_temp is not None:
                        max_nozzle_temp = max(nozzle_temp, nozzle_2_temp)
                        logger.debug(
                            f"Checking temp for plug {plug_id}: nozzle1={nozzle_temp}°C, "
                            f"nozzle2={nozzle_2_temp}°C, max={max_nozzle_temp}°C, "
                            f"threshold={temp_threshold}°C"
                        )
                    else:
                        logger.debug(
                            f"Checking temp for plug {plug_id}: nozzle={nozzle_temp}°C, "
                            f"threshold={temp_threshold}°C"
                        )

                    if max_nozzle_temp < temp_threshold:
                        # All nozzles are below threshold, turn off
                        class PlugInfo:
                            def __init__(self):
                                self.ip_address = ip_address
                                self.username = username
                                self.password = password
                                self.name = f"plug_{plug_id}"

                        plug_info = PlugInfo()
                        await tasmota_service.turn_off(plug_info)
                        logger.info(
                            f"Turned off plug {plug_id} after nozzle temp dropped to "
                            f"{max_nozzle_temp}°C (threshold: {temp_threshold}°C)"
                        )
                        break

                await asyncio.sleep(check_interval)
                elapsed += check_interval

            if elapsed >= max_wait:
                logger.warning(
                    f"Temperature-based turn-off timed out for plug {plug_id} after {max_wait}s"
                )

        except asyncio.CancelledError:
            logger.debug(f"Temperature-based turn-off cancelled for plug {plug_id}")
        finally:
            self._pending_off.pop(plug_id, None)

    def _cancel_pending_off(self, plug_id: int):
        """Cancel any pending off task for this plug."""
        if plug_id in self._pending_off:
            logger.debug(f"Cancelling pending turn-off for plug {plug_id}")
            self._pending_off[plug_id].cancel()
            del self._pending_off[plug_id]

    def cancel_all_pending(self):
        """Cancel all pending turn-off tasks."""
        for plug_id in list(self._pending_off.keys()):
            self._cancel_pending_off(plug_id)


# Global singleton
smart_plug_manager = SmartPlugManager()
