"""A printer's new IP reaches the proxy virtual printers aimed at it.

Proxy VPs forward to the IP they were started with. The sync that restarts
them on a changed target IP ran only when a virtual printer was saved, so
editing the printer's IP left them forwarding to the old address until then.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


@pytest.fixture
def vp_sync():
    with (
        patch("backend.app.api.routes.printers.printer_manager.disconnect_printer"),
        patch("backend.app.api.routes.printers.printer_manager.connect_printer", new=AsyncMock()),
        patch(
            "backend.app.services.virtual_printer.virtual_printer_manager.sync_from_db",
            new_callable=AsyncMock,
        ) as sync,
    ):
        yield sync


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ip_change_syncs_virtual_printers(async_client: AsyncClient, printer_factory, vp_sync):
    printer = await printer_factory(ip_address="192.168.1.50")

    response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"ip_address": "192.168.1.77"})

    assert response.status_code == 200
    vp_sync.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_unchanged_ip_does_not_sync(async_client: AsyncClient, printer_factory, vp_sync):
    printer = await printer_factory(ip_address="192.168.1.50")

    response = await async_client.patch(
        f"/api/v1/printers/{printer.id}", json={"name": "Renamed", "ip_address": "192.168.1.50"}
    )

    assert response.status_code == 200
    vp_sync.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failed_sync_does_not_fail_the_update(async_client: AsyncClient, printer_factory, vp_sync):
    printer = await printer_factory(ip_address="192.168.1.50")
    vp_sync.side_effect = RuntimeError("sync broke")

    response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"ip_address": "192.168.1.77"})

    assert response.status_code == 200
    assert response.json()["ip_address"] == "192.168.1.77"
