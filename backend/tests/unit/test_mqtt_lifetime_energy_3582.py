"""#3582: an MQTT plug's lifetime energy counter, beside its daily one.

Per-print energy is the difference of two "total" readings. An MQTT plug had
one energy value and used it as both today and total, so with a daily counter
(Tasmota ``ENERGY.Today``) a print running across midnight came out negative
(and was dropped) or too small. A plug with ``mqtt_energy_total_path`` reads
the lifetime counter from the same topic; one without keeps the old reading.
"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from backend.app.services.energy_plug import select_energy_reading
from backend.app.services.mqtt_smart_plug import MQTTSmartPlugService, subscribe_plug_to_mqtt
from backend.app.services.plug_energy import get_plug_energy

TOPIC = "tele/tasmota_plug/SENSOR"


def _sensor(today: float, total: float) -> SimpleNamespace:
    """A Tasmota SENSOR message carrying both counters."""
    payload = {"ENERGY": {"Today": today, "Total": total, "Power": 150}}
    return SimpleNamespace(topic=TOPIC, payload=json.dumps(payload).encode())


def _plug(plug_id: int = 7, total_path: str | None = "ENERGY.Total", total_multiplier: float = 1.0):
    return SimpleNamespace(
        id=plug_id,
        plug_type="mqtt",
        mqtt_topic=None,
        mqtt_multiplier=None,
        mqtt_power_topic=TOPIC,
        mqtt_power_path="ENERGY.Power",
        mqtt_power_multiplier=1.0,
        mqtt_energy_topic=TOPIC,
        mqtt_energy_path="ENERGY.Today",
        mqtt_energy_multiplier=1.0,
        mqtt_energy_total_path=total_path,
        mqtt_energy_total_multiplier=total_multiplier,
        mqtt_state_topic=None,
        mqtt_state_path=None,
        mqtt_state_on_value=None,
    )


def _service_with(plug) -> MQTTSmartPlugService:
    service = MQTTSmartPlugService()
    subscribe_plug_to_mqtt(service, plug)
    return service


class TestSubscription:
    def test_both_counters_are_read_from_one_message(self):
        plug = _plug()
        service = _service_with(plug)

        service._on_message(None, None, _sensor(today=1.25, total=842.5))

        data = service.get_plug_data(plug.id)
        assert data.energy == pytest.approx(1.25)
        assert data.energy_total == pytest.approx(842.5)

    def test_lifetime_multiplier_is_its_own(self):
        """A device reporting Wh for the lifetime counter only."""
        plug = _plug(total_multiplier=0.001)
        service = _service_with(plug)

        service._on_message(None, None, _sensor(today=1.25, total=842500))

        data = service.get_plug_data(plug.id)
        assert data.energy == pytest.approx(1.25)
        assert data.energy_total == pytest.approx(842.5)

    def test_without_a_lifetime_path_nothing_is_read_for_it(self):
        plug = _plug(total_path=None)
        service = _service_with(plug)

        service._on_message(None, None, _sensor(today=1.25, total=842.5))

        data = service.get_plug_data(plug.id)
        assert data.energy == pytest.approx(1.25)
        assert data.energy_total is None

    def test_blank_lifetime_path_counts_as_unset(self):
        plug = _plug(total_path="   ")
        service = _service_with(plug)

        service._on_message(None, None, _sensor(today=1.25, total=842.5))

        assert service.get_plug_data(plug.id).energy_total is None
        assert "energy_total" not in service.plug_configs[plug.id]


class TestPerPrintAcrossMidnight:
    async def _reading(self, service, plug):
        with patch("backend.app.services.plug_energy.mqtt_relay", SimpleNamespace(smart_plug_service=service)):
            return await get_plug_energy(plug, None)

    @pytest.mark.asyncio
    async def test_a_print_across_midnight_keeps_its_energy(self):
        plug = _plug()
        service = _service_with(plug)

        # 23:00: 4.8 kWh today, 842.5 lifetime. 01:00: the daily counter
        # restarted at midnight, the lifetime one kept counting.
        service._on_message(None, None, _sensor(today=4.8, total=842.5))
        start = await self._reading(service, plug)
        service._on_message(None, None, _sensor(today=0.3, total=843.4))
        end = await self._reading(service, plug)

        assert end["total"] - start["total"] == pytest.approx(0.9)
        assert end["today"] == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_without_a_lifetime_path_the_one_value_is_both(self):
        """Unchanged for existing plugs: right when the topic is a lifetime counter."""
        plug = _plug(total_path=None)
        service = _service_with(plug)

        service._on_message(None, None, _sensor(today=4.8, total=842.5))
        reading = await self._reading(service, plug)

        assert reading["today"] == pytest.approx(4.8)
        assert reading["total"] == pytest.approx(4.8)

    @pytest.mark.asyncio
    async def test_no_lifetime_reading_yet_is_not_picked_as_the_counter(self):
        """Only the daily value has arrived: the plug must not stand in for a
        lifetime counter, or the print's start reading is the daily one."""
        plug = _plug()
        service = MQTTSmartPlugService()
        subscribe_plug_to_mqtt(service, plug)
        service._on_message(
            None,
            None,
            SimpleNamespace(topic=TOPIC, payload=json.dumps({"ENERGY": {"Today": 4.8}}).encode()),
        )

        async def read(p, db):
            return await self._reading(service, p)

        assert await select_energy_reading([plug], read, None) is None


