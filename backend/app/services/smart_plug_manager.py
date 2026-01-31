"""Manager for smart plug automation and delayed turn-off."""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.services.homeassistant import homeassistant_service
from backend.app.services.printer_manager import printer_manager
from backend.app.services.tasmota import tasmota_service

if TYPE_CHECKING:
    from backend.app.models.smart_plug import SmartPlug

logger = logging.getLogger(__name__)


class SmartPlugManager:
    """Manages smart plug automation and delayed turn-off."""

    def __init__(self):
        self._pending_off: dict[int, asyncio.Task] = {}  # plug_id -> task
        self._loop: asyncio.AbstractEventLoop | None = None
        self._scheduler_task: asyncio.Task | None = None
        self._last_schedule_check: dict[int, str] = {}  # plug_id -> "HH:MM" last executed

    async def get_service_for_plug(self, plug: "SmartPlug", db: AsyncSession | None = None):
        """Get the appropriate service for the plug type.

        For HA plugs, configures the service with current settings from DB.
        """
        if plug.plug_type == "homeassistant":
            # Configure HA service with current settings
            await self._configure_ha_service(db)
            return homeassistant_service
        return tasmota_service

    async def _configure_ha_service(self, db: AsyncSession | None = None):
        """Configure the HA service with URL and token from settings."""
        from backend.app.models.settings import Settings

        try:
            if db:
                # Use provided session
                result = await db.execute(select(Settings).where(Settings.key == "ha_url"))
                ha_url_setting = result.scalar_one_or_none()
                result = await db.execute(select(Settings).where(Settings.key == "ha_token"))
                ha_token_setting = result.scalar_one_or_none()
            else:
                # Create new session
                from backend.app.core.database import async_session

                async with async_session() as session:
                    result = await session.execute(select(Settings).where(Settings.key == "ha_url"))
                    ha_url_setting = result.scalar_one_or_none()
                    result = await session.execute(select(Settings).where(Settings.key == "ha_token"))
                    ha_token_setting = result.scalar_one_or_none()

            ha_url = ha_url_setting.value if ha_url_setting else ""
            ha_token = ha_token_setting.value if ha_token_setting else ""
            homeassistant_service.configure(ha_url, ha_token)
        except Exception as e:
            logger.warning(f"Failed to configure HA service: {e}")

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the event loop for async operations."""
        self._loop = loop

    def start_scheduler(self):
        """Start the background scheduler for time-based plug control."""
        if self._scheduler_task is None:
            self._scheduler_task = asyncio.create_task(self._schedule_loop())
            logger.info("Smart plug scheduler started")

    def stop_scheduler(self):
        """Stop the background scheduler."""
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None
            logger.info("Smart plug scheduler stopped")

    async def _schedule_loop(self):
        """Background loop that checks scheduled on/off times every minute."""
        while True:
            try:
                await self._check_schedules()
            except Exception as e:
                logger.error(f"Error in schedule check: {e}")

            # Wait until the next minute
            await asyncio.sleep(60)

    async def _check_schedules(self):
        """Check all plugs for scheduled on/off times."""
        from backend.app.core.database import async_session
        from backend.app.models.smart_plug import SmartPlug

        current_time = datetime.now().strftime("%H:%M")

        async with async_session() as db:
            result = await db.execute(
                select(SmartPlug).where(
                    SmartPlug.enabled.is_(True),
                    SmartPlug.schedule_enabled.is_(True),
                )
            )
            plugs = result.scalars().all()

            for plug in plugs:
                service = await self.get_service_for_plug(plug, db)

                # Check if we should turn on
                if plug.schedule_on_time == current_time:
                    last_check = self._last_schedule_check.get(plug.id)
                    if last_check != f"on:{current_time}":
                        logger.info(f"Schedule: Turning on plug '{plug.name}' at {current_time}")
                        success = await service.turn_on(plug)
                        if success:
                            plug.last_state = "ON"
                            plug.last_checked = datetime.utcnow()
                            self._last_schedule_check[plug.id] = f"on:{current_time}"

                # Check if we should turn off
                if plug.schedule_off_time == current_time:
                    last_check = self._last_schedule_check.get(plug.id)
                    if last_check != f"off:{current_time}":
                        logger.info(f"Schedule: Turning off plug '{plug.name}' at {current_time}")
                        success = await service.turn_off(plug)
                        if success:
                            plug.last_state = "OFF"
                            plug.last_checked = datetime.utcnow()
                            self._last_schedule_check[plug.id] = f"off:{current_time}"
                            # Mark printer offline if linked
                            if plug.printer_id:
                                printer_manager.mark_printer_offline(plug.printer_id)

            await db.commit()

    async def _get_plug_for_printer(self, printer_id: int, db: AsyncSession) -> "SmartPlug | None":
        """Get the smart plug linked to a printer."""
        from backend.app.models.smart_plug import SmartPlug

        result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
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
        service = await self.get_service_for_plug(plug, db)
        success = await service.turn_on(plug)

        if success:
            # Update last state and reset auto_off_executed
            plug.last_state = "ON"
            plug.last_checked = datetime.utcnow()
            plug.auto_off_executed = False  # Reset flag when turning on
            await db.commit()

    async def on_print_complete(self, printer_id: int, status: str, db: AsyncSession):
        """Called when a print completes - schedule turn off if configured.

        Only triggers auto-off on successful completion (status='completed').
        Failed prints keep the printer powered on for user investigation.
        """
        plug = await self._get_plug_for_printer(printer_id, db)

        if not plug:
            return

        if not plug.enabled:
            logger.debug(f"Smart plug '{plug.name}' is disabled, skipping auto-off")
            return

        if not plug.auto_off:
            logger.debug(f"Smart plug '{plug.name}' auto_off is disabled")
            return

        # Skip auto-off for HA script entities (scripts can only be triggered, not turned off)
        if plug.plug_type == "homeassistant" and plug.ha_entity_id and plug.ha_entity_id.startswith("script."):
            logger.debug(f"Smart plug '{plug.name}' is a HA script entity, skipping auto-off")
            return

        # Only auto-off on successful completion, not on failures
        # This allows the user to investigate errors before power-off
        if status != "completed":
            logger.info(
                f"Print on printer {printer_id} ended with status '{status}', "
                f"skipping auto-off for plug '{plug.name}' to allow investigation"
            )
            return

        logger.info(f"Print completed successfully on printer {printer_id}, scheduling turn-off for plug '{plug.name}'")

        if plug.off_delay_mode == "time":
            self._schedule_delayed_off(plug, printer_id, plug.off_delay_minutes * 60)
        elif plug.off_delay_mode == "temperature":
            self._schedule_temp_based_off(plug, printer_id, plug.off_temp_threshold)

    def _schedule_delayed_off(self, plug: "SmartPlug", printer_id: int, delay_seconds: int):
        """Schedule turn-off after delay."""
        # Cancel any existing task for this plug
        self._cancel_pending_off(plug.id)

        logger.info(f"Scheduling turn-off for plug '{plug.name}' in {delay_seconds} seconds")

        # Mark as pending in database (survives restarts)
        asyncio.create_task(self._mark_auto_off_pending(plug.id, True))

        task = asyncio.create_task(
            self._delayed_off(
                plug.id,
                plug.plug_type,
                plug.ip_address,
                plug.ha_entity_id,
                plug.username,
                plug.password,
                printer_id,
                delay_seconds,
            )
        )
        self._pending_off[plug.id] = task

    async def _delayed_off(
        self,
        plug_id: int,
        plug_type: str,
        ip_address: str | None,
        ha_entity_id: str | None,
        username: str | None,
        password: str | None,
        printer_id: int,
        delay_seconds: int,
    ):
        """Wait and turn off."""
        try:
            await asyncio.sleep(delay_seconds)

            # Create a minimal plug-like object for the service
            class PlugInfo:
                def __init__(self):
                    self.plug_type = plug_type
                    self.ip_address = ip_address
                    self.ha_entity_id = ha_entity_id
                    self.username = username
                    self.password = password
                    self.name = f"plug_{plug_id}"

            plug_info = PlugInfo()
            service = await self.get_service_for_plug(plug_info)
            success = await service.turn_off(plug_info)
            logger.info(f"Turned off plug {plug_id} after time delay")

            # Mark auto_off_executed in database and update printer status
            if success:
                await self._mark_auto_off_executed(plug_id)
                # Mark the printer as offline immediately
                printer_manager.mark_printer_offline(printer_id)

        except asyncio.CancelledError:
            logger.debug(f"Delayed turn-off cancelled for plug {plug_id}")
        finally:
            self._pending_off.pop(plug_id, None)

    def _schedule_temp_based_off(self, plug: "SmartPlug", printer_id: int, temp_threshold: int):
        """Monitor temperature and turn off when below threshold."""
        # Cancel any existing task for this plug
        self._cancel_pending_off(plug.id)

        logger.info(f"Scheduling temperature-based turn-off for plug '{plug.name}' (threshold: {temp_threshold}°C)")

        # Mark as pending in database (survives restarts)
        asyncio.create_task(self._mark_auto_off_pending(plug.id, True))

        task = asyncio.create_task(
            self._temp_based_off(
                plug.id,
                plug.plug_type,
                plug.ip_address,
                plug.ha_entity_id,
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
        plug_type: str,
        ip_address: str | None,
        ha_entity_id: str | None,
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
                        logger.info(
                            f"Temp check plug {plug_id}: nozzle1={nozzle_temp}°C, "
                            f"nozzle2={nozzle_2_temp}°C, max={max_nozzle_temp}°C, "
                            f"threshold={temp_threshold}°C"
                        )
                    else:
                        logger.info(f"Temp check plug {plug_id}: nozzle={nozzle_temp}°C, threshold={temp_threshold}°C")

                    if max_nozzle_temp < temp_threshold:
                        # All nozzles are below threshold, turn off
                        class PlugInfo:
                            def __init__(self):
                                self.plug_type = plug_type
                                self.ip_address = ip_address
                                self.ha_entity_id = ha_entity_id
                                self.username = username
                                self.password = password
                                self.name = f"plug_{plug_id}"

                        plug_info = PlugInfo()
                        service = await self.get_service_for_plug(plug_info)
                        success = await service.turn_off(plug_info)
                        logger.info(
                            f"Turned off plug {plug_id} after nozzle temp dropped to "
                            f"{max_nozzle_temp}°C (threshold: {temp_threshold}°C)"
                        )

                        # Mark auto_off_executed in database and update printer status
                        if success:
                            await self._mark_auto_off_executed(plug_id)
                            # Mark the printer as offline immediately
                            printer_manager.mark_printer_offline(printer_id)

                        break

                await asyncio.sleep(check_interval)
                elapsed += check_interval

            if elapsed >= max_wait:
                logger.warning(f"Temperature-based turn-off timed out for plug {plug_id} after {max_wait}s")

        except asyncio.CancelledError:
            logger.debug(f"Temperature-based turn-off cancelled for plug {plug_id}")
        finally:
            self._pending_off.pop(plug_id, None)

    async def _mark_auto_off_pending(self, plug_id: int, pending: bool):
        """Mark a plug as having a pending auto-off (survives restarts)."""
        try:
            from backend.app.core.database import async_session
            from backend.app.models.smart_plug import SmartPlug

            async with async_session() as db:
                result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
                plug = result.scalar_one_or_none()
                if plug:
                    plug.auto_off_pending = pending
                    plug.auto_off_pending_since = datetime.utcnow() if pending else None
                    await db.commit()
                    logger.debug(f"Marked plug {plug_id} auto_off_pending={pending}")
        except Exception as e:
            logger.warning(f"Failed to update plug {plug_id} pending state: {e}")

    async def _mark_auto_off_executed(self, plug_id: int):
        """Disable auto-off after it was executed (one-shot behavior)."""
        try:
            from backend.app.core.database import async_session
            from backend.app.models.smart_plug import SmartPlug

            async with async_session() as db:
                result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
                plug = result.scalar_one_or_none()
                if plug:
                    plug.auto_off = False  # Disable auto-off (one-shot behavior)
                    plug.auto_off_executed = False  # Reset the flag
                    plug.auto_off_pending = False  # Clear pending state
                    plug.auto_off_pending_since = None
                    plug.last_state = "OFF"
                    plug.last_checked = datetime.utcnow()
                    await db.commit()
                    logger.info(f"Auto-off executed and disabled for plug {plug_id}")
        except Exception as e:
            logger.warning(f"Failed to update plug {plug_id} after auto-off: {e}")

    def _cancel_pending_off(self, plug_id: int):
        """Cancel any pending off task for this plug."""
        if plug_id in self._pending_off:
            logger.debug(f"Cancelling pending turn-off for plug {plug_id}")
            self._pending_off[plug_id].cancel()
            del self._pending_off[plug_id]
            # Clear pending state in database
            asyncio.create_task(self._mark_auto_off_pending(plug_id, False))

    def cancel_all_pending(self):
        """Cancel all pending turn-off tasks."""
        for plug_id in list(self._pending_off.keys()):
            self._cancel_pending_off(plug_id)

    async def resume_pending_auto_offs(self):
        """Resume any pending auto-offs that were interrupted by a restart.

        Called on startup to check for plugs that had auto-off pending but
        never completed (e.g., due to service restart).
        """
        try:
            from backend.app.core.database import async_session
            from backend.app.models.smart_plug import SmartPlug

            async with async_session() as db:
                # Find all plugs with pending auto-off
                result = await db.execute(
                    select(SmartPlug).where(
                        SmartPlug.auto_off_pending.is_(True),
                        SmartPlug.printer_id.isnot(None),
                    )
                )
                pending_plugs = result.scalars().all()

                for plug in pending_plugs:
                    # Check how long it's been pending (timeout after 2 hours)
                    if plug.auto_off_pending_since:
                        elapsed = (datetime.utcnow() - plug.auto_off_pending_since).total_seconds()
                        if elapsed > 7200:  # 2 hours
                            logger.warning(
                                f"Auto-off for plug '{plug.name}' was pending for {elapsed / 60:.0f} minutes, "
                                f"clearing stale pending state"
                            )
                            plug.auto_off_pending = False
                            plug.auto_off_pending_since = None
                            await db.commit()
                            continue

                    logger.info(f"Resuming pending auto-off for plug '{plug.name}' (printer {plug.printer_id})")

                    # Resume the appropriate off mode
                    if plug.off_delay_mode == "temperature":
                        self._schedule_temp_based_off(plug, plug.printer_id, plug.off_temp_threshold)
                    else:
                        # For time mode, just turn off immediately since delay already passed
                        logger.info(f"Time-based auto-off was pending, turning off plug '{plug.name}' now")

                        service = await self.get_service_for_plug(plug, db)
                        success = await service.turn_off(plug)
                        if success:
                            await self._mark_auto_off_executed(plug.id)
                            printer_manager.mark_printer_offline(plug.printer_id)

                if pending_plugs:
                    logger.info(f"Resumed {len(pending_plugs)} pending auto-off(s)")

        except Exception as e:
            logger.warning(f"Failed to resume pending auto-offs: {e}")


# Global singleton
smart_plug_manager = SmartPlugManager()
