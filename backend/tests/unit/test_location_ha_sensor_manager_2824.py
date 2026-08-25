import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.location_ha_sensor_manager import LocationHASensorManager


def _sensor(**overrides):
    base = {
        "id": 1,
        "location_id": 7,
        "name": "Drybox Humidity",
        "entity_id": "sensor.drybox_humidity",
        "kind": "numeric",
        "device_class": "humidity",
        "unit": "%",
        "alert_state": None,
        "alert_above": 60,
        "alert_below": None,
        "notify_on_alert": False,
        "last_state": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestNotificationEdge:
    async def _apply(self, manager, sensor, states, notify):
        db = AsyncMock()
        db.get.return_value = SimpleNamespace(name="Drybox 1")
        with patch("backend.app.services.notification_service.notification_service", notify):
            await manager._apply(db, [sensor], states)

    async def test_fires_once_on_the_way_in(self):
        manager = LocationHASensorManager()
        sensor = _sensor(notify_on_alert=True)
        notify = AsyncMock()

        await self._apply(manager, sensor, {sensor.entity_id: {"state": "40"}}, notify)
        assert notify.on_location_ha_sensor_alert.await_count == 0

        await self._apply(manager, sensor, {sensor.entity_id: {"state": "65"}}, notify)
        assert notify.on_location_ha_sensor_alert.await_count == 1

        await self._apply(manager, sensor, {sensor.entity_id: {"state": "66"}}, notify)
        assert notify.on_location_ha_sensor_alert.await_count == 1

    async def test_silent_on_the_first_poll_after_a_restart(self):
        manager = LocationHASensorManager()
        sensor = _sensor(notify_on_alert=True)
        notify = AsyncMock()

        await self._apply(manager, sensor, {sensor.entity_id: {"state": "65"}}, notify)

        assert notify.on_location_ha_sensor_alert.await_count == 0
        assert manager.get_reading(sensor.id).alerting is True

    async def test_silent_when_the_sensor_opts_out(self):
        manager = LocationHASensorManager()
        sensor = _sensor(notify_on_alert=False)
        notify = AsyncMock()

        await self._apply(manager, sensor, {sensor.entity_id: {"state": "40"}}, notify)
        await self._apply(manager, sensor, {sensor.entity_id: {"state": "65"}}, notify)

        assert notify.on_location_ha_sensor_alert.await_count == 0

    async def test_passes_the_location_name_not_a_printer_name(self):
        manager = LocationHASensorManager()
        sensor = _sensor(notify_on_alert=True)
        notify = AsyncMock()

        await self._apply(manager, sensor, {sensor.entity_id: {"state": "40"}}, notify)
        await self._apply(manager, sensor, {sensor.entity_id: {"state": "65"}}, notify)

        notify.on_location_ha_sensor_alert.assert_awaited_once()
        _, kwargs = notify.on_location_ha_sensor_alert.await_args
        assert kwargs["location_name"] == "Drybox 1"
        assert kwargs["sensor_name"] == "Drybox Humidity"

    async def test_a_dropout_does_not_count_as_the_alert_clearing(self):
        manager = LocationHASensorManager()
        sensor = _sensor(notify_on_alert=True)
        notify = AsyncMock()

        await self._apply(manager, sensor, {sensor.entity_id: {"state": "40"}}, notify)
        await self._apply(manager, sensor, {sensor.entity_id: {"state": "65"}}, notify)
        await self._apply(manager, sensor, {sensor.entity_id: None}, notify)
        await self._apply(manager, sensor, {sensor.entity_id: {"state": "66"}}, notify)

        assert notify.on_location_ha_sensor_alert.await_count == 1


class TestLastStatePersistence:
    """What goes into last_state must fit its String(64) column.

    A numeric entity can start reporting free text longer than the column.
    SQLite stores it anyway, but PostgreSQL rejects the row — and since
    _apply commits the whole pass at once, one such sensor would sink every
    sensor's update on every tick.
    """

    async def _apply(self, manager, sensor, state):
        db = AsyncMock()
        with patch("backend.app.services.notification_service.notification_service", AsyncMock()):
            await manager._apply(db, [sensor], {sensor.entity_id: {"state": state}})

    async def test_a_long_text_state_is_cut_to_the_column_width(self):
        manager = LocationHASensorManager()
        sensor = _sensor(last_changed=None, last_checked=None)
        long_state = "x" * 500

        await self._apply(manager, sensor, long_state)

        assert sensor.last_state == "x" * 64
        # The cache keeps the full state — only what is persisted is cut.
        assert manager.get_reading(sensor.id).state == long_state

    async def test_an_unchanged_long_state_is_not_a_change_on_every_poll(self):
        manager = LocationHASensorManager()
        sensor = _sensor(last_changed=None, last_checked=None)
        long_state = "x" * 500

        await self._apply(manager, sensor, long_state)
        first_changed = sensor.last_changed
        await self._apply(manager, sensor, long_state)

        # Comparing the stored (cut) value against the raw state would see a
        # difference on every poll and churn last_changed forever.
        assert sensor.last_changed == first_changed


class TestPollLoopSurvival:
    """The loop must outlive a transient database error (#2824 review).

    _get_poll_interval() reads Settings, so the sleep leg of _poll_loop does
    I/O and can raise on pool exhaustion or a restarting database. Letting
    that escape ends the task permanently: stop() is what clears _task, so a
    self-terminated loop leaves it set and start() refuses to restart it.
    """

    async def test_survives_a_failing_poll_interval_lookup(self):
        manager = LocationHASensorManager()
        polls = 0

        async def counting_poll():
            nonlocal polls
            polls += 1
            if polls >= 2:
                raise asyncio.CancelledError  # end the loop once we've proven it came back

        async def failing_interval():
            raise RuntimeError("QueuePool limit reached")

        with (
            patch.object(manager, "poll_once", counting_poll),
            patch.object(manager, "_get_poll_interval", failing_interval),
            patch("backend.app.services.location_ha_sensor_manager.POLL_INTERVAL", 0),
        ):
            await manager._poll_loop()

        # Without the guard the first lookup failure escapes _poll_loop and
        # poll_once never runs a second time.
        assert polls == 2

    async def test_a_cancel_during_the_fallback_sleep_still_stops_the_loop(self):
        manager = LocationHASensorManager()

        async def failing_interval():
            raise RuntimeError("database is locked")

        with (
            patch.object(manager, "poll_once", AsyncMock()),
            patch.object(manager, "_get_poll_interval", failing_interval),
            patch("backend.app.services.location_ha_sensor_manager.POLL_INTERVAL", 3600),
        ):
            task = asyncio.create_task(manager._poll_loop())
            await asyncio.sleep(0)  # let it reach the fallback sleep
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
