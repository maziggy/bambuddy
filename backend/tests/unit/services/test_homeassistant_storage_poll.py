"""Tests for HomeAssistantService.poll_storage_unit attribute fallback.

Filament dryers / storage boxes are monitored via cheap HA sensors. Most
report a numeric ``state`` on a ``sensor.*`` entity and work directly. But some
users point a storage unit at a ``climate.*`` entity (or a device) whose
``state`` is a mode string like ``"heat"`` and whose real reading lives under
attributes (``current_temperature`` / ``current_humidity``). Before the
fallback, those entities silently reported nothing — the dryer card stayed on
"Entities configured but no data yet" forever.

These tests lock in that the poller reads ``state`` first and falls back to the
known attribute keys, while still rejecting genuinely unavailable entities.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.homeassistant import HomeAssistantService


def _state_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response


def _mock_client(by_entity: dict[str, dict]):
    """Return a mocked httpx.AsyncClient that serves payloads keyed by entity id.

    The entity id is the last path segment of the /api/states/<entity> URL.
    """

    async def _get(url, *args, **kwargs):
        entity_id = url.rsplit("/", 1)[-1]
        return _state_response(by_entity[entity_id])

    client = MagicMock()
    client.get = AsyncMock(side_effect=_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _service() -> HomeAssistantService:
    service = HomeAssistantService()
    service.configure("http://ha.local", "tok")
    return service


@pytest.mark.asyncio
async def test_numeric_state_sensors_report_directly():
    """The common case: plain sensor.* entities with numeric state."""
    by_entity = {
        "sensor.dryer_temperature": {"state": "45.2", "attributes": {"unit_of_measurement": "°C"}},
        "sensor.dryer_humidity": {"state": "18", "attributes": {"unit_of_measurement": "%"}},
    }
    service = _service()

    with patch("httpx.AsyncClient", return_value=_mock_client(by_entity)):
        result = await service.poll_storage_unit(1, "sensor.dryer_temperature", "sensor.dryer_humidity")

    assert result["temp"] == 45.2
    assert result["temp_unit"] == "°C"
    assert result["humidity"] == 18.0
    # Cache is populated for the API layer to read.
    assert service.get_cached_storage(1) == result


@pytest.mark.asyncio
async def test_climate_entity_falls_back_to_attributes():
    """A climate.* entity reports a mode string as state; reading is in attrs."""
    by_entity = {
        "climate.dryer": {
            "state": "heat",
            "attributes": {"current_temperature": 50.0, "current_humidity": 22.5},
        },
    }
    service = _service()

    with patch("httpx.AsyncClient", return_value=_mock_client(by_entity)):
        result = await service.poll_storage_unit(2, "climate.dryer", "climate.dryer")

    assert result["temp"] == 50.0
    assert result["humidity"] == 22.5


@pytest.mark.asyncio
async def test_unavailable_entity_reports_nothing():
    """unknown/unavailable with no usable attribute yields None, not a crash."""
    by_entity = {
        "sensor.dryer_temperature": {"state": "unavailable", "attributes": {}},
        "sensor.dryer_humidity": {"state": "unknown", "attributes": {}},
    }
    service = _service()

    with patch("httpx.AsyncClient", return_value=_mock_client(by_entity)):
        result = await service.poll_storage_unit(3, "sensor.dryer_temperature", "sensor.dryer_humidity")

    assert result["temp"] is None
    assert result["humidity"] is None


@pytest.mark.asyncio
async def test_only_one_entity_configured():
    """A unit may have just a humidity sensor; temp stays None."""
    by_entity = {
        "sensor.box_humidity": {"state": "40.5", "attributes": {}},
    }
    service = _service()

    with patch("httpx.AsyncClient", return_value=_mock_client(by_entity)):
        result = await service.poll_storage_unit(4, None, "sensor.box_humidity")

    assert result["temp"] is None
    assert result["humidity"] == 40.5


@pytest.mark.asyncio
async def test_no_entities_returns_none():
    service = _service()
    result = await service.poll_storage_unit(5, None, None)
    assert result is None


def test_coerce_float_rejects_sentinels():
    assert HomeAssistantService._coerce_float("unavailable") is None
    assert HomeAssistantService._coerce_float("unknown") is None
    assert HomeAssistantService._coerce_float("") is None
    assert HomeAssistantService._coerce_float(None) is None
    assert HomeAssistantService._coerce_float("nan") is None
    assert HomeAssistantService._coerce_float("42.5") == 42.5
    assert HomeAssistantService._coerce_float(13) == 13.0
