"""An update cannot leave a smart plug without what its type needs.

Create checks that a Tasmota plug has an address, a Home Assistant plug an
entity, an MQTT plug a topic and a REST plug a URL. An update applied the
change without that check, so clearing the field (or switching the type
without it) stored a plug every later read of it failed on: a 500 from the
plug list, the plug detail and the update itself.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _plug(async_client: AsyncClient, **fields) -> dict:
    response = await async_client.post("/api/v1/smart-plugs/", json={"name": "Plug", **fields})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("created", "change", "message"),
    [
        ({"ip_address": "192.168.1.100"}, {"ip_address": None}, "ip_address is required for Tasmota plugs"),
        # As on create: an empty address fails the address format first.
        ({"ip_address": "192.168.1.100"}, {"ip_address": ""}, "String should match pattern"),
        (
            {"ip_address": "192.168.1.100"},
            {"plug_type": "homeassistant"},
            "ha_entity_id is required for Home Assistant plugs",
        ),
        (
            {"plug_type": "homeassistant", "ha_entity_id": "switch.printer"},
            {"ha_entity_id": None},
            "ha_entity_id is required for Home Assistant plugs",
        ),
        (
            {"plug_type": "mqtt", "mqtt_power_topic": "plug/power"},
            {"mqtt_power_topic": None},
            "At least one MQTT topic must be configured",
        ),
        (
            {"plug_type": "rest", "rest_on_url": "http://192.168.1.80/on"},
            {"rest_on_url": None},
            "At least one of ON URL or OFF URL is required",
        ),
    ],
)
async def test_an_update_that_drops_a_required_field_is_refused(
    async_client: AsyncClient, mock_mqtt_smart_plug_service, created, change, message
):
    plug = await _plug(async_client, **created)

    response = await async_client.patch(f"/api/v1/smart-plugs/{plug['id']}", json=change)

    assert response.status_code == 422, response.text
    assert message in response.text
    # Nothing was stored: the plug still reads back as it was.
    after = await async_client.get(f"/api/v1/smart-plugs/{plug['id']}")
    assert after.status_code == 200, after.text
    listed = await async_client.get("/api/v1/smart-plugs/")
    assert listed.status_code == 200, listed.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_update_that_keeps_the_type_complete_is_applied(async_client: AsyncClient):
    plug = await _plug(async_client, ip_address="192.168.1.100")

    response = await async_client.patch(
        f"/api/v1/smart-plugs/{plug['id']}",
        json={"plug_type": "homeassistant", "ha_entity_id": "switch.printer", "ip_address": None},
    )

    assert response.status_code == 200, response.text
    assert response.json()["plug_type"] == "homeassistant"
    assert response.json()["ha_entity_id"] == "switch.printer"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_unrelated_update_still_works(async_client: AsyncClient):
    plug = await _plug(async_client, ip_address="192.168.1.100")

    response = await async_client.patch(f"/api/v1/smart-plugs/{plug['id']}", json={"name": "Renamed"})

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("created", "change"),
    [
        ({"ip_address": "192.168.1.100"}, {"ip_address": "plug.local"}),
        ({"ip_address": "192.168.1.100"}, {"name": ""}),
        ({"ip_address": "192.168.1.100"}, {"name": "x" * 150}),
        ({"ip_address": "192.168.1.100"}, {"plug_type": "homeassistant", "ha_entity_id": "Bad Entity"}),
        ({"plug_type": "homeassistant", "ha_entity_id": "switch.printer"}, {"ha_power_entity": "switch.not_a_sensor"}),
    ],
)
async def test_an_update_with_a_value_create_would_refuse_is_refused(async_client: AsyncClient, created, change):
    """Values in the wrong form broke the read-back the same way."""
    plug = await _plug(async_client, **created)

    response = await async_client.patch(f"/api/v1/smart-plugs/{plug['id']}", json=change)

    assert response.status_code == 422, response.text
    assert (await async_client.get(f"/api/v1/smart-plugs/{plug['id']}")).status_code == 200
    assert (await async_client.get("/api/v1/smart-plugs/")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_plug_stored_invalid_earlier_can_be_repaired(async_client: AsyncClient, db_session):
    from backend.app.models.smart_plug import SmartPlug

    plug = SmartPlug(name="Old", plug_type="tasmota", ip_address="plug.local")
    db_session.add(plug)
    await db_session.commit()

    response = await async_client.patch(f"/api/v1/smart-plugs/{plug.id}", json={"ip_address": "192.168.1.5"})

    assert response.status_code == 200, response.text
    assert response.json()["ip_address"] == "192.168.1.5"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_refused_update_does_not_echo_the_stored_plug(async_client: AsyncClient):
    """The error names the field, nothing else: the stored plug carries its
    write-only credentials."""
    plug = await _plug(async_client, ip_address="192.168.1.100", username="admin", password="plug-pw-value-8")

    # Changes that keep the address, so the stored password stays in the plug.
    for change in ({"name": ""}, {"name": "x" * 150}):
        response = await async_client.patch(f"/api/v1/smart-plugs/{plug['id']}", json=change)
        assert response.status_code == 422, response.text
        assert "plug-pw-value-8" not in response.text
        assert set().union(*(error.keys() for error in response.json()["detail"])) <= {"type", "loc", "msg"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_switching_to_tasmota_keeps_one_tasmota_plug_per_printer(async_client: AsyncClient, printer_factory):
    """Create allows one Tasmota plug per printer; an edit that only changes
    the type must not add a second."""
    printer = await printer_factory(name="P1")
    await _plug(async_client, ip_address="192.168.1.100", printer_id=printer.id)
    ha = await _plug(async_client, plug_type="homeassistant", ha_entity_id="switch.p1", printer_id=printer.id)

    response = await async_client.patch(
        f"/api/v1/smart-plugs/{ha['id']}", json={"plug_type": "tasmota", "ip_address": "192.168.1.101"}
    )

    assert response.status_code == 400, response.text
    assert "already has a Tasmota plug" in response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_printer_already_holding_two_tasmota_plugs_is_answered_not_crashed(
    async_client: AsyncClient, db_session, printer_factory
):
    """Rows stored before the check covered type changes."""
    from backend.app.models.smart_plug import SmartPlug

    printer = await printer_factory(name="P2")
    for ip in ("192.168.1.110", "192.168.1.111"):
        db_session.add(SmartPlug(name=ip, plug_type="tasmota", ip_address=ip, printer_id=printer.id))
    await db_session.commit()

    created = await async_client.post(
        "/api/v1/smart-plugs/", json={"name": "Third", "ip_address": "192.168.1.112", "printer_id": printer.id}
    )

    assert created.status_code == 400, created.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_plugs_on_an_already_doubled_printer_can_still_be_toggled(
    async_client: AsyncClient, db_session, printer_factory
):
    """The check is for an edit that makes a plug the printer's second Tasmota
    plug; a toggle on either of two stored ones changes neither."""
    from backend.app.models.smart_plug import SmartPlug

    printer = await printer_factory(name="P3")
    plugs = [
        SmartPlug(name=ip, plug_type="tasmota", ip_address=ip, printer_id=printer.id)
        for ip in ("192.168.1.120", "192.168.1.121")
    ]
    db_session.add_all(plugs)
    await db_session.commit()

    for plug in plugs:
        response = await async_client.patch(
            f"/api/v1/smart-plugs/{plug.id}", json={"auto_off": False, "name": "Renamed"}
        )
        assert response.status_code == 200, response.text
