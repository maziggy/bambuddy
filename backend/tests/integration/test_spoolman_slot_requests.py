"""Spoolman slot requests the database would refuse are refused up front (SSA-8, SSA-9).

An ams_id the table's CHECK does not admit (it allows AMS units 0-7, AMS-HT
128-191 and the external slot 255) came back as a 500. The link route also
wrote the spool's tag to Spoolman before the local insert failed, leaving a
half-applied link. And an archived Spoolman spool could be assigned to a slot,
then charged the next print's usage; the built-in inventory refuses that.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_spoolman_assignment_verification import (  # noqa: F401
    SPOOL,
    _spoolman_client,
    printer_id,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _assign(async_client, printer_id, spool=SPOOL, **slot):
    client = _spoolman_client()
    client.get_spool = AsyncMock(return_value=spool)
    with (
        patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=client)),
        patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm,
    ):
        pm.get_client = MagicMock(return_value=None)
        pm.get_status = MagicMock(return_value=None)
        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 10, "printer_id": printer_id, **{"ams_id": 0, "tray_id": 0, **slot}},
        )
    return response


@pytest.mark.parametrize("ams_id", [8, 50, 127, 192, 254])
async def test_an_ams_id_no_slot_has_is_refused(async_client: AsyncClient, printer_id, ams_id):
    assert (await _assign(async_client, printer_id, ams_id=ams_id)).status_code == 422


@pytest.mark.parametrize("ams_id", [0, 7, 128, 191, 255])
async def test_every_real_slot_is_accepted(async_client: AsyncClient, printer_id, ams_id):
    response = await _assign(async_client, printer_id, ams_id=ams_id)

    assert response.status_code == 200, response.text


async def test_an_archived_spool_is_refused(async_client: AsyncClient, printer_id):
    response = await _assign(async_client, printer_id, spool={**SPOOL, "archived": True})

    assert response.status_code == 400, response.text


@pytest.mark.parametrize(("ams_id", "tray_id"), [(50, 0), (0, 4)])
async def test_a_link_to_no_real_slot_writes_nothing_to_spoolman(
    async_client: AsyncClient, printer_id, ams_id, tray_id
):
    client = _spoolman_client()
    with patch("backend.app.api.routes.spoolman.get_spoolman_client", AsyncMock(return_value=client)):
        response = await async_client.post(
            "/api/v1/spoolman/spools/10/link",
            json={
                "tray_uuid": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
                "printer_id": printer_id,
                "ams_id": ams_id,
                "tray_id": tray_id,
            },
        )

    assert response.status_code == 422, response.text
    client.merge_spool_extra.assert_not_awaited()


async def test_a_link_puts_no_archived_spool_on_a_slot(async_client: AsyncClient, printer_id):
    client = _spoolman_client()
    client.get_spool = AsyncMock(return_value={**SPOOL, "archived": True})
    with patch("backend.app.api.routes.spoolman.get_spoolman_client", AsyncMock(return_value=client)):
        response = await async_client.post(
            "/api/v1/spoolman/spools/10/link",
            json={"spool_tag": "A1B2C3D4E5F6A1B2", "printer_id": printer_id, "ams_id": 0, "tray_id": 0},
        )

    assert response.status_code == 400, response.text
    client.merge_spool_extra.assert_not_awaited()
