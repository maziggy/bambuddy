"""Manager for smart plug automation and delayed turn-off."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.tasks import spawn_background_task
from backend.app.services.homeassistant import homeassistant_service
from backend.app.services.mqtt_relay import mqtt_relay
from backend.app.services.plug_energy import get_plug_energy
from backend.app.services.printer_manager import printer_manager
from backend.app.services.rest_smart_plug import rest_smart_plug_service
from backend.app.services.tasmota import tasmota_service
from backend.app.utils.local_time import next_local_hour, to_naive_utc, utcnow_naive

if TYPE_CHECKING:
    from backend.app.models.smart_plug import SmartPlug

logger = logging.getLogger(__name__)


async def publish_plug_state(
    plug_id: int, plug_name: str, printer_id: int | None, *, on: bool, ha_entity_id: str | None = None
) -> None:
    """Tell the MQTT relay a plug was switched (smart_plugs/on or /off).

    Called after every successful on or off command, whatever sent it, also when
    the plug already was in that state. A Home Assistant script run through its
    entity is not a switch and publishes nothing. The printer name is read from
    the database: the connection cache keeps a renamed printer's old name and has
    none for an inactive one. Never raises: the plug did switch, and a broker
    problem must not turn that into an error.
    """
    if ha_entity_id and ha_entity_id.startswith("script."):
        return
    if not mqtt_relay.enabled or not mqtt_relay.connected:
        return
    try:
        printer_name = None
        if printer_id:
            from backend.app.core import database
            from backend.app.models.printer import Printer

            async with database.async_session() as db:
                printer_name = (
                    await db.execute(select(Printer.name).where(Printer.id == printer_id))
                ).scalar_one_or_none()
        await mqtt_relay.on_smart_plug_state(
            plug_id=plug_id,
            plug_name=plug_name,
            state="on" if on else "off",
            printer_id=printer_id,
            printer_name=printer_name,
        )
    except Exception as e:
        logger.debug("MQTT relay: failed to publish the state of plug %s: %s", plug_id, e)


def _is_script_plug(plug) -> bool:
    """A Home Assistant script entity: it can be run, not switched."""
    return (
        getattr(plug, "plug_type", None) == "homeassistant"
        and bool(getattr(plug, "ha_entity_id", None))
        and plug.ha_entity_id.startswith("script.")
    )


async def run_linked_scripts(printer_id: int, plug_state: str, db: AsyncSession) -> None:
    """Run the HA scripts linked to a printer after its power plug switched.

    ON runs the scripts set to "Run when printer turns on", OFF those set to
    "Run when printer turns off". Never raises: a script that fails must not
    undo the switch that triggered it.
    """
    from backend.app.models.smart_plug import SmartPlug

    result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
    for plug in result.scalars().all():
        # "Enabled" is the script's automation switch, as at print start.
        if not _is_script_plug(plug) or not plug.enabled:
            continue
        if plug_state == "ON" and plug.auto_on:
            logger.info("Auto-triggering script '%s' on printer power-on", plug.name)
        elif plug_state == "OFF" and plug.auto_off:
            logger.info("Auto-triggering script '%s' on printer power-off", plug.name)
        else:
            continue
        try:
            service = await smart_plug_manager.get_service_for_plug(plug, db)
            await service.turn_on(plug)  # Scripts are triggered by calling turn_on
        except Exception as e:
            logger.error("Failed to trigger script '%s': %s", plug.name, e)


# (printer_id, state) -> when its scripts last ran after an automatic switch.
# Two plugs feeding one printer both auto-off after a print; the printer
# turned off once.
_recent_automatic_script_runs: dict[tuple[int, str], float] = {}
_AUTOMATIC_SCRIPT_RUN_WINDOW_S = 120.0


async def run_linked_scripts_after_automatic_switch(printer_id: int | None, plug, plug_state: str) -> None:
    """Linked scripts for a printer's power plug switched by Bambuddy itself.

    Only for a plug that feeds the printer: an accessory switching is not the
    printer turning on or off. Print start is not routed here -- it runs the
    "on" scripts itself, as linked plugs with auto-on.
    """
    if not printer_id or _is_script_plug(plug) or not getattr(plug, "controls_printer_power", True):
        return
    now = time.monotonic()
    last = _recent_automatic_script_runs.get((printer_id, plug_state))
    if last is not None and now - last < _AUTOMATIC_SCRIPT_RUN_WINDOW_S:
        return
    _recent_automatic_script_runs[(printer_id, plug_state)] = now
    from backend.app.core.database import async_session

    try:
        async with async_session() as db:
            await run_linked_scripts(printer_id, plug_state, db)
    except Exception as e:
        logger.warning("Could not run the scripts linked to printer %s: %s", printer_id, e)


class SmartPlugManager:
    """Manages smart plug automation and delayed turn-off."""

    def __init__(self):
        self._pending_off: dict[int, asyncio.Task] = {}  # plug_id -> task
        self._scheduler_task: asyncio.Task | None = None
        self._snapshot_task: asyncio.Task | None = None
        self._power_alert_task: asyncio.Task | None = None
        self._relay_energy_task: asyncio.Task | None = None
        self._last_schedule_check: dict[int, str] = {}  # plug_id -> "HH:MM" last executed

    async def get_service_for_plug(self, plug: "SmartPlug", db: AsyncSession | None = None):
        """Get the appropriate service for the plug type.

        For HA plugs, configures the service with current settings from DB.
        """
        if plug.plug_type == "homeassistant":
            # Configure HA service with current settings
            await self._configure_ha_service(db)
            return homeassistant_service
        if plug.plug_type == "rest":
            return rest_smart_plug_service
        return tasmota_service

    async def _configure_ha_service(self, db: AsyncSession | None = None):
        """Configure the HA service with URL and token from settings."""
        from backend.app.api.routes.settings import get_homeassistant_settings

        try:
            if db:
                # Use provided session
                ha_settings = await get_homeassistant_settings(db)
            else:
                # Create new session
                from backend.app.core.database import async_session

                async with async_session() as session:
                    ha_settings = await get_homeassistant_settings(session)

            homeassistant_service.configure(ha_settings["ha_url"], ha_settings["ha_token"])
        except Exception as e:
            logger.warning("Failed to configure HA service: %s", e)

    def start_scheduler(self):
        """Start the background scheduler for time-based plug control."""
        if self._scheduler_task is None:
            self._scheduler_task = asyncio.create_task(self._schedule_loop())
            logger.info("Smart plug scheduler started")
        if self._snapshot_task is None:
            self._snapshot_task = asyncio.create_task(self._snapshot_loop())
            logger.info("Smart plug energy snapshot loop started")
        if self._power_alert_task is None:
            self._power_alert_task = asyncio.create_task(self._power_alert_loop())
            logger.info("Smart plug power alert loop started")
        if self._relay_energy_task is None:
            self._relay_energy_task = asyncio.create_task(self._relay_energy_loop())
            logger.info("Smart plug MQTT relay energy loop started")

    def stop_scheduler(self):
        """Stop the background scheduler."""
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None
            logger.info("Smart plug scheduler stopped")
        if self._snapshot_task:
            self._snapshot_task.cancel()
            self._snapshot_task = None
            logger.info("Smart plug energy snapshot loop stopped")
        if self._power_alert_task:
            self._power_alert_task.cancel()
            self._power_alert_task = None
            logger.info("Smart plug power alert loop stopped")
        if self._relay_energy_task:
            self._relay_energy_task.cancel()
            self._relay_energy_task = None
            logger.info("Smart plug MQTT relay energy loop stopped")

    async def _schedule_loop(self):
        """Background loop that checks scheduled on/off times every minute."""
        while True:
            try:
                await self._check_schedules()
            except Exception as e:
                logger.error("Error in schedule check: %s", e)

            # Wait until the next minute
            await asyncio.sleep(60)

    async def _power_alert_loop(self):
        """Background loop that checks plugs with power alerts on, once a minute.

        Without it an alert would only be noticed while someone had a printer
        page open, since the plug status is otherwise only read for display.
        """
        from backend.app.core import database
        from backend.app.services.power_alerts import check_all_plugs

        await asyncio.sleep(30)
        while True:
            try:
                # Looked up on each pass: a backup restore replaces the session factory.
                async with database.async_session() as db:
                    await check_all_plugs(db)
            except Exception as e:
                logger.error("Error in power alert check: %s", e)
            await asyncio.sleep(60)

    async def _snapshot_loop(self):
        """Background loop that captures each plug's lifetime energy counter.

        Powers date-range queries in "total consumption" energy mode (#941) and,
        since #2539, the derived Today / Yesterday figures for every plug that
        reports only a cumulative counter.

        Ticks on the local hour rather than every 3600s from boot. That is what
        makes the derivation exact: a drifting timer leaves the last snapshot
        before midnight up to an hour early, and an hour of a printer's draw is
        a real number of watt-hours to lose off the day boundary. Aligning to the
        *local* hour also lands a tick on local midnight in the half-hour-offset
        timezones (India, Nepal), where midnight is not on a UTC hour at all.
        """
        # Short warm-up delay so other services finish booting; still gives us an
        # initial snapshot well before the first boundary.
        await asyncio.sleep(30)
        while True:
            try:
                await self._capture_energy_snapshots()
            except Exception as e:
                logger.error("Error in energy snapshot capture: %s", e)

            now = datetime.now(timezone.utc)
            delay = (next_local_hour(now) - now).total_seconds()
            await asyncio.sleep(max(delay, 60))

    async def _relay_energy_loop(self):
        """Background loop that publishes every plug's energy to the MQTT relay, once a minute.

        The relay's smart_plugs/energy topic has nothing else feeding it: the
        plug status is otherwise only read for display, for power alerts and,
        hourly, for the lifetime counter.
        """
        from backend.app.core import database

        await asyncio.sleep(30)
        while True:
            try:
                # Looked up on each pass: a backup restore replaces the session factory.
                async with database.async_session() as db:
                    await self._publish_energy_to_relay(db)
            except Exception as e:
                logger.error("Error publishing plug energy to the MQTT relay: %s", e)
            await asyncio.sleep(60)

    async def _publish_energy_to_relay(self, db: AsyncSession) -> None:
        """Read each enabled plug's power and energy and publish it to the MQTT relay.

        Nothing is read while the relay is off or disconnected: on an install
        without the relay this costs no request to any plug.
        """
        from backend.app.models.smart_plug import SmartPlug

        if not mqtt_relay.enabled or not mqtt_relay.connected:
            return
        plugs = (await db.execute(select(SmartPlug).where(SmartPlug.enabled.is_(True)))).scalars().all()
        for plug in plugs:
            # An MQTT plug's values are its last message; once it stops
            # reporting they are stale, not a reading.
            if plug.plug_type == "mqtt" and not mqtt_relay.smart_plug_service.is_reachable(plug.id):
                continue
            try:
                energy = await get_plug_energy(plug, db)
            except Exception as e:
                logger.debug("MQTT relay: failed to read energy from plug %s: %s", plug.id, e)
                continue
            if not energy:
                continue
            await mqtt_relay.on_smart_plug_energy(
                plug.id, plug.name, energy.get("power"), energy.get("today"), energy.get("total")
            )

    async def _capture_energy_snapshots(self):
        """Capture one energy snapshot row per plug with a usable lifetime counter."""
        from backend.app.core.database import async_session
        from backend.app.models.smart_plug import SmartPlug

        async with async_session() as db:
            plugs_result = await db.execute(select(SmartPlug).where(SmartPlug.enabled.is_(True)))
            plugs = list(plugs_result.scalars().all())
            if not plugs:
                return

            # Naive UTC: the column is naive, and asyncpg rejects an aware value
            # outright (SQLite quietly drops the offset, which is why this went
            # unnoticed — on Postgres the whole capture raised).
            now = utcnow_naive()
            # One price for every row of this tick: read from Home Assistant
            # when that's the source, so it is refreshed hourly (#1251).
            from backend.app.services.energy_price import current_price, new_snapshot

            price = await current_price(db, remember=True)
            captured = 0
            for plug in plugs:
                # MQTT plugs only publish a "today" counter that resets at midnight —
                # they can never feed cumulative snapshots, so skip them outright to
                # avoid a noisy tasmota-service fallback attempt on an IP-less plug.
                if plug.plug_type == "mqtt":
                    continue
                try:
                    service = await self.get_service_for_plug(plug, db)
                    energy = await service.get_energy(plug)
                except Exception as e:
                    logger.debug("Snapshot: failed to read energy from plug %s: %s", plug.id, e)
                    continue
                if not energy:
                    continue
                lifetime = energy.get("total")
                if lifetime is None:
                    # The plug exposes no cumulative counter — a REST plug with only
                    # rest_energy_path set, say. Nothing to snapshot, and its Today
                    # comes straight from the device anyway.
                    continue
                db.add(
                    await new_snapshot(db, plug_id=plug.id, recorded_at=now, lifetime_kwh=float(lifetime), price=price)
                )
                captured += 1

            # Commits the price read above too, even with nothing captured.
            await db.commit()
            if captured:
                logger.info("Captured %d energy snapshot(s)", captured)

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
                        logger.info("Schedule: Turning on plug '%s' at %s", plug.name, current_time)
                        success = await service.turn_on(plug)
                        if success:
                            plug.last_state = "ON"
                            plug.last_checked = utcnow_naive()
                            self._last_schedule_check[plug.id] = f"on:{current_time}"
                            await publish_plug_state(
                                plug.id, plug.name, plug.printer_id, on=True, ha_entity_id=plug.ha_entity_id
                            )
                            await run_linked_scripts_after_automatic_switch(plug.printer_id, plug, "ON")

                # Check if we should turn off
                if plug.schedule_off_time == current_time:
                    last_check = self._last_schedule_check.get(plug.id)
                    if last_check != f"off:{current_time}":
                        logger.info("Schedule: Turning off plug '%s' at %s", plug.name, current_time)
                        success = await service.turn_off(plug)
                        if success:
                            plug.last_state = "OFF"
                            plug.last_checked = utcnow_naive()
                            self._last_schedule_check[plug.id] = f"off:{current_time}"
                            await publish_plug_state(
                                plug.id, plug.name, plug.printer_id, on=False, ha_entity_id=plug.ha_entity_id
                            )
                            # Mark printer offline if this plug feeds it (#2629)
                            if plug.printer_id and plug.controls_printer_power:
                                printer_manager.mark_printer_offline(plug.printer_id)
                            await run_linked_scripts_after_automatic_switch(plug.printer_id, plug, "OFF")

            await db.commit()

    async def _get_plugs_for_printer(self, printer_id: int, db: AsyncSession) -> list["SmartPlug"]:
        """Get all smart plugs linked to a printer for automation control."""
        from backend.app.models.smart_plug import SmartPlug

        result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
        return list(result.scalars().all())

    async def on_print_start(self, printer_id: int, db: AsyncSession):
        """Called when a print starts - turn on all plugs linked to this printer."""
        plugs = await self._get_plugs_for_printer(printer_id, db)

        if not plugs:
            return

        for plug in plugs:
            if not plug.enabled:
                logger.debug("Smart plug '%s' is disabled, skipping auto-on", plug.name)
                continue

            # Cancel any pending off task FIRST — a re-print must abort a
            # scheduled auto-off regardless of the plug's auto_on setting
            # (#1890). Previously this lived behind the auto_on gate, so a plug
            # with auto_on disabled kept its pending off and cut power mid-print.
            self._cancel_pending_off(plug.id)

            if not plug.auto_on:
                logger.debug("Smart plug '%s' auto_on is disabled", plug.name)
                continue

            # Turn on the plug
            logger.info("Print started on printer %s, turning on plug '%s'", printer_id, plug.name)
            try:
                service = await self.get_service_for_plug(plug, db)
                success = await service.turn_on(plug)

                if success:
                    plug.last_state = "ON"
                    plug.last_checked = utcnow_naive()
                    plug.auto_off_executed = False  # Reset flag when turning on
                    await publish_plug_state(
                        plug.id, plug.name, plug.printer_id, on=True, ha_entity_id=plug.ha_entity_id
                    )
            except Exception as e:
                logger.warning("Failed to turn on plug '%s' for printer %s: %s", plug.name, printer_id, e)

        await db.commit()

    async def on_print_complete(self, printer_id: int, status: str, db: AsyncSession):
        """Called when a print completes - schedule turn off for all plugs linked to this printer.

        Only triggers auto-off on successful completion (status='completed').
        Failed prints keep the printer powered on for user investigation.
        """
        # Only auto-off on successful completion, not on failures
        if status != "completed":
            logger.info(
                "Print on printer %s ended with status '%s', skipping auto-off to allow investigation",
                printer_id,
                status,
            )
            return

        plugs = await self._get_plugs_for_printer(printer_id, db)

        if not plugs:
            return

        for plug in plugs:
            if not plug.enabled:
                logger.debug("Smart plug '%s' is disabled, skipping auto-off", plug.name)
                continue

            if not plug.auto_off:
                logger.debug("Smart plug '%s' auto_off is disabled", plug.name)
                continue

            # Skip auto-off for HA script entities (scripts can only be triggered, not turned off)
            if plug.plug_type == "homeassistant" and plug.ha_entity_id and plug.ha_entity_id.startswith("script."):
                logger.debug("Smart plug '%s' is a HA script entity, skipping auto-off", plug.name)
                continue

            logger.info(
                "Print completed successfully on printer %s, scheduling turn-off for plug '%s'",
                printer_id,
                plug.name,
            )

            self._schedule_off_per_mode(plug, printer_id)

    def _schedule_off_per_mode(self, plug: "SmartPlug", printer_id: int):
        """Schedule an auto-off using the plug's configured off strategy.

        Honours the per-plug ``off_delay_mode`` — ``time`` waits
        ``off_delay_minutes``; ``temperature`` waits until the nozzle drops
        below ``off_temp_threshold`` (#1890 — the queue/scheduler auto-off
        paths used to hardcode 50°C / 600s and ignore these settings). Both
        branches register a cancellable task in ``_pending_off``, so a re-print
        cancels the pending off via :meth:`on_print_start`.
        """
        if plug.off_delay_mode == "temperature":
            self._schedule_temp_based_off(plug, printer_id, plug.off_temp_threshold)
        else:
            # Default / "time": also the safe fallback for any unexpected value.
            self._schedule_delayed_off(plug, printer_id, plug.off_delay_minutes * 60)

    async def schedule_off_after_queue_job(self, printer_id: int, db: AsyncSession):
        """Schedule auto-off for a printer after a queue job that opted in.

        The print-queue "auto off after this job" toggle (`auto_off_after`) is
        a per-job override, independent of the plug's global ``auto_off`` flag —
        so unlike :meth:`on_print_complete` this does NOT gate on ``plug.auto_off``.
        It still honours ``enabled`` and skips HA-script entities (which can only
        be triggered, not turned off), and uses each plug's configured off
        strategy via :meth:`_schedule_off_per_mode`. Replaces three inline
        cooldown waits (50°C, 600s) that ignored plug settings,
        fired on the cooldown *timeout* regardless of print state, and could not
        be cancelled by a re-print (#1890).
        """
        plugs = await self._get_plugs_for_printer(printer_id, db)
        for plug in plugs:
            if not plug.enabled:
                logger.debug("Smart plug '%s' is disabled, skipping queue auto-off", plug.name)
                continue
            if plug.plug_type == "homeassistant" and plug.ha_entity_id and plug.ha_entity_id.startswith("script."):
                logger.debug("Smart plug '%s' is a HA script entity, skipping queue auto-off", plug.name)
                continue
            logger.info(
                "Queue job finished on printer %s, scheduling turn-off for plug '%s'",
                printer_id,
                plug.name,
            )
            self._schedule_off_per_mode(plug, printer_id)

    async def on_drying_complete(self, printer_id: int, db: AsyncSession):
        """Schedule turn-off for plugs flagged ``auto_off_after_drying`` when
        an AMS drying cycle finishes on this printer (#1349).

        Mirrors :meth:`on_print_complete` but uses the drying-specific
        toggle and delay. Iterates every plug linked to the printer and
        fires only on the ones the user has opted-in via the per-plug
        toggle. Always uses the time-delay branch — temperature-based
        cooldown is about the printer's hotend, which isn't meaningful
        after a drying cycle (AMS chamber is the thing that's hot, and
        Bambuddy doesn't track its temperature).
        """
        plugs = await self._get_plugs_for_printer(printer_id, db)
        if not plugs:
            return

        for plug in plugs:
            if not plug.enabled:
                logger.debug("Smart plug '%s' is disabled, skipping drying auto-off", plug.name)
                continue

            if not plug.auto_off_after_drying:
                logger.debug("Smart plug '%s' auto_off_after_drying is disabled, skipping", plug.name)
                continue

            # HA script entities can only be triggered, not turned off — same
            # guard the print-finish path uses.
            if plug.plug_type == "homeassistant" and plug.ha_entity_id and plug.ha_entity_id.startswith("script."):
                logger.debug("Smart plug '%s' is a HA script entity, skipping drying auto-off", plug.name)
                continue

            logger.info(
                "Drying completed on printer %s, scheduling turn-off for plug '%s' in %d min",
                printer_id,
                plug.name,
                plug.off_delay_after_drying_minutes,
            )
            self._schedule_delayed_off(plug, printer_id, plug.off_delay_after_drying_minutes * 60)

    def _schedule_delayed_off(self, plug: "SmartPlug", printer_id: int, delay_seconds: int):
        """Schedule turn-off after delay."""
        # Cancel any existing task for this plug
        self._cancel_pending_off(plug.id)

        logger.info("Scheduling turn-off for plug '%s' in %s seconds", plug.name, delay_seconds)

        # Mark as pending in database (survives restarts)
        spawn_background_task(self._mark_auto_off_pending(plug.id, True), name=f"plug-auto-off-pending-{plug.id}")

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
                controls_printer_power=plug.controls_printer_power,
                plug_name=plug.name,
                rest_off_url=plug.rest_off_url if plug.plug_type == "rest" else None,
                rest_off_body=plug.rest_off_body if plug.plug_type == "rest" else None,
                rest_method=plug.rest_method if plug.plug_type == "rest" else None,
                rest_headers=plug.rest_headers if plug.plug_type == "rest" else None,
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
        *,
        controls_printer_power: bool = True,
        plug_name: str = "",
        rest_off_url: str | None = None,
        rest_off_body: str | None = None,
        rest_method: str | None = None,
        rest_headers: str | None = None,
    ):
        """Wait and turn off."""
        try:
            await asyncio.sleep(delay_seconds)

            # #1890: never cut power while a print is loaded / running. The
            # delay fires unconditionally after N minutes, so if the user
            # re-started (or reprinted) in the meantime, the printer is active
            # again — skip the off and clear the pending flag rather than
            # killing the print mid-way.
            if printer_manager.is_print_active(printer_id):
                logger.info(
                    "Skipping auto-off for plug %s: printer %s is printing again (state=%s)",
                    plug_id,
                    printer_id,
                    getattr(printer_manager.get_status(printer_id), "state", "unknown"),
                )
                await self._mark_auto_off_pending(plug_id, False)
                return

            # Create a minimal plug-like object for the service
            class PlugInfo:
                def __init__(self):
                    self.plug_type = plug_type
                    self.ip_address = ip_address
                    self.ha_entity_id = ha_entity_id
                    self.username = username
                    self.password = password
                    self.name = f"plug_{plug_id}"
                    # REST fields
                    self.rest_off_url = rest_off_url
                    self.rest_off_body = rest_off_body
                    self.rest_method = rest_method
                    self.rest_headers = rest_headers
                    self.controls_printer_power = controls_printer_power

            plug_info = PlugInfo()
            service = await self.get_service_for_plug(plug_info)
            success = await service.turn_off(plug_info)
            logger.info("Turned off plug %s after time delay", plug_id)

            # Mark auto_off_executed in database and update printer status
            if success:
                await self._mark_auto_off_executed(plug_id)
                await publish_plug_state(
                    plug_id, plug_name or f"plug_{plug_id}", printer_id, on=False, ha_entity_id=ha_entity_id
                )
                # Mark the printer as offline immediately — but only when this
                # plug actually feeds the printer (#2629).
                if controls_printer_power:
                    printer_manager.mark_printer_offline(printer_id)
                await run_linked_scripts_after_automatic_switch(printer_id, plug_info, "OFF")

        except asyncio.CancelledError:
            logger.debug("Delayed turn-off cancelled for plug %s", plug_id)
        finally:
            self._pending_off.pop(plug_id, None)

    def _schedule_temp_based_off(self, plug: "SmartPlug", printer_id: int, temp_threshold: int):
        """Monitor temperature and turn off when below threshold."""
        # Cancel any existing task for this plug
        self._cancel_pending_off(plug.id)

        logger.info("Scheduling temperature-based turn-off for plug '%s' (threshold: %s°C)", plug.name, temp_threshold)

        # Mark as pending in database (survives restarts)
        spawn_background_task(self._mark_auto_off_pending(plug.id, True), name=f"plug-auto-off-pending-{plug.id}")

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
                controls_printer_power=plug.controls_printer_power,
                plug_name=plug.name,
                rest_off_url=plug.rest_off_url if plug.plug_type == "rest" else None,
                rest_off_body=plug.rest_off_body if plug.plug_type == "rest" else None,
                rest_method=plug.rest_method if plug.plug_type == "rest" else None,
                rest_headers=plug.rest_headers if plug.plug_type == "rest" else None,
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
        *,
        controls_printer_power: bool = True,
        plug_name: str = "",
        rest_off_url: str | None = None,
        rest_off_body: str | None = None,
        rest_method: str | None = None,
        rest_headers: str | None = None,
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
                        logger.info(
                            "Temp check plug %s: nozzle=%s°C, threshold=%s°C", plug_id, nozzle_temp, temp_threshold
                        )

                    if max_nozzle_temp < temp_threshold:
                        # #1890: the nozzle can dip below the threshold between
                        # a finished print and a fresh one starting (e.g. a
                        # touchscreen reprint during the PREPARE/heating phase).
                        # Guard the turn-off so we never cut power on a loaded
                        # print; keep polling until it's genuinely idle again.
                        if printer_manager.is_print_active(printer_id):
                            logger.info(
                                "Deferring temp-based auto-off for plug %s: printer %s is printing again (state=%s)",
                                plug_id,
                                printer_id,
                                getattr(printer_manager.get_status(printer_id), "state", "unknown"),
                            )
                            await asyncio.sleep(check_interval)
                            elapsed += check_interval
                            continue

                        # All nozzles are below threshold, turn off
                        class PlugInfo:
                            def __init__(self):
                                self.plug_type = plug_type
                                self.ip_address = ip_address
                                self.ha_entity_id = ha_entity_id
                                self.username = username
                                self.password = password
                                self.name = f"plug_{plug_id}"
                                # REST fields
                                self.rest_off_url = rest_off_url
                                self.rest_off_body = rest_off_body
                                self.rest_method = rest_method
                                self.rest_headers = rest_headers
                                self.controls_printer_power = controls_printer_power

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
                            await publish_plug_state(
                                plug_id, plug_name or f"plug_{plug_id}", printer_id, on=False, ha_entity_id=ha_entity_id
                            )
                            # Mark the printer as offline immediately — but only
                            # when this plug actually feeds the printer (#2629).
                            if controls_printer_power:
                                printer_manager.mark_printer_offline(printer_id)
                            await run_linked_scripts_after_automatic_switch(printer_id, plug_info, "OFF")

                        break

                await asyncio.sleep(check_interval)
                elapsed += check_interval

            if elapsed >= max_wait:
                logger.warning("Temperature-based turn-off timed out for plug %s after %ss", plug_id, max_wait)

        except asyncio.CancelledError:
            logger.debug("Temperature-based turn-off cancelled for plug %s", plug_id)
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
                    plug.auto_off_pending_since = utcnow_naive() if pending else None
                    await db.commit()
                    logger.debug("Marked plug %s auto_off_pending=%s", plug_id, pending)
        except Exception as e:
            logger.warning("Failed to update plug %s pending state: %s", plug_id, e)

    async def _mark_auto_off_executed(self, plug_id: int):
        """Disable auto-off after it was executed (one-shot behavior unless persistent)."""
        try:
            from backend.app.core.database import async_session
            from backend.app.models.smart_plug import SmartPlug

            async with async_session() as db:
                result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
                plug = result.scalar_one_or_none()
                if plug:
                    if not plug.auto_off_persistent:
                        plug.auto_off = False  # Disable auto-off (one-shot behavior)
                    plug.auto_off_executed = False  # Reset the flag
                    plug.auto_off_pending = False  # Clear pending state
                    plug.auto_off_pending_since = None
                    plug.last_state = "OFF"
                    plug.last_checked = utcnow_naive()
                    await db.commit()
                    if plug.auto_off_persistent:
                        logger.info("Auto-off executed for plug %s (persistent, stays enabled)", plug_id)
                    else:
                        logger.info("Auto-off executed and disabled for plug %s", plug_id)
        except Exception as e:
            logger.warning("Failed to update plug %s after auto-off: %s", plug_id, e)

    def _cancel_pending_off(self, plug_id: int):
        """Cancel any pending off task for this plug."""
        if plug_id in self._pending_off:
            logger.debug("Cancelling pending turn-off for plug %s", plug_id)
            self._pending_off[plug_id].cancel()
            del self._pending_off[plug_id]
            # Clear pending state in database
            spawn_background_task(self._mark_auto_off_pending(plug_id, False), name=f"plug-auto-off-pending-{plug_id}")

    def forget_plug(self, plug_id: int) -> None:
        """Cancel a deleted plug's pending turn-off.

        The task holds the plug's address and printer id, so it would still switch
        the outlet, and on SQLite read the next printer given the id. Nothing is
        written: the row is gone, and SQLite gives its id to the next plug.
        """
        task = self._pending_off.pop(plug_id, None)
        if task is not None:
            task.cancel()

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
                        pending_since = to_naive_utc(plug.auto_off_pending_since)
                        elapsed = (utcnow_naive() - pending_since).total_seconds()
                        if elapsed > 7200:  # 2 hours
                            logger.warning(
                                f"Auto-off for plug '{plug.name}' was pending for {elapsed / 60:.0f} minutes, "
                                f"clearing stale pending state"
                            )
                            plug.auto_off_pending = False
                            plug.auto_off_pending_since = None
                            await db.commit()
                            continue

                    logger.info("Resuming pending auto-off for plug '%s' (printer %s)", plug.name, plug.printer_id)

                    # #1890: never resume a power-off onto a live print. If the
                    # printer started a new print during the downtime, the stale
                    # pending off must be dropped, not executed — same guard the
                    # live off-executors use.
                    if printer_manager.is_print_active(plug.printer_id):
                        logger.info(
                            "Not resuming auto-off for plug '%s': printer %s is printing (state=%s); clearing pending",
                            plug.name,
                            plug.printer_id,
                            getattr(printer_manager.get_status(plug.printer_id), "state", "unknown"),
                        )
                        plug.auto_off_pending = False
                        plug.auto_off_pending_since = None
                        await db.commit()
                        continue

                    # Resume the appropriate off mode
                    if plug.off_delay_mode == "temperature":
                        self._schedule_temp_based_off(plug, plug.printer_id, plug.off_temp_threshold)
                    else:
                        # For time mode, just turn off immediately since delay already passed
                        logger.info("Time-based auto-off was pending, turning off plug '%s' now", plug.name)

                        service = await self.get_service_for_plug(plug, db)
                        success = await service.turn_off(plug)
                        if success:
                            await self._mark_auto_off_executed(plug.id)
                            await publish_plug_state(
                                plug.id, plug.name, plug.printer_id, on=False, ha_entity_id=plug.ha_entity_id
                            )
                            if plug.controls_printer_power:
                                printer_manager.mark_printer_offline(plug.printer_id)
                            await run_linked_scripts_after_automatic_switch(plug.printer_id, plug, "OFF")

                if pending_plugs:
                    logger.info("Resumed %s pending auto-off(s)", len(pending_plugs))

        except Exception as e:
            logger.warning("Failed to resume pending auto-offs: %s", e)


# Global singleton
smart_plug_manager = SmartPlugManager()
