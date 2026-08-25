from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.ha_sensor_manager import SensorReading
from backend.app.services.location_ha_sensor_manager import location_ha_sensor_manager

HUMIDITY = {
    "name": "Drybox Humidity",
    "entity_id": "sensor.drybox_humidity",
    "kind": "numeric",
    "device_class": "humidity",
    "unit": "%",
}
DOOR = {
    "name": "Cabinet Door",
    "entity_id": "binary_sensor.cabinet_door",
    "kind": "binary",
    "device_class": "door",
    "alert_state": "on",
}


@pytest.fixture(autouse=True)
def _no_live_ha():
    with patch.object(location_ha_sensor_manager, "refresh_one", AsyncMock()):
        yield


@pytest.fixture(autouse=True)
def _clean_cache():
    yield
    location_ha_sensor_manager._readings.clear()
    location_ha_sensor_manager._last_alerting.clear()


class TestCrud:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bind_a_humidity_sensor(self, async_client: AsyncClient, location_factory):
        location = await location_factory()

        response = await async_client.post(
            "/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["entity_id"] == "sensor.drybox_humidity"
        assert body["kind"] == "numeric"
        assert body["show_on_card"] is True
        assert body["notify_on_alert"] is False
        assert "block_print" not in body

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_a_switch(self, async_client: AsyncClient, location_factory):
        location = await location_factory()

        response = await async_client.post(
            "/api/v1/location-ha-sensors/",
            json={**DOOR, "location_id": location.id, "entity_id": "switch.something"},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_a_kind_that_contradicts_the_entity(self, async_client: AsyncClient, location_factory):
        location = await location_factory()

        response = await async_client.post(
            "/api/v1/location-ha-sensors/",
            json={**HUMIDITY, "location_id": location.id, "kind": "binary"},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_a_notify_with_nothing_to_trigger_on(self, async_client: AsyncClient, location_factory):
        location = await location_factory()

        response = await async_client.post(
            "/api/v1/location-ha-sensors/",
            json={**DOOR, "location_id": location.id, "alert_state": None, "notify_on_alert": True},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_a_duplicate_binding(self, async_client: AsyncClient, location_factory):
        location = await location_factory()
        payload = {**HUMIDITY, "location_id": location.id}
        await async_client.post("/api/v1/location-ha-sensors/", json=payload)

        response = await async_client.post("/api/v1/location-ha-sensors/", json=payload)

        assert response.status_code == 400
        assert "already bound" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_an_entity_id_longer_than_the_column(self, async_client: AsyncClient, location_factory):
        """entity_id is bounded by max_length, not just by its pattern.

        The pattern's [a-z0-9_]+ is unbounded, so a direct API caller could
        exceed the String(255) column. SQLite stores it regardless, but
        PostgreSQL raises DataError, and the route only maps IntegrityError —
        it would come back as a 500 instead of a 422.
        """
        location = await location_factory()

        response = await async_client.post(
            "/api/v1/location-ha-sensors/",
            json={**HUMIDITY, "location_id": location.id, "entity_id": "sensor." + "a" * 400},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_rejects_an_entity_id_longer_than_the_column(self, async_client: AsyncClient, location_factory):
        location = await location_factory()
        created = await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id})

        response = await async_client.patch(
            f"/api/v1/location-ha-sensors/{created.json()['id']}",
            json={"entity_id": "sensor." + "b" * 400},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_row_that_predates_the_bound_is_still_readable(
        self, async_client: AsyncClient, db_session, location_factory
    ):
        """The bound guards writes; it must not turn old rows into a 500.

        SQLite never enforced the column's 255, so an install that took a
        long entity_id through the API before max_length existed has that row
        today. Inheriting the bound on the response model would fail response
        validation and take the whole list down for one row -- the same 500
        the bound was added to prevent, moved to the read path.
        """
        from sqlalchemy import text

        location = await location_factory()
        created = await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id})
        legacy_id = "sensor." + "a" * 400

        await db_session.execute(
            text("UPDATE location_ha_sensors SET entity_id = :e WHERE id = :i"),
            {"e": legacy_id, "i": created.json()["id"]},
        )
        await db_session.commit()

        response = await async_client.get(f"/api/v1/location-ha-sensors/?location_id={location.id}")

        assert response.status_code == 200
        assert response.json()[0]["entity_id"] == legacy_id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_an_unknown_location(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": 9999})

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_accepts_a_coherent_change(self, async_client: AsyncClient, location_factory):
        location = await location_factory()
        created = await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id})
        sensor_id = created.json()["id"]

        response = await async_client.patch(
            f"/api/v1/location-ha-sensors/{sensor_id}",
            json={"alert_above": 60, "notify_on_alert": True, "name": "Box Humidity"},
        )

        assert response.status_code == 200
        assert response.json()["notify_on_alert"] is True
        assert response.json()["name"] == "Box Humidity"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_drops_the_cached_reading(self, async_client: AsyncClient, location_factory):
        location = await location_factory()
        created = await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id})
        sensor_id = created.json()["id"]
        location_ha_sensor_manager._readings[sensor_id] = SensorReading("41.2", 41.2, True, True)

        response = await async_client.delete(f"/api/v1/location-ha-sensors/{sensor_id}")

        assert response.status_code == 200
        assert location_ha_sensor_manager.get_reading(sensor_id) is None


