"""Power alerts: checked in the background, sent once per crossing.

Pre-fix the check called a ``notification_service.send_notification`` that does
not exist, so no power alert was ever sent and the plug status request failed
with 500 once a threshold was crossed. It also only ran from that status
request, i.e. while someone had the printer page open, and it fired every five
minutes for as long as the power stayed outside the range.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.smart_plug import SmartPlug
from backend.app.services.power_alerts import PowerAlertMonitor, check_all_plugs
from backend.app.utils.local_time import utcnow_naive


async def _plug(db_session, **kwargs) -> SmartPlug:
    fields = {
        "name": "Printer plug",
        "ip_address": "192.168.1.50",
        "power_alert_enabled": True,
        "power_alert_high": 200.0,
        "power_alert_low": None,
    }
    fields.update(kwargs)
    plug = SmartPlug(**fields)
    db_session.add(plug)
    await db_session.commit()
    return plug


@pytest.fixture
def sent():
    with patch(
        "backend.app.services.power_alerts.notification_service.on_printer_error", new_callable=AsyncMock
    ) as on_printer_error:
        yield on_printer_error


class TestCrossings:
    @pytest.mark.asyncio
    async def test_alerts_once_when_power_rises_above_the_high_value(self, db_session, sent):
        plug = await _plug(db_session)
        monitor = PowerAlertMonitor()

        for watts in (150.0, 250.0, 260.0, 255.0):
            await monitor.evaluate(plug, "ON", watts, db_session)

        sent.assert_awaited_once()
        kwargs = sent.await_args.kwargs
        assert kwargs["printer_name"] == "Printer plug"
        assert kwargs["error_type"] == "Power High"
        assert "250.0W" in kwargs["error_detail"] and "200.0W" in kwargs["error_detail"]
        assert plug.power_alert_last_triggered is not None

    @pytest.mark.asyncio
    async def test_alerts_again_after_the_power_was_back_in_range(self, db_session, sent):
        plug = await _plug(db_session)
        monitor = PowerAlertMonitor()

        await monitor.evaluate(plug, "ON", 250.0, db_session)
        await monitor.evaluate(plug, "ON", 150.0, db_session)
        plug.power_alert_last_triggered = utcnow_naive() - timedelta(minutes=10)
        await monitor.evaluate(plug, "ON", 250.0, db_session)

        assert sent.await_count == 2

    @pytest.mark.asyncio
    async def test_alerts_when_power_falls_below_the_low_value(self, db_session, sent):
        plug = await _plug(db_session, power_alert_high=None, power_alert_low=20.0)
        monitor = PowerAlertMonitor()

        await monitor.evaluate(plug, "ON", 80.0, db_session)
        await monitor.evaluate(plug, "ON", 5.0, db_session)

        sent.assert_awaited_once()
        assert sent.await_args.kwargs["error_type"] == "Power Low"

    @pytest.mark.asyncio
    async def test_a_switched_off_plug_raises_no_low_alert(self, db_session, sent):
        plug = await _plug(db_session, power_alert_high=None, power_alert_low=20.0)
        monitor = PowerAlertMonitor()

        await monitor.evaluate(plug, "ON", 80.0, db_session)
        await monitor.evaluate(plug, "OFF", 0.0, db_session)

        sent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_crossing_inside_the_cooldown_is_sent_once_it_ends(self, db_session, sent):
        plug = await _plug(db_session, power_alert_last_triggered=utcnow_naive())
        monitor = PowerAlertMonitor()

        await monitor.evaluate(plug, "ON", 250.0, db_session)
        sent.assert_not_awaited()

        plug.power_alert_last_triggered = utcnow_naive() - timedelta(minutes=10)
        await monitor.evaluate(plug, "ON", 250.0, db_session)
        sent.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disabled_alerts_send_nothing(self, db_session, sent):
        plug = await _plug(db_session, power_alert_enabled=False)

        await PowerAlertMonitor().evaluate(plug, "ON", 999.0, db_session)

        sent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_linked_printer_is_passed_for_the_provider_filter(self, db_session, sent, printer_factory):
        printer = await printer_factory()
        plug = await _plug(db_session, printer_id=printer.id)

        await PowerAlertMonitor().evaluate(plug, "ON", 250.0, db_session)

        assert sent.await_args.kwargs["printer_id"] == printer.id


class TestBackgroundPass:
    @pytest.mark.asyncio
    async def test_reads_only_plugs_with_alerts_on_and_survives_an_unreachable_one(self, db_session, sent):
        down = await _plug(db_session, name="Down")
        up = await _plug(db_session, name="Up", ip_address="192.168.1.51")
        await _plug(db_session, name="Off", ip_address="192.168.1.52", power_alert_enabled=False)
        down_id, up_id = down.id, up.id

        read_names = []

        async def read(plug, db):
            read_names.append(plug.name)
            if plug.id == down_id:
                raise TimeoutError("unreachable")
            return "ON", 250.0

        with patch("backend.app.services.power_alerts.read_plug", side_effect=read):
            await check_all_plugs(db_session, PowerAlertMonitor())

        assert sorted(read_names) == ["Down", "Up"]
        sent.assert_awaited_once()
        assert sent.await_args.kwargs["printer_name"] == "Up"
        refreshed = (await db_session.execute(select(SmartPlug).where(SmartPlug.id == up_id))).scalar_one()
        assert refreshed.power_alert_last_triggered is not None


class TestStatusRouteNoLongerChecks:
    @pytest.mark.asyncio
    async def test_status_answers_with_a_plug_above_its_threshold(self, async_client, db_session, sent):
        plug = await _plug(db_session)
        with (
            patch(
                "backend.app.api.routes.smart_plugs.tasmota_service.get_status",
                AsyncMock(return_value={"state": "ON", "reachable": True, "device_name": None}),
            ),
            patch(
                "backend.app.api.routes.smart_plugs.tasmota_service.get_energy",
                AsyncMock(return_value={"power": 250.0, "today": 1.0, "total": 5.0}),
            ),
        ):
            response = await async_client.get(f"/api/v1/smart-plugs/{plug.id}/status")

        assert response.status_code == 200, response.text
        assert response.json()["energy"]["power"] == 250.0
        sent.assert_not_awaited()


class TestReadPlug:
    """State and power for every plug type; an unreachable plug reads as nothing."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("plug_type", ["tasmota", "rest", "homeassistant"])
    async def test_polled_plugs(self, db_session, plug_type):
        from backend.app.services import plug_energy

        plug = await _plug(db_session, plug_type=plug_type)
        service = {
            "tasmota": plug_energy.tasmota_service,
            "rest": plug_energy.rest_smart_plug_service,
            "homeassistant": plug_energy.homeassistant_service,
        }[plug_type]
        with (
            patch.object(service, "get_status", AsyncMock(return_value={"state": "ON", "reachable": True})),
            patch.object(service, "get_energy", AsyncMock(return_value={"power": 42.5})),
            patch(
                "backend.app.api.routes.settings.get_homeassistant_settings",
                AsyncMock(return_value={"ha_url": "http://ha", "ha_token": "t"}),
            ),
        ):
            assert await plug_energy.read_plug(plug, db_session) == ("ON", 42.5)

        with (
            patch.object(service, "get_status", AsyncMock(return_value={"state": None, "reachable": False})),
            patch(
                "backend.app.api.routes.settings.get_homeassistant_settings",
                AsyncMock(return_value={"ha_url": "http://ha", "ha_token": "t"}),
            ),
        ):
            assert await plug_energy.read_plug(plug, db_session) == (None, None)

    @pytest.mark.asyncio
    async def test_mqtt_plug_answers_from_its_last_message(self, db_session):
        from types import SimpleNamespace

        from backend.app.services import plug_energy

        plug = await _plug(db_session, plug_type="mqtt")
        mqtt = plug_energy.mqtt_relay.smart_plug_service
        with (
            patch.object(mqtt, "get_plug_data", return_value=SimpleNamespace(state="OFF", power=0.0, energy=1.0)),
            patch.object(mqtt, "is_reachable", return_value=True),
        ):
            assert await plug_energy.read_plug(plug, db_session) == ("OFF", 0.0)
        with patch.object(mqtt, "get_plug_data", return_value=None):
            assert await plug_energy.read_plug(plug, db_session) == (None, None)


