"""#3582: the MQTT plug's lifetime energy path through the smart plug API."""

import pytest
from httpx import AsyncClient

from backend.app.services.mqtt_smart_plug import SmartPlugMQTTData

TOPIC = "tele/tasmota_plug/SENSOR"


def _payload(**overrides) -> dict:
    data = {
        "name": "Tasmota MQTT",
        "plug_type": "mqtt",
        "mqtt_energy_topic": TOPIC,
        "mqtt_energy_path": "ENERGY.Today",
        "mqtt_energy_total_path": "ENERGY.Total",
        "mqtt_energy_total_multiplier": 0.001,
        "enabled": True,
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_stores_and_subscribes_the_lifetime_path(async_client: AsyncClient, mock_mqtt_smart_plug_service):
    response = await async_client.post("/api/v1/smart-plugs/", json=_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mqtt_energy_total_path"] == "ENERGY.Total"
    assert body["mqtt_energy_total_multiplier"] == 0.001
    kwargs = mock_mqtt_smart_plug_service.smart_plug_service.subscribe.call_args.kwargs
    assert kwargs["energy_topic"] == TOPIC
    assert kwargs["energy_total_path"] == "ENERGY.Total"
    assert kwargs["energy_total_multiplier"] == 0.001


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_without_energy_topic_is_refused(async_client: AsyncClient, mock_mqtt_smart_plug_service):
    response = await async_client.post(
        "/api/v1/smart-plugs/",
        json=_payload(mqtt_energy_topic=None, mqtt_power_topic="p/power"),
    )

    assert response.status_code == 422
    assert "lifetime energy path needs an energy topic" in response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_changing_only_the_lifetime_path_resubscribes(async_client: AsyncClient, mock_mqtt_smart_plug_service):
    service = mock_mqtt_smart_plug_service.smart_plug_service
    created = (await async_client.post("/api/v1/smart-plugs/", json=_payload(mqtt_energy_total_path=None))).json()
    assert service.subscribe.call_args.kwargs["energy_total_path"] is None
    service.subscribe.reset_mock()

    response = await async_client.patch(
        f"/api/v1/smart-plugs/{created['id']}", json={"mqtt_energy_total_path": "ENERGY.Total"}
    )

    assert response.status_code == 200, response.text
    service.unsubscribe.assert_called_once_with(created["id"])
    assert service.subscribe.call_args.kwargs["energy_total_path"] == "ENERGY.Total"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_status_reports_today_and_lifetime_separately(async_client: AsyncClient, mock_mqtt_smart_plug_service):
    service = mock_mqtt_smart_plug_service.smart_plug_service
    created = (await async_client.post("/api/v1/smart-plugs/", json=_payload())).json()
    service.get_plug_data.return_value = SmartPlugMQTTData(
        plug_id=created["id"], power=150.0, energy=0.3, energy_total=843.4, state="ON"
    )
    service.is_reachable.return_value = True

    response = await async_client.get(f"/api/v1/smart-plugs/{created['id']}/status")

    assert response.status_code == 200, response.text
    energy = response.json()["energy"]
    assert energy["today"] == pytest.approx(0.3)
    assert energy["total"] == pytest.approx(843.4)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_status_without_lifetime_path_is_unchanged(async_client: AsyncClient, mock_mqtt_smart_plug_service):
    service = mock_mqtt_smart_plug_service.smart_plug_service
    created = (await async_client.post("/api/v1/smart-plugs/", json=_payload(mqtt_energy_total_path=None))).json()
    service.get_plug_data.return_value = SmartPlugMQTTData(plug_id=created["id"], power=150.0, energy=4.8, state="ON")
    service.is_reachable.return_value = True

    response = await async_client.get(f"/api/v1/smart-plugs/{created['id']}/status")

    energy = response.json()["energy"]
    assert energy["today"] == pytest.approx(4.8)
    assert energy["total"] is None