class TestReadings:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_serves_the_cached_reading(self, async_client: AsyncClient, location_factory):
        location = await location_factory()
        created = await async_client.post(
            "/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id, "alert_above": 60}
        )
        sensor_id = created.json()["id"]
        location_ha_sensor_manager._readings[sensor_id] = SensorReading("65.0", 65.0, True, True)

        response = await async_client.get(f"/api/v1/location-ha-sensors/by-location/{location.id}/readings")

        assert response.status_code == 200
        reading = response.json()[0]
        assert reading["value"] == 65.0
        assert reading["alerting"] is True
        assert reading["unit"] == "%"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_hidden_sensors_stay_off_the_card(self, async_client: AsyncClient, location_factory):
        location = await location_factory()
        await async_client.post(
            "/api/v1/location-ha-sensors/",
            json={**HUMIDITY, "location_id": location.id, "show_on_card": False},
        )

        response = await async_client.get(f"/api/v1/location-ha-sensors/by-location/{location.id}/readings")

        assert response.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_hidden_sensors_are_included_when_not_restricted_to_the_card(
        self, async_client: AsyncClient, location_factory
    ):
        location = await location_factory()
        await async_client.post(
            "/api/v1/location-ha-sensors/",
            json={**HUMIDITY, "location_id": location.id, "show_on_card": False},
        )

        response = await async_client.get(
            f"/api/v1/location-ha-sensors/by-location/{location.id}/readings?show_on_card=false"
        )

        assert len(response.json()) == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_other_locations_sensors_are_not_listed(self, async_client: AsyncClient, location_factory):
        one = await location_factory()
        two = await location_factory()
        await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": one.id})

        response = await async_client.get(f"/api/v1/location-ha-sensors/by-location/{two.id}/readings")

        assert response.json() == []


