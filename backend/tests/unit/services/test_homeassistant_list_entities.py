"""Regression tests for HomeAssistantService.list_entities domain filtering (#1388).

Reporter MartinNYHC opened the Add Smart Plug modal in HA mode, typed a search
matching a multi-entity device (one Shelly outlet exposed as switch + several
sensor.* and binary_sensor.* siblings), and clicked a non-switch entity. The
schema regex for ha_entity_id only accepts switch/light/input_boolean/script,
so the Save round-trip came back 422 with the raw Pydantic pattern string —
the same regex shown in the bug report screenshot.

Root cause: before this fix, the search path bypassed the domain filter
entirely, so the dropdown showed every entity whose entity_id or friendly_name
matched the query, regardless of whether the schema would later accept it.
Users could click an entity they had no way to actually save.

Fix: always apply the allowed-domains filter, and apply the search filter on
top of it. The two filters now compose instead of branching.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.homeassistant import HomeAssistantService


def _ha_response(entities: list[dict]) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=entities)
    return response


def _mock_get(entities: list[dict]):
    async_client = MagicMock()
    async_client.get = AsyncMock(return_value=_ha_response(entities))
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=None)
    return async_client


@pytest.mark.asyncio
async def test_no_search_returns_only_allowed_domains():
    """Without a search query, only switch/light/input_boolean/script appear."""
    entities = [
        {"entity_id": "switch.printer", "attributes": {"friendly_name": "Printer"}, "state": "on"},
        {"entity_id": "light.lamp", "attributes": {"friendly_name": "Lamp"}, "state": "off"},
        {"entity_id": "input_boolean.flag", "attributes": {"friendly_name": "Flag"}, "state": "on"},
        {"entity_id": "script.morning", "attributes": {"friendly_name": "Morning"}, "state": "off"},
        {"entity_id": "sensor.power", "attributes": {"friendly_name": "Power"}, "state": "12.3"},
        {"entity_id": "binary_sensor.status", "attributes": {"friendly_name": "Status"}, "state": "on"},
        {"entity_id": "media_player.tv", "attributes": {"friendly_name": "TV"}, "state": "idle"},
    ]
    service = HomeAssistantService()

    with patch("httpx.AsyncClient", return_value=_mock_get(entities)):
        result = await service.list_entities("http://ha.local", "tok")

    domains = sorted({e["domain"] for e in result})
    assert domains == ["input_boolean", "light", "script", "switch"]
    assert len(result) == 4


@pytest.mark.asyncio
async def test_search_still_filters_to_allowed_domains():
    """#1388: search must compose with the domain filter, not replace it.

    Reporter's setup: a Shelly outlet device generates one switch.* entity
    and several sensor.*/binary_sensor.* siblings, all sharing a common
    friendly-name prefix. The user searched the prefix and was offered the
    non-switch siblings as clickable options — picking one led to the 422
    pattern error. After the fix, the search-narrowed list excludes them.
    """
    entities = [
        {
            "entity_id": "switch.prise_imprimante_3d_bambu_output_1",
            "attributes": {"friendly_name": "Prise imprimante 3D Bambu Output 1"},
            "state": "on",
        },
        {
            "entity_id": "sensor.prise_imprimante_3d_bambu_output_1_power",
            "attributes": {"friendly_name": "Prise imprimante 3D Bambu Output 1 Puissance"},
            "state": "12.5",
        },
        {
            "entity_id": "binary_sensor.prise_imprimante_3d_bambu_output_1_status",
            "attributes": {"friendly_name": "Prise imprimante 3D Bambu Output 1 Status"},
            "state": "on",
        },
        {
            "entity_id": "sensor.prise_imprimante_3d_bambu_output_1_energy",
            "attributes": {"friendly_name": "Prise imprimante 3D Bambu Output 1 Énergie"},
            "state": "0.42",
        },
    ]
    service = HomeAssistantService()

    with patch("httpx.AsyncClient", return_value=_mock_get(entities)):
        result = await service.list_entities("http://ha.local", "tok", search="Prise imprimante")

    assert len(result) == 1
    assert result[0]["entity_id"] == "switch.prise_imprimante_3d_bambu_output_1"


@pytest.mark.asyncio
async def test_search_matches_by_entity_id_or_friendly_name():
    """Search still matches across both fields, just within the allowed set."""
    entities = [
        {"entity_id": "switch.printer_a", "attributes": {"friendly_name": "Living Room Plug"}, "state": "on"},
        {"entity_id": "switch.printer_b", "attributes": {"friendly_name": "Office Plug"}, "state": "off"},
        {"entity_id": "light.living_room", "attributes": {"friendly_name": "Ceiling"}, "state": "off"},
    ]
    service = HomeAssistantService()

    with patch("httpx.AsyncClient", return_value=_mock_get(entities)):
        result = await service.list_entities("http://ha.local", "tok", search="living")

    ids = sorted(e["entity_id"] for e in result)
    assert ids == ["light.living_room", "switch.printer_a"]


@pytest.mark.asyncio
async def test_search_is_case_insensitive():
    entities = [
        {"entity_id": "switch.PRINTER", "attributes": {"friendly_name": "Printer"}, "state": "on"},
    ]
    service = HomeAssistantService()

    with patch("httpx.AsyncClient", return_value=_mock_get(entities)):
        result = await service.list_entities("http://ha.local", "tok", search="PRINTER")

    assert len(result) == 1


@pytest.mark.asyncio
async def test_empty_search_treated_as_no_search():
    """A whitespace-only search string should fall back to the full allowed-
    domain list rather than matching everything that contains an empty string."""
    entities = [
        {"entity_id": "switch.foo", "attributes": {"friendly_name": "Foo"}, "state": "on"},
        {"entity_id": "sensor.bar", "attributes": {"friendly_name": "Bar"}, "state": "1"},
    ]
    service = HomeAssistantService()

    with patch("httpx.AsyncClient", return_value=_mock_get(entities)):
        result = await service.list_entities("http://ha.local", "tok", search="   ")

    assert len(result) == 1
    assert result[0]["entity_id"] == "switch.foo"