@pytest.fixture
def patched_session(db_session):
    @asynccontextmanager
    async def _session():
        yield db_session

    with patch("backend.app.core.database.async_session", _session):
        yield


class TestSnapshotsAndTotals:
    @pytest.mark.asyncio
    async def test_hourly_snapshot_takes_the_lifetime_counter(self, db_session, smart_plug_factory, patched_session):
        from backend.app.models.smart_plug_energy_snapshot import SmartPlugEnergySnapshot
        from backend.app.services import smart_plug_manager as manager_module

        lifetime = await smart_plug_factory(
            name="Tasmota",
            plug_type="mqtt",
            mqtt_energy_topic=TOPIC,
            mqtt_energy_path="ENERGY.Today",
            mqtt_energy_total_path="ENERGY.Total",
        )
        daily_only = await smart_plug_factory(
            name="Daily", plug_type="mqtt", mqtt_energy_topic=TOPIC, mqtt_energy_path="ENERGY.Today"
        )
        service = MQTTSmartPlugService()
        subscribe_plug_to_mqtt(service, lifetime)
        subscribe_plug_to_mqtt(service, daily_only)
        service._on_message(None, None, _sensor(today=4.8, total=842.5))

        with patch("backend.app.services.plug_energy.mqtt_relay", SimpleNamespace(smart_plug_service=service)):
            await manager_module.SmartPlugManager()._capture_energy_snapshots()

        rows = (await db_session.execute(select(SmartPlugEnergySnapshot))).scalars().all()
        # The daily-only plug may hold a daily counter: never snapshotted.
        assert [(row.plug_id, row.lifetime_kwh) for row in rows] == [(lifetime.id, pytest.approx(842.5))]

    @pytest.mark.asyncio
    async def test_statistics_lifetime_total_reads_the_lifetime_counter(self, db_session, smart_plug_factory):
        from backend.app.api.routes.archives import _sum_live_plug_totals

        lifetime = await smart_plug_factory(
            name="Tasmota",
            plug_type="mqtt",
            mqtt_energy_topic=TOPIC,
            mqtt_energy_path="ENERGY.Today",
            mqtt_energy_total_path="ENERGY.Total",
        )
        daily_only = await smart_plug_factory(
            name="Daily", plug_type="mqtt", mqtt_energy_topic=TOPIC, mqtt_energy_path="ENERGY.Today"
        )
        service = MQTTSmartPlugService()
        subscribe_plug_to_mqtt(service, lifetime)
        subscribe_plug_to_mqtt(service, daily_only)
        service._on_message(None, None, _sensor(today=4.8, total=842.5))

        with patch("backend.app.services.mqtt_relay.mqtt_relay", SimpleNamespace(smart_plug_service=service)):
            total = await _sum_live_plug_totals(db_session)

        # 842.5 lifetime from the first, the one value (4.8) from the second.
        assert total == pytest.approx(842.5 + 4.8)


class TestValidation:
    def test_lifetime_path_needs_an_energy_topic(self):
        from pydantic import ValidationError

        from backend.app.schemas.smart_plug import SmartPlugCreate

        with pytest.raises(ValidationError, match="lifetime energy path needs an energy topic"):
            SmartPlugCreate(
                name="P",
                plug_type="mqtt",
                mqtt_power_topic="p/power",
                mqtt_energy_total_path="ENERGY.Total",
            )

    def test_legacy_topic_serves_as_the_energy_topic(self):
        from backend.app.schemas.smart_plug import SmartPlugCreate

        plug = SmartPlugCreate(name="P", plug_type="mqtt", mqtt_topic=TOPIC, mqtt_energy_total_path="ENERGY.Total")

        assert plug.mqtt_energy_total_path == "ENERGY.Total"
