"""An archived or deleted spool gives its AMS slots up.

Assigning refuses an archived spool, and nothing else looks at the archive
flag: a slot left pointing at one kept showing it, and the next print charged
its usage to it. A deleted Spoolman spool left its slot row pointing at an id
Spoolman no longer has.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

SPOOLMAN_SPOOL = {
    "id": 42,
    "filament": {"id": 1, "name": "PLA", "material": "PLA", "weight": 1000, "color_hex": "FF0000"},
    "remaining_weight": 800.0,
    "used_weight": 200.0,
    "archived": False,
    "extra": {},
}


async def _assigned_built_in_spools(db_session, printer_factory, count=1) -> list[int]:
    printer = await printer_factory()
    ids = []
    for tray in range(count):
        spool = Spool(material="PLA", label_weight=1000, weight_used=0)
        db_session.add(spool)
        await db_session.flush()
        db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=tray))
        ids.append(spool.id)
    await db_session.commit()
    return ids


async def _built_in_slots(db_session) -> list:
    return list((await db_session.execute(select(SpoolAssignment))).scalars().all())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_archiving_a_spool_unassigns_it(async_client: AsyncClient, db_session, printer_factory):
    (spool_id,) = await _assigned_built_in_spools(db_session, printer_factory)

    with patch("backend.app.api.routes.inventory.ws_manager") as ws:
        ws.broadcast = AsyncMock()
        response = await async_client.post(f"/api/v1/inventory/spools/{spool_id}/archive")

    assert response.status_code == 200
    assert await _built_in_slots(db_session) == []
    sent = [c.args[0]["type"] for c in ws.broadcast.call_args_list]
    assert "spool_assignment_changed" in sent


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bulk_archiving_unassigns_every_spool(async_client: AsyncClient, db_session, printer_factory):
    ids = await _assigned_built_in_spools(db_session, printer_factory, count=2)

    response = await async_client.post("/api/v1/inventory/spools/bulk-archive", json={"ids": ids})

    assert response.status_code == 200
    assert await _built_in_slots(db_session) == []


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("bulk", [False, True])
async def test_deleting_a_spool_announces_the_freed_slots(async_client: AsyncClient, db_session, printer_factory, bulk):
    """SA-4: other tabs refresh slot views only on spool_assignment_changed;
    inventory_changed alone left the deleted spool shown in its slot."""
    ids = await _assigned_built_in_spools(db_session, printer_factory, count=2)

    with patch("backend.app.api.routes.inventory.ws_manager") as ws:
        ws.broadcast = AsyncMock()
        if bulk:
            response = await async_client.post("/api/v1/inventory/spools/bulk-delete", json={"ids": ids})
        else:
            response = await async_client.delete(f"/api/v1/inventory/spools/{ids[0]}")

    assert response.status_code == 200
    freed = sorted(
        c.args[0]["tray_id"] for c in ws.broadcast.call_args_list if c.args[0]["type"] == "spool_assignment_changed"
    )
    assert freed == ([0, 1] if bulk else [0])
    assert [a.tray_id for a in await _built_in_slots(db_session)] == ([] if bulk else [1])


@pytest.fixture
async def spoolman_slot(db_session, printer_factory):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    printer = await printer_factory()
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=0, spoolman_spool_id=42))
    await db_session.commit()


def _client():
    client = MagicMock()
    client.base_url = "http://localhost:7912"
    client.health_check = AsyncMock(return_value=True)
    client.delete_spool = AsyncMock(return_value=True)
    client.set_spool_archived = AsyncMock(
        side_effect=lambda spool_id, archived: {**SPOOLMAN_SPOOL, "archived": archived}
    )
    return client


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/v1/spoolman/inventory/spools/42/archive", None),
        ("post", "/api/v1/spoolman/inventory/spools/bulk-archive", {"ids": [42]}),
        ("delete", "/api/v1/spoolman/inventory/spools/42", None),
        ("post", "/api/v1/spoolman/inventory/spools/bulk-delete", {"ids": [42]}),
    ],
)
async def test_a_spoolman_spool_archived_or_deleted_gives_its_slot_up(
    async_client: AsyncClient, db_session, spoolman_slot, method, path, body
):
    with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=_client())):
        call = getattr(async_client, method)
        response = await (call(path, json=body) if body is not None else call(path))

    assert response.status_code == 200, response.text
    rows = (await db_session.execute(select(SpoolmanSlotAssignment))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_archived_spools_tag_still_in_the_ams_is_not_adopted(test_engine, db_session, printer_factory):
    """The spool ran out and was archived; its empty core, tag intact, waits in
    the AMS to be swapped. The freed slot stays free: no new spool is made
    from the tag, and no unopened spool of the same filament takes it over."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.main import on_ams_change

    printer = await printer_factory()
    uuid = "A1B2C3D4E5F60718293A4B5C6D7E8F90"
    db_session.add(
        Spool(
            material="PLA",
            label_weight=1000,
            weight_used=1000,
            tray_uuid=uuid,
            tag_uid="0A1B2C3D4E5F6071",
            archived_at=datetime.now(timezone.utc),
        )
    )
    # An unopened spool of the same filament, which an unknown tag would be linked to.
    db_session.add(Spool(material="PLA", label_weight=1000, weight_used=0, rgba="FF0000FF"))
    await db_session.commit()

    ams_data = [
        {
            "id": 0,
            "tray": [
                {
                    "id": 0,
                    "tray_type": "PLA",
                    "tray_color": "FF0000FF",
                    "tray_info_idx": "GFA00",
                    "tray_uuid": uuid,
                    "tag_uid": "0A1B2C3D4E5F6071",
                    "remain": 0,
                }
            ],
        }
    ]
    state = SimpleNamespace(raw_data={"ams": {"ams": ams_data}}, state="IDLE", connected=True)
    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.main.async_session", real_db),
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.ws_manager") as ws,
    ):
        pm.get_printer.return_value = MagicMock(name="X1C", serial_number="ARCHTAG")
        pm.get_status.return_value = state
        pm.get_client.return_value = MagicMock()
        pm.get_model.return_value = "X1C"
        relay.on_ams_change = AsyncMock()
        ws.send_printer_status = AsyncMock()
        ws.broadcast = AsyncMock()

        await on_ams_change(printer.id, ams_data)

    db_session.expire_all()
    assert await _built_in_slots(db_session) == []
    spools = (await db_session.execute(select(Spool))).scalars().all()
    assert len(spools) == 2
    unopened = next(s for s in spools if s.archived_at is None)
    assert unopened.tray_uuid is None and unopened.tag_uid is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_archiving_a_spoolman_spool_announces_the_freed_slot(
    async_client: AsyncClient, db_session, spoolman_slot
):
    with (
        patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=_client())),
        patch("backend.app.api.routes.spoolman_inventory.ws_manager") as ws,
    ):
        ws.broadcast = AsyncMock()
        response = await async_client.post("/api/v1/spoolman/inventory/spools/42/archive")

    assert response.status_code == 200
    sent = [c.args[0] for c in ws.broadcast.call_args_list]
    assert any(m["type"] == "spool_assignment_changed" and m["tray_id"] == 0 for m in sent)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_spoolman_spool_feeding_a_print_keeps_its_slot_until_the_print_is_charged(
    async_client: AsyncClient, test_engine, db_session, spoolman_slot, archive_factory
):
    """Its usage is charged at completion by the slot's spool, which only the
    slot row knows for a spool without a tag."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.models.active_print_spoolman import ActivePrintSpoolman
    from backend.app.services.spoolman_tracking import release_archived_slot_rows

    printer_id = await db_session.scalar(select(SpoolmanSlotAssignment.printer_id))
    archive = await archive_factory(printer_id, status="printing", with_run=False)
    db_session.add(ActivePrintSpoolman(printer_id=printer_id, archive_id=archive.id, filament_usage=[], ams_trays={}))
    await db_session.commit()

    with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=_client())):
        response = await async_client.post("/api/v1/spoolman/inventory/spools/42/archive")
    assert response.status_code == 200
    assert len((await db_session.execute(select(SpoolmanSlotAssignment))).scalars().all()) == 1

    # The print completed and its usage was reported; Spoolman has the spool archived.
    await db_session.execute(ActivePrintSpoolman.__table__.delete())
    await db_session.commit()
    client = _client()
    client.get_all_spools = AsyncMock(return_value=[{**SPOOLMAN_SPOOL, "archived": True}])
    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.services.spoolman_tracking.async_session", real_db),
        patch(
            "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback", AsyncMock(return_value=client)
        ),
        patch("backend.app.core.websocket.ws_manager.broadcast", AsyncMock()),
    ):
        await release_archived_slot_rows(printer_id)

    db_session.expire_all()
    assert (await db_session.execute(select(SpoolmanSlotAssignment))).scalars().all() == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_release_after_a_print_keeps_a_live_spools_slot(test_engine, db_session, spoolman_slot):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.services.spoolman_tracking import release_archived_slot_rows

    printer_id = await db_session.scalar(select(SpoolmanSlotAssignment.printer_id))
    client = _client()
    client.get_all_spools = AsyncMock(return_value=[SPOOLMAN_SPOOL])
    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.services.spoolman_tracking.async_session", real_db),
        patch(
            "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback", AsyncMock(return_value=client)
        ),
    ):
        await release_archived_slot_rows(printer_id)

    db_session.expire_all()
    assert len((await db_session.execute(select(SpoolmanSlotAssignment))).scalars().all()) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bulk_archive_releases_a_spool_archived_before_it_released_slots(
    async_client: AsyncClient, db_session, printer_factory
):
    from datetime import datetime, timezone

    (spool_id,) = await _assigned_built_in_spools(db_session, printer_factory)
    spool = await db_session.get(Spool, spool_id)
    spool.archived_at = datetime.now(timezone.utc)
    await db_session.commit()

    response = await async_client.post("/api/v1/inventory/spools/bulk-archive", json={"ids": [spool_id]})

    assert response.json()["already_archived"] == [spool_id]
    assert await _built_in_slots(db_session) == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_release_after_a_print_frees_a_slot_of_a_spool_spoolman_no_longer_has(
    test_engine, db_session, spoolman_slot
):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.services.spoolman_tracking import release_archived_slot_rows

    printer_id = await db_session.scalar(select(SpoolmanSlotAssignment.printer_id))
    client = _client()
    client.get_all_spools = AsyncMock(return_value=[])
    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.services.spoolman_tracking.async_session", real_db),
        patch(
            "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback", AsyncMock(return_value=client)
        ),
        patch("backend.app.core.websocket.ws_manager.broadcast", AsyncMock()),
    ):
        await release_archived_slot_rows(printer_id)

    db_session.expire_all()
    assert (await db_session.execute(select(SpoolmanSlotAssignment))).scalars().all() == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_archived_spools_third_party_tag_still_in_the_ams_is_not_offered_as_new(
    test_engine, db_session, printer_factory
):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.main import on_ams_change

    printer = await printer_factory()
    db_session.add(
        Spool(material="PETG", label_weight=1000, tag_uid="04A1B2C3D4E5F6", archived_at=datetime.now(timezone.utc))
    )
    await db_session.commit()
    ams_data = [
        {"id": 0, "tray": [{"id": 0, "tray_type": "PETG", "tray_color": "00FF00FF", "tag_uid": "04A1B2C3D4E5F6"}]}
    ]
    state = SimpleNamespace(raw_data={"ams": {"ams": ams_data}}, state="IDLE", connected=True)
    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.main.async_session", real_db),
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main._broadcast_unknown_tag", new_callable=AsyncMock) as prompt,
    ):
        pm.get_printer.return_value = MagicMock(name="X1C", serial_number="ARCHTAG2")
        pm.get_status.return_value = state
        pm.get_client.return_value = MagicMock()
        pm.get_model.return_value = "X1C"
        relay.on_ams_change = AsyncMock()
        ws.send_printer_status = AsyncMock()
        ws.broadcast = AsyncMock()

        await on_ams_change(printer.id, ams_data)

    prompt.assert_not_awaited()