class TestEntityPicker:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_explains_itself_when_ha_is_not_configured(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/location-ha-sensors/entities")

        assert response.status_code == 400
        assert "Home Assistant not configured" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_entities_is_not_parsed_as_a_sensor_id(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/location-ha-sensors/entities")

        assert response.status_code != 404


class TestCascadeAndUniqueness:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_cannot_create_a_duplicate_binding(self, async_client: AsyncClient, location_factory):
        location = await location_factory()
        await async_client.post("/api/v1/location-ha-sensors/", json={**DOOR, "location_id": location.id})
        second = await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id})

        response = await async_client.patch(
            f"/api/v1/location-ha-sensors/{second.json()['id']}",
            json={"entity_id": DOOR["entity_id"], "kind": "binary"},
        )

        assert response.status_code == 400
        assert "already bound" in response.json()["detail"]

    # One sensor per category per location (#2824 review). The card footer and
    # the inventory column each pick their reading with a single `find`, so a
    # second sensor of the same category silently shadows the first instead of
    # appearing next to it. The modal prompts to replace; these cover the same
    # rule for a direct API caller.
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_a_second_sensor_of_the_same_category(self, async_client: AsyncClient, location_factory):
        location = await location_factory()
        await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id})

        response = await async_client.post(
            "/api/v1/location-ha-sensors/",
            json={
                **HUMIDITY,
                "location_id": location.id,
                "name": "Second Humidity",
                "entity_id": "sensor.drybox_humidity_two",
            },
        )

        assert response.status_code == 400
        assert "humidity" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_moisture_does_not_collide_with_humidity(self, async_client: AsyncClient, location_factory):
        """ "moisture" is binary wet/dry, not a humidity percentage.

        Treating it as the humidity category let a leak detector block the
        hygrometer on the same location, put "wet" in a percent-formatted
        column, and promised it thresholds the schema rejects for a binary
        sensor. It has no category, so it does not take part in this rule.
        """
        location = await location_factory()
        await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id})

        response = await async_client.post(
            "/api/v1/location-ha-sensors/",
            json={
                "location_id": location.id,
                "name": "Drybox Leak",
                "entity_id": "binary_sensor.drybox_moisture",
                "kind": "binary",
                "device_class": "moisture",
                "alert_state": "on",
            },
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_allows_a_different_category_on_the_same_location(self, async_client: AsyncClient, location_factory):
        """The auto-bind flow adds temperature/humidity/battery siblings together."""
        location = await location_factory()
        await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id})

        response = await async_client.post(
            "/api/v1/location-ha-sensors/",
            json={
                **HUMIDITY,
                "location_id": location.id,
                "name": "Drybox Temperature",
                "entity_id": "sensor.drybox_temperature",
                "device_class": "temperature",
                "unit": "°C",
            },
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_same_category_on_another_location_is_fine(self, async_client: AsyncClient, location_factory):
        one = await location_factory()
        two = await location_factory()
        await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": one.id})

        response = await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": two.id})

        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_repointing_a_sensor_within_its_own_category_still_works(
        self, async_client: AsyncClient, location_factory
    ):
        """The modal's replace flow PATCHes the existing row — it must not hit its own rule."""
        location = await location_factory()
        created = await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id})

        response = await async_client.patch(
            f"/api/v1/location-ha-sensors/{created.json()['id']}",
            json={"entity_id": "sensor.other_humidity", "device_class": "humidity"},
        )

        assert response.status_code == 200
        assert response.json()["entity_id"] == "sensor.other_humidity"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_cannot_collide_with_another_sensors_category(
        self, async_client: AsyncClient, location_factory
    ):
        location = await location_factory()
        await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id})
        temperature = await async_client.post(
            "/api/v1/location-ha-sensors/",
            json={
                **HUMIDITY,
                "location_id": location.id,
                "name": "Drybox Temperature",
                "entity_id": "sensor.drybox_temperature",
                "device_class": "temperature",
                "unit": "°C",
            },
        )

        response = await async_client.patch(
            f"/api/v1/location-ha-sensors/{temperature.json()['id']}",
            json={"device_class": "humidity"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deleting_a_location_takes_its_sensors(self, async_client: AsyncClient, location_factory):
        location = await location_factory()
        await async_client.post("/api/v1/location-ha-sensors/", json={**DOOR, "location_id": location.id})

        deleted = await async_client.delete(f"/api/v1/inventory/locations/{location.id}")

        assert deleted.status_code == 200
        listed = await async_client.get("/api/v1/location-ha-sensors/")
        assert listed.json() == []


class TestThresholdValidation:
    """NaN/Infinity must not get into the alert thresholds.

    Pydantic's lax mode coerces the strings "nan"/"inf" into real floats. A
    NaN threshold satisfies "notify_on_alert requires an alert condition" yet
    every comparison against it is False — a notification that can never fire
    — and it slips past the below-vs-above ordering check the same way, while
    responses serialize it as null so the UI shows an empty field.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "Infinity"])
    async def test_create_rejects_non_finite_thresholds(self, async_client: AsyncClient, location_factory, bad):
        location = await location_factory()

        response = await async_client.post(
            "/api/v1/location-ha-sensors/",
            json={**HUMIDITY, "location_id": location.id, "alert_above": bad},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_rejects_non_finite_thresholds(self, async_client: AsyncClient, location_factory):
        location = await location_factory()
        created = await async_client.post("/api/v1/location-ha-sensors/", json={**HUMIDITY, "location_id": location.id})

        response = await async_client.patch(
            f"/api/v1/location-ha-sensors/{created.json()['id']}",
            json={"alert_below": "nan"},
        )

        assert response.status_code == 422


class TestUniqueBindingBackstop:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_database_itself_rejects_a_duplicate_binding(
        self, async_client: AsyncClient, location_factory, db_session
    ):
        """The route's duplicate check is read-then-insert; the unique index is
        what stops the race where two concurrent creates both pass it."""
        from sqlalchemy.exc import IntegrityError

        from backend.app.models.location_ha_sensor import LocationHASensor

        location = await location_factory()
        db_session.add(LocationHASensor(location_id=location.id, name="First", entity_id="sensor.x", kind="numeric"))
        await db_session.commit()

        db_session.add(LocationHASensor(location_id=location.id, name="Second", entity_id="sensor.x", kind="numeric"))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()


class TestAlertDefaultsSetting:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_accepts_a_real_defaults_map(self, async_client: AsyncClient):
        value = '{"humidity": {"alertAbove": "60", "alertBelow": "", "notifyOnAlert": true}}'

        response = await async_client.put("/api/v1/settings/", json={"location_sensor_alert_defaults": value})

        assert response.status_code == 200
        fetched = await async_client.get("/api/v1/settings/")
        assert fetched.json()["location_sensor_alert_defaults"] == value

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_caps_the_stored_length(self, async_client: AsyncClient):
        """The real payload is three categories × three short fields — well
        under 300 characters. The cap only stops a stray client from parking
        megabytes in the settings table; the frontend already treats anything
        unparseable as "use the built-ins"."""
        response = await async_client.put("/api/v1/settings/", json={"location_sensor_alert_defaults": "x" * 2001})

        assert response.status_code == 422


class TestPollInterval:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_defaults_to_120_seconds(self, async_client: AsyncClient):
        interval = await location_ha_sensor_manager._get_poll_interval()

        assert interval == 120

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reads_the_configured_value(self, async_client: AsyncClient):
        response = await async_client.put("/api/v1/settings/", json={"location_sensor_poll_interval": 300})
        assert response.status_code == 200

        interval = await location_ha_sensor_manager._get_poll_interval()

        assert interval == 300

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_a_value_below_the_60s_minimum(self, async_client: AsyncClient):
        response = await async_client.put("/api/v1/settings/", json={"location_sensor_poll_interval": 30})

        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_clamps_a_stored_value_below_the_minimum(self, async_client: AsyncClient, db_session):
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="location_sensor_poll_interval", value="10"))
        await db_session.commit()

        interval = await location_ha_sensor_manager._get_poll_interval()

        assert interval == 60


class TestSaveSurvivesHomeAssistant:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_succeeds_even_if_the_first_read_blows_up(self, async_client: AsyncClient, location_factory):
        location = await location_factory()

        with patch.object(location_ha_sensor_manager, "refresh_one", AsyncMock(side_effect=RuntimeError("HA said no"))):
            response = await async_client.post(
                "/api/v1/location-ha-sensors/", json={**DOOR, "location_id": location.id}
            )

        assert response.status_code == 200
        listed = await async_client.get(f"/api/v1/location-ha-sensors/?location_id={location.id}")
        assert len(listed.json()) == 1
