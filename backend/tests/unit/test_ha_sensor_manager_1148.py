"""Unit tests for Home Assistant sensors bound to a printer (#1148, #448).

The alert rules decide three separate things — the pill colour on the card, a
notification, and whether the queue holds — so they are tested directly rather
than through any one of those consumers.

The recurring theme is that "we could not read it" must never be mistaken for
a reading. A door contact whose integration has dropped out reports
"unavailable", not "closed", and treating that as closed would let a print
start into an open enclosure; treating it as *open* would strand the queue.
Neither: it is not a reading at all.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.ha_sensor_manager import (
    HASensorManager,
    SensorReading,
    describe_state,
    evaluate,
)


def _sensor(**overrides):
    """A sensor row as the poller sees it, without touching the DB."""
    base = {
        "id": 1,
        "printer_id": 4,
        "name": "Enclosure Door",
        "entity_id": "binary_sensor.enclosure_door",
        "kind": "binary",
        "device_class": "door",
        "unit": None,
        "alert_state": "on",
        "alert_above": None,
        "alert_below": None,
        "block_print": False,
        "notify_on_alert": False,
        "last_state": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _numeric(**overrides):
    base = {
        "entity_id": "sensor.enclosure_temp",
        "kind": "numeric",
        "device_class": "temperature",
        "unit": "\u00b0C",
        "alert_state": None,
        "name": "Enclosure Temp",
    }
    base.update(overrides)
    return _sensor(**base)


class TestEvaluateBinary:
    def test_alerts_in_the_configured_state(self):
        reading = evaluate(_sensor(), {"state": "on"})

        assert reading == SensorReading(state="on", value=None, alerting=True, reachable=True)

    def test_quiet_in_the_other_state(self):
        assert evaluate(_sensor(), {"state": "off"}).alerting is False

    def test_alert_state_off_inverts_the_rule(self):
        """A "fan running" contact alarms when it stops, not when it starts."""
        sensor = _sensor(alert_state="off", name="Exhaust Fan")

        assert evaluate(sensor, {"state": "off"}).alerting is True
        assert evaluate(sensor, {"state": "on"}).alerting is False

    def test_no_alert_state_never_alerts(self):
        """Display-only sensors are the default — they just show a state."""
        sensor = _sensor(alert_state=None)

        assert evaluate(sensor, {"state": "on"}).alerting is False
        assert evaluate(sensor, {"state": "on"}).reachable is True

    def test_state_is_normalised_to_lower_case(self):
        """Some integrations report "ON"; the alert rule stores "on"."""
        assert evaluate(_sensor(), {"state": "ON"}).state == "on"
        assert evaluate(_sensor(), {"state": "ON"}).alerting is True


class TestEvaluateNumeric:
    def test_above_threshold_alerts(self):
        assert evaluate(_numeric(alert_above=35), {"state": "41.2"}).alerting is True

    def test_below_threshold_alerts(self):
        assert evaluate(_numeric(alert_below=15), {"state": "12"}).alerting is True

    def test_inside_the_band_is_quiet(self):
        reading = evaluate(_numeric(alert_above=35, alert_below=15), {"state": "22.5"})

        assert reading.alerting is False
        assert reading.value == 22.5

    def test_exactly_on_the_threshold_is_not_an_alert(self):
        """Strict comparison, so a 35 °C limit does not alarm at exactly 35."""
        assert evaluate(_numeric(alert_above=35), {"state": "35"}).alerting is False

    def test_a_sensor_that_stops_reporting_numbers_does_not_alert(self):
        """Reachable, but no value to compare — so no verdict either way."""
        reading = evaluate(_numeric(alert_above=35), {"state": "calibrating"})

        assert reading.reachable is True
        assert reading.value is None
        assert reading.alerting is False


class TestUnreadable:
    @pytest.mark.parametrize("state", ["unavailable", "unknown", None])
    def test_ha_non_states_are_not_readings(self, state):
        reading = evaluate(_sensor(), {"state": state})

        assert reading.reachable is False
        assert reading.alerting is False
        assert reading.state is None

    def test_a_failed_fetch_is_not_a_reading(self):
        """fetch_states maps an entity it could not read to None."""
        reading = evaluate(_sensor(), None)

        assert reading == SensorReading(state=None, value=None, alerting=False, reachable=False)


class TestDescribeState:
    def test_binary_uses_the_raw_state(self):
        assert describe_state(_sensor(), evaluate(_sensor(), {"state": "on"})) == "on"

    def test_numeric_carries_its_unit(self):
        sensor = _numeric()

        assert describe_state(sensor, evaluate(sensor, {"state": "41.20"})) == "41.2 °C"

    def test_numeric_without_a_unit_is_bare(self):
        sensor = _numeric(unit=None)

        assert describe_state(sensor, evaluate(sensor, {"state": "7"})) == "7"


class TestBlockedPrinters:
    """The interlock only ever reports a positive, current finding."""

    def _manager_with(self, sensors, readings):
        manager = HASensorManager()
        manager._readings = readings
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: sensors))
        return manager, db

    @pytest.mark.asyncio
    async def test_reports_an_alerting_blocking_sensor(self):
        sensor = _sensor(block_print=True)
        manager, db = self._manager_with([sensor], {1: SensorReading("on", None, True, True)})

        assert await manager.blocked_printers(db) == {4: "Enclosure Door"}

    @pytest.mark.asyncio
    async def test_silent_when_not_alerting(self):
        sensor = _sensor(block_print=True)
        manager, db = self._manager_with([sensor], {1: SensorReading("off", None, False, True)})

        assert await manager.blocked_printers(db) == {}

    @pytest.mark.asyncio
    async def test_silent_when_home_assistant_is_unreachable(self):
        """The queue must keep running when HA is down, not seize up."""
        sensor = _sensor(block_print=True)
        manager, db = self._manager_with([sensor], {1: SensorReading(None, None, False, False)})

        assert await manager.blocked_printers(db) == {}

    @pytest.mark.asyncio
    async def test_silent_before_the_first_poll(self):
        """A cold cache is not evidence the door is open."""
        sensor = _sensor(block_print=True)
        manager, db = self._manager_with([sensor], {})

        assert await manager.blocked_printers(db) == {}

    @pytest.mark.asyncio
    async def test_names_every_blocking_sensor_on_a_printer(self):
        sensors = [
            _sensor(id=1, block_print=True, name="Front Door"),
            _sensor(id=2, block_print=True, name="Side Panel"),
        ]
        manager, db = self._manager_with(
            sensors,
            {
                1: SensorReading("on", None, True, True),
                2: SensorReading("on", None, True, True),
            },
        )

        assert await manager.blocked_printers(db) == {4: "Front Door, Side Panel"}


class TestNotificationEdge:
    """Alerts fire on the transition into the alert state, not while it lasts."""

    async def _apply(self, manager, sensor, states, notify):
        db = AsyncMock()
        db.get.return_value = SimpleNamespace(name="X1C-1")
        with patch("backend.app.services.notification_service.notification_service", notify):
            await manager._apply(db, [sensor], states)

    @pytest.mark.asyncio
    async def test_fires_once_on_the_way_in(self):
        manager = HASensorManager()
        sensor = _sensor(notify_on_alert=True)
        notify = AsyncMock()

        # First poll seeds the cache; a door already open at startup has not
        # just been opened.
        await self._apply(manager, sensor, {sensor.entity_id: {"state": "off"}}, notify)
        assert notify.on_ha_sensor_alert.await_count == 0

        await self._apply(manager, sensor, {sensor.entity_id: {"state": "on"}}, notify)
        assert notify.on_ha_sensor_alert.await_count == 1

        # Still open on the next pass — no second alert.
        await self._apply(manager, sensor, {sensor.entity_id: {"state": "on"}}, notify)
        assert notify.on_ha_sensor_alert.await_count == 1

    @pytest.mark.asyncio
    async def test_silent_on_the_first_poll_after_a_restart(self):
        """Cold cache. Re-announcing every pre-existing alert on every restart
        is how users learn to ignore the alert."""
        manager = HASensorManager()
        sensor = _sensor(notify_on_alert=True)
        notify = AsyncMock()

        await self._apply(manager, sensor, {sensor.entity_id: {"state": "on"}}, notify)

        assert notify.on_ha_sensor_alert.await_count == 0
        assert manager.get_reading(sensor.id).alerting is True

    @pytest.mark.asyncio
    async def test_silent_when_the_sensor_opts_out(self):
        manager = HASensorManager()
        sensor = _sensor(notify_on_alert=False)
        notify = AsyncMock()

        await self._apply(manager, sensor, {sensor.entity_id: {"state": "off"}}, notify)
        await self._apply(manager, sensor, {sensor.entity_id: {"state": "on"}}, notify)

        assert notify.on_ha_sensor_alert.await_count == 0

    @pytest.mark.asyncio
    async def test_re_arms_after_the_alert_clears(self):
        manager = HASensorManager()
        sensor = _sensor(notify_on_alert=True)
        notify = AsyncMock()

        for state in ("off", "on", "off", "on"):
            await self._apply(manager, sensor, {sensor.entity_id: {"state": state}}, notify)

        assert notify.on_ha_sensor_alert.await_count == 2

    @pytest.mark.asyncio
    async def test_a_dropout_does_not_count_as_the_alert_clearing(self):
        """on -> unavailable -> on is one continuous alert, not two.

        Without this, a flaky Zigbee contact would notify on every reconnect.
        """
        manager = HASensorManager()
        sensor = _sensor(notify_on_alert=True)
        notify = AsyncMock()

        await self._apply(manager, sensor, {sensor.entity_id: {"state": "off"}}, notify)
        await self._apply(manager, sensor, {sensor.entity_id: {"state": "on"}}, notify)
        await self._apply(manager, sensor, {sensor.entity_id: None}, notify)
        await self._apply(manager, sensor, {sensor.entity_id: {"state": "on"}}, notify)

        assert notify.on_ha_sensor_alert.await_count == 1
