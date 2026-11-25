"""Spoolman slot changes are announced to pages open elsewhere (#3402).

A page refetches its slot rows, and the tag -> spool map the slot cards fall
back on, when the server announces a change over the websocket. A scale that
assigned a spool through the API left the printer page's hover card on the
previous spool until a reload. The assign route announces its slots since
#1606; these are the other Spoolman routes that write or clear a slot row or a
spool's tag, which built-in inventory already announces.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

TAG = "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"


@pytest.fixture
async def spoolman_settings(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


@pytest.fixture
def client():
    c = MagicMock()
    c.base_url = "http://localhost:7912"
    c.health_check = AsyncMock(return_value=True)
    c.has_tag_api = AsyncMock(return_value=False)
    c.add_native_tags = AsyncMock(return_value=0)
    c.unlink_all_native_tags = AsyncMock()
    c.ensure_tag_extra_field = AsyncMock(return_value=True)
    c.ensure_extra_field = AsyncMock(return_value=True)
    c.get_spools = AsyncMock(return_value=[])
    c.get_spool = AsyncMock(return_value={"id": 5, "archived": False, "extra": {}})
    c.merge_spool_extra = AsyncMock(return_value={"id": 5, "extra": {"tag": f'"{TAG}"'}})
    with (
        patch("backend.app.api.routes.spoolman.get_spoolman_client", AsyncMock(return_value=c)),
        patch("backend.app.api.routes.spoolman.init_spoolman_client", AsyncMock(return_value=c)),
        patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=c)),
        patch("backend.app.api.routes.spoolman.printer_manager.get_client", MagicMock(return_value=None)),
    ):
        yield c


@pytest.fixture
def ws():
    """One websocket for both route modules: the announcement helper lives in
    spoolman_inventory, the link routes in spoolman."""
    w = MagicMock()
    w.broadcast = AsyncMock()
    with (
        patch("backend.app.api.routes.spoolman_inventory.ws_manager", w),
        patch("backend.app.api.routes.spoolman.ws_manager", w),
    ):
        yield w


def _slots(ws) -> set[tuple[int, int, int]]:
    return {
        (c.args[0]["printer_id"], c.args[0]["ams_id"], c.args[0]["tray_id"])
        for c in ws.broadcast.await_args_list
        if c.args[0].get("type") == "spool_assignment_changed"
    }


def _inventory_changed(ws) -> bool:
    return any(c.args[0].get("type") == "inventory_changed" for c in ws.broadcast.await_args_list)


async def _row(db, printer_id: int, ams_id: int, tray_id: int, spool_id: int = 5) -> None:
    db.add(SpoolmanSlotAssignment(printer_id=printer_id, ams_id=ams_id, tray_id=tray_id, spoolman_spool_id=spool_id))
    await db.commit()


async def test_unassign_announces_every_slot_it_cleared(
    async_client: AsyncClient, db_session, printer_factory, spoolman_settings, client, ws
):
    here = await printer_factory(name="Here")
    there = await printer_factory(name="There")
    await _row(db_session, here.id, 0, 0)
    await _row(db_session, there.id, 1, 2)
    await _row(db_session, there.id, 0, 3, spool_id=6)  # another spool's slot

    response = await async_client.delete("/api/v1/spoolman/inventory/slot-assignments/5")

    assert response.status_code == 200, response.text
    assert _slots(ws) == {(here.id, 0, 0), (there.id, 1, 2)}


async def test_unassign_of_a_spool_in_no_slot_announces_nothing(
    async_client: AsyncClient, db_session, printer_factory, spoolman_settings, client, ws
):
    response = await async_client.delete("/api/v1/spoolman/inventory/slot-assignments/5")

    assert response.status_code == 200, response.text
    assert _slots(ws) == set()


async def test_a_link_onto_a_slot_announces_the_slot_and_the_tag(
    async_client: AsyncClient, db_session, printer_factory, spoolman_settings, client, ws
):
    printer = await printer_factory()

    response = await async_client.post(
        "/api/v1/spoolman/spools/5/link",
        json={"tray_uuid": TAG, "printer_id": printer.id, "ams_id": 0, "tray_id": 1},
    )

    assert response.status_code == 200, response.text
    assert _slots(ws) == {(printer.id, 0, 1)}
    assert _inventory_changed(ws)


async def test_a_tag_only_link_announces_the_tag(async_client: AsyncClient, db_session, spoolman_settings, client, ws):
    """No slot row, but the slot cards find a spool by its tag."""
    response = await async_client.post("/api/v1/spoolman/spools/5/link", json={"tray_uuid": TAG})

    assert response.status_code == 200, response.text
    assert _slots(ws) == set()
    assert _inventory_changed(ws)


async def test_unlink_announces_the_slots_it_cleared(
    async_client: AsyncClient, db_session, printer_factory, spoolman_settings, client, ws
):
    printer = await printer_factory()
    await _row(db_session, printer.id, 0, 2)
    client.merge_spool_extra = AsyncMock(return_value={"id": 5, "extra": {"tag": '""'}})

    response = await async_client.post("/api/v1/spoolman/spools/5/unlink")

    assert response.status_code == 200, response.text
    assert _slots(ws) == {(printer.id, 0, 2)}
    assert _inventory_changed(ws)


async def test_a_spool_created_from_a_slot_is_announced(
    async_client: AsyncClient, db_session, printer_factory, spoolman_settings, client, ws
):
    printer = await printer_factory()
    tray = {"id": 3, "tag_uid": "0102030405060708", "tray_uuid": TAG, "tray_type": "PLA"}
    status = SimpleNamespace(raw_data={"ams": [{"id": 0, "tray": [tray]}]})
    client.parse_ams_tray = MagicMock(return_value=SimpleNamespace(tag_uid=tray["tag_uid"], tray_uuid=TAG))
    client.sync_ams_tray = AsyncMock(return_value={"id": 9})

    with patch("backend.app.api.routes.spoolman.printer_manager.get_status", MagicMock(return_value=status)):
        response = await async_client.post(
            "/api/v1/spoolman/spools/from-slot", json={"printer_id": printer.id, "ams_id": 0, "tray_id": 3}
        )

    assert response.status_code == 200, response.text
    assert _slots(ws) == {(printer.id, 0, 3)}
    assert _inventory_changed(ws)


async def test_the_assign_announces_after_the_previous_spool_lost_the_slots_tag(
    async_client: AsyncClient, db_session, printer_factory, spoolman_settings, client, ws
):
    """The reported path: a page that refetches on the announcement must not
    read the previous spool still holding the slot's fallback tag."""
    import json

    from backend.app.services.spoolman_tracking import get_fallback_spool_tag_for_slot

    printer = await printer_factory(serial_number="01S00C123456789")
    fallback = get_fallback_spool_tag_for_slot(printer.serial_number, 0, 0)
    previous = {"id": 32, "archived": False, "extra": {"tag": json.dumps(fallback)}}
    client.get_spools = AsyncMock(return_value=[previous])

    async def _merge(spool_id, fields):
        if spool_id == 32:
            previous["extra"] = {**previous["extra"], **fields}
        return {"id": spool_id, "extra": fields}

    client.merge_spool_extra = AsyncMock(side_effect=_merge)
    tag_when_announced: list[str] = []

    async def _broadcast(message):
        if message.get("type") == "spool_assignment_changed":
            tag_when_announced.append(json.loads(previous["extra"]["tag"]))

    ws.broadcast = AsyncMock(side_effect=_broadcast)

    with patch("backend.app.api.routes.spoolman_inventory.printer_manager.get_client", MagicMock(return_value=None)):
        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 5, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
        )

    assert response.status_code == 200, response.text
    assert tag_when_announced == [""], "announced while spool #32 still held the slot's tag"
