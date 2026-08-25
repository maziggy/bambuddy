import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.location import Location
from backend.app.models.location_ha_sensor import LAST_STATE_MAX_LENGTH, LocationHASensor
from backend.app.models.settings import Settings
from backend.app.services.ha_sensor_manager import SensorReading, describe_state, evaluate, persistable_state
from backend.app.services.homeassistant import homeassistant_service
from backend.app.utils.local_time import utcnow_naive

logger = logging.getLogger(__name__)

POLL_INTERVAL = 120
MIN_POLL_INTERVAL = 60


class LocationHASensorManager:
    def __init__(self):
        self._task: asyncio.Task | None = None
        # sensor id -> last reading. Sensors absent from this map have not been
        # polled yet; callers must not read that as "not alerting" without also
        # checking, which is why get_reading returns None rather than a default.
        self._readings: dict[int, SensorReading] = {}
        # sensor id -> alerting, from the last reading we could actually take.
        # Kept apart from _readings because a dropout must not read as the
        # alert clearing: on -> unavailable -> on is one continuous alert, and
        # notifying off _readings alone would ping the user on every reconnect
        # of a flaky sensor. Absent means "never had a reachable reading".
        self._last_alerting: dict[int, bool] = {}

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._poll_loop())
            logger.info("Home Assistant location-sensor poller started")

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("Home Assistant location-sensor poller stopped")

    def get_reading(self, sensor_id: int) -> SensorReading | None:
        return self._readings.get(sensor_id)

    def forget(self, sensor_id: int):
        """Drop a deleted sensor's cached reading so its id cannot be reused
        by a later row and answer with the old sensor's state."""
        self._readings.pop(sensor_id, None)
        self._last_alerting.pop(sensor_id, None)

    async def _poll_loop(self):
        # Poll first, sleep after — the interval is configurable and can be
        # minutes long, and a restart should not leave every location's
        # reading blank on the card for a full interval before the first one
        # lands.
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Home Assistant location-sensor poll failed: %s", e)
            try:
                await asyncio.sleep(await self._get_poll_interval())
            except asyncio.CancelledError:
                break
            except Exception as e:
                # _get_poll_interval() reads Settings, so this leg does I/O
                # and a transient database failure (pool exhaustion, a
                # restarting server) can raise here. Letting it escape ends
                # the task for good: stop() is what clears self._task, so a
                # loop that died on its own leaves it set and start() will
                # not revive it — location sensors would stay frozen until
                # the process restarts. The poll_once() call above already
                # survives the same error one line earlier.
                logger.warning("Home Assistant location-sensor poll interval lookup failed: %s", e)
                await asyncio.sleep(POLL_INTERVAL)

    async def _get_poll_interval(self) -> int:
        """User-configurable poll cadence, clamped to a sane floor.

        Falls back to the default on a missing row or a corrupted value
        rather than raising — a bad setting must not take the poller down.
        """
        from backend.app.core.database import async_session

        async with async_session() as db:
            result = await db.execute(select(Settings).where(Settings.key == "location_sensor_poll_interval"))
            row = result.scalar_one_or_none()
        if row is None:
            return POLL_INTERVAL
        try:
            return max(MIN_POLL_INTERVAL, int(row.value))
        except (TypeError, ValueError):
            return POLL_INTERVAL

    async def poll_once(self):
        """One pass over every configured sensor."""
        from backend.app.core.database import async_session

        async with async_session() as db:
            result = await db.execute(select(LocationHASensor))
            sensors = list(result.scalars().all())

            # Drop readings for rows that no longer exist. The delete route
            # calls forget(), but a location deleted with sensors attached
            # takes them out by cascade, and a restored backup can renumber
            # them — either way a stale id must not answer for a later sensor.
            live = {s.id for s in sensors}
            for stale in set(self._readings) - live:
                self.forget(stale)

            if not sensors:
                return

            if not await self._configure(db):
                for sensor in sensors:
                    self._readings[sensor.id] = SensorReading(None, None, False, False)
                return

            states = await homeassistant_service.fetch_states(sorted({s.entity_id for s in sensors}))
            await self._apply(db, sensors, states)

    async def refresh_one(self, db: AsyncSession, sensor: LocationHASensor):
        """Read a single sensor now, on the caller's session.

        Used after a create or an edit so the card shows a state straight away
        instead of blank until the next tick. Deliberately not a full
        ``poll_once``: a request handler must not wait on every configured
        entity, and must not fire another user's notification as a side effect
        of this one saving a form.
        """
        self.forget(sensor.id)
        if not await self._configure(db):
            self._readings[sensor.id] = SensorReading(None, None, False, False)
            return

        states = await homeassistant_service.fetch_states([sensor.entity_id])
        reading = evaluate(sensor, states.get(sensor.entity_id))
        self._readings[sensor.id] = reading
        if reading.reachable:
            self._last_alerting[sensor.id] = reading.alerting

        sensor.last_checked = utcnow_naive()
        persisted = persistable_state(reading.state, LAST_STATE_MAX_LENGTH)
        if reading.reachable and sensor.last_state != persisted:
            sensor.last_state = persisted
            sensor.last_changed = sensor.last_checked
        await db.commit()
        await db.refresh(sensor)

    async def _configure(self, db: AsyncSession) -> bool:
        from backend.app.api.routes.settings import get_homeassistant_settings

        try:
            ha_settings = await get_homeassistant_settings(db)
        except Exception as e:
            logger.warning("Failed to read Home Assistant settings: %s", e)
            return False
        if not ha_settings["ha_url"] or not ha_settings["ha_token"]:
            return False
        homeassistant_service.configure(ha_settings["ha_url"], ha_settings["ha_token"])
        return True

    async def _apply(self, db: AsyncSession, sensors: list[LocationHASensor], states: dict[str, dict | None]):
        """Fold poll results into the cache, the DB and any notifications."""
        from backend.app.services.notification_service import notification_service

        now = utcnow_naive()
        alerts: list[tuple[LocationHASensor, SensorReading]] = []

        for sensor in sensors:
            payload = states.get(sensor.entity_id)
            reading = evaluate(sensor, payload)
            was_alerting = self._last_alerting.get(sensor.id)
            self._readings[sensor.id] = reading

            sensor.last_checked = now
            if reading.reachable:
                persisted = persistable_state(reading.state, LAST_STATE_MAX_LENGTH)
                if sensor.last_state != persisted:
                    sensor.last_state = persisted
                    sensor.last_changed = now

            # Notify on the edge into alerting only. `was_alerting is None` is
            # a cold cache (first poll after a restart) — a drybox that was
            # already too humid then has not just become too humid, and
            # re-announcing it on every restart would train users to ignore
            # the alert.
            if sensor.notify_on_alert and reading.reachable and reading.alerting and was_alerting is False:
                alerts.append((sensor, reading))

            if reading.reachable:
                self._last_alerting[sensor.id] = reading.alerting

        await db.commit()

        for sensor, reading in alerts:
            # db.get, not sensor.location: touching the lazy relationship from
            # an async session raises MissingGreenlet.
            location = await db.get(Location, sensor.location_id)
            try:
                await notification_service.on_location_ha_sensor_alert(
                    location_name=location.name if location else "Unknown",
                    sensor_name=sensor.name,
                    state=describe_state(sensor, reading),
                    db=db,
                )
            except Exception as e:
                logger.warning("Failed to send HA sensor alert for '%s': %s", sensor.name, e)


location_ha_sensor_manager = LocationHASensorManager()
