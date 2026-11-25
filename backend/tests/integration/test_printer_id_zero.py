"""A printer id of 0 is checked like any other printer id.

Smart plug create and edit and virtual printer create tested the id for
truthiness, so 0 skipped the "Printer not found" check: PostgreSQL then failed
on the foreign key with a server error, SQLite stored a plug or virtual
printer pointing at a printer that does not exist.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_creating_a_plug_for_printer_0_is_refused(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/smart-plugs/", json={"name": "Plug", "ip_address": "192.168.1.100", "printer_id": 0}
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Printer not found"
    assert (await async_client.get("/api/v1/smart-plugs/")).json() == []


async def test_moving_a_plug_to_printer_0_is_refused(async_client: AsyncClient):
    created = await async_client.post("/api/v1/smart-plugs/", json={"name": "Plug", "ip_address": "192.168.1.100"})
    assert created.status_code == 200, created.text
    plug_id = created.json()["id"]

    response = await async_client.patch(f"/api/v1/smart-plugs/{plug_id}", json={"printer_id": 0})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Printer not found"
    assert (await async_client.get(f"/api/v1/smart-plugs/{plug_id}")).json()["printer_id"] is None


async def test_creating_a_virtual_printer_for_printer_0_is_refused(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/virtual-printers",
        json={"name": "VP", "mode": "queue", "access_code": "12345678", "target_printer_id": 0},
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Printer with ID 0 not found"
    listed = (await async_client.get("/api/v1/virtual-printers")).json()
    printers = listed if isinstance(listed, list) else listed.get("printers", [])
    assert printers == []