class TestReviewRound1:
    """Alerts must not be lost on a failed write, one plug's error must not stop
    the others, stale state must not swallow a crossing, a plug that can't say
    whether it is on raises no low alert, and an unreachable plug is not polled
    every minute."""

    @pytest.mark.asyncio
    async def test_a_failed_write_does_not_lose_the_alert(self, db_session, sent):
        from sqlalchemy.exc import OperationalError

        plug = await _plug(db_session)
        monitor = PowerAlertMonitor()
        real_commit = db_session.commit
        failing = AsyncMock(side_effect=OperationalError("UPDATE", {}, Exception("database is locked")))

        with (
            patch("backend.app.services.power_alerts.read_plug", AsyncMock(return_value=("ON", 250.0))),
            patch.object(db_session, "commit", failing),
        ):
            await check_all_plugs(db_session, monitor)
        sent.assert_not_awaited()

        db_session.commit = real_commit
        with patch("backend.app.services.power_alerts.read_plug", AsyncMock(return_value=("ON", 250.0))):
            await check_all_plugs(db_session, monitor)
        sent.assert_awaited_once()
        assert sent.await_args.kwargs["printer_name"] == plug.name

    @pytest.mark.asyncio
    async def test_a_failed_send_is_retried_after_the_cooldown(self, db_session, sent):
        plug = await _plug(db_session)
        plug_id = plug.id
        monitor = PowerAlertMonitor()
        sent.side_effect = [RuntimeError("provider down"), None]

        with patch("backend.app.services.power_alerts.read_plug", AsyncMock(return_value=("ON", 250.0))):
            await check_all_plugs(db_session, monitor)
            row = (await db_session.execute(select(SmartPlug).where(SmartPlug.id == plug_id))).scalar_one()
            row.power_alert_last_triggered = utcnow_naive() - timedelta(minutes=10)
            await db_session.commit()
            await check_all_plugs(db_session, monitor)

        assert sent.await_count == 2

    @pytest.mark.asyncio
    async def test_one_plugs_db_error_does_not_stop_the_others(self, test_engine, db_session, sent):
        """A real failed UPDATE leaves the session needing a rollback, as
        "database is locked" does on SQLite."""
        from sqlalchemy import event
        from sqlalchemy.exc import OperationalError

        await _plug(db_session, name="First")
        await _plug(db_session, name="Second", ip_address="192.168.1.51")
        failed = {"done": False}

        def fail_first_update(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("UPDATE SMART_PLUGS") and not failed["done"]:
                failed["done"] = True
                raise OperationalError(statement, parameters, Exception("database is locked"))

        event.listen(test_engine.sync_engine, "before_cursor_execute", fail_first_update)
        try:
            with patch("backend.app.services.power_alerts.read_plug", AsyncMock(return_value=("ON", 250.0))):
                await check_all_plugs(db_session, PowerAlertMonitor())
        finally:
            event.remove(test_engine.sync_engine, "before_cursor_execute", fail_first_update)

        assert failed["done"]
        assert [c.kwargs["printer_name"] for c in sent.await_args_list] == ["Second"]

    @pytest.mark.asyncio
    async def test_switching_alerts_off_and_on_forgets_the_old_crossing(self, db_session, sent):
        plug = await _plug(db_session, power_alert_high=None, power_alert_low=10.0)
        plug_id = plug.id
        monitor = PowerAlertMonitor()
        reader = AsyncMock(return_value=("ON", 3.0))

        async def set_enabled(enabled: bool):
            row = (await db_session.execute(select(SmartPlug).where(SmartPlug.id == plug_id))).scalar_one()
            row.power_alert_enabled = enabled
            row.power_alert_last_triggered = utcnow_naive() - timedelta(minutes=10)
            await db_session.commit()

        with patch("backend.app.services.power_alerts.read_plug", reader):
            await check_all_plugs(db_session, monitor)
            await set_enabled(False)
            await check_all_plugs(db_session, monitor)
            await set_enabled(True)
            await check_all_plugs(db_session, monitor)

        assert sent.await_count == 2

    @pytest.mark.asyncio
    async def test_unknown_switch_state_raises_no_low_alert_but_still_high(self, db_session, sent):
        plug = await _plug(db_session, power_alert_high=200.0, power_alert_low=10.0)
        monitor = PowerAlertMonitor()

        await monitor.evaluate(plug, None, 0.0, db_session)
        sent.assert_not_awaited()

        await monitor.evaluate(plug, None, 250.0, db_session)
        sent.assert_awaited_once()
        assert sent.await_args.kwargs["error_type"] == "Power High"

    @pytest.mark.asyncio
    async def test_an_unreachable_plug_is_polled_again_only_after_a_pause(self, db_session, sent):
        await _plug(db_session)
        monitor = PowerAlertMonitor()
        reader = AsyncMock(return_value=(None, None))
        clock = {"now": 1000.0}

        with (
            patch("backend.app.services.power_alerts.read_plug", reader),
            patch("backend.app.services.power_alerts.time.monotonic", side_effect=lambda: clock["now"]),
        ):
            await check_all_plugs(db_session, monitor)
            clock["now"] += 60
            await check_all_plugs(db_session, monitor)
            assert reader.await_count == 1

            clock["now"] += 600
            await check_all_plugs(db_session, monitor)
            assert reader.await_count == 2


class TestReviewRound2:
    def test_a_fresh_mqtt_subscription_can_be_asked_if_reachable(self):
        """Its default last_seen had no time zone, so the check raised TypeError
        until the first message arrived."""
        from backend.app.services.mqtt_smart_plug import MQTTSmartPlugService, SmartPlugMQTTData

        service = MQTTSmartPlugService()
        service.plug_data[7] = SmartPlugMQTTData(plug_id=7)

        assert service.is_reachable(7) is True

    @pytest.mark.asyncio
    async def test_an_mqtt_plug_without_data_is_not_paused(self, db_session, sent):
        """MQTT is read from memory, so there is no network call to spare; a plug
        that hasn't published yet must be checked again on the next pass."""
        await _plug(db_session, plug_type="mqtt")
        monitor = PowerAlertMonitor()
        reader = AsyncMock(side_effect=[(None, None), ("ON", 250.0)])

        with patch("backend.app.services.power_alerts.read_plug", reader):
            await check_all_plugs(db_session, monitor)
            await check_all_plugs(db_session, monitor)

        assert reader.await_count == 2
        sent.assert_awaited_once()
