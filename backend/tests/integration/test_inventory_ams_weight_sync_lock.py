"""The built-in inventory's AMS weight sync, the weight lock and a printing printer.

A weight set by hand or measured on the SpoolBuddy scale is locked, so the
automatic remain% sync leaves it alone. "Sync Weights from AMS" is the explicit
override: it also updates locked spools and releases the lock. It leaves the
spools of a printing printer alone, whose usage the print's completion charges.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolbuddy_device import SpoolBuddyDevice


async def _assigned_spool(db_session, printer_factory, *, locked: bool) -> int:
    printer = await printer_factory()
    spool = Spool(material="PLA", label_weight=1000, core_weight=250, weight_used=100, weight_locked=locked)
    db_session.add(spool)
    await db_session.flush()
    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=0))
    await db_session.commit()
    return spool.id


def _printer(state: str, remain: int = 50, connected: bool = True):
    return SimpleNamespace(
        state=state, connected=connected, raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": remain}]}]}
    )


async def _sync(async_client, state):
    with patch("backend.app.services.printer_manager.printer_manager") as pm:
        pm.get_status = MagicMock(return_value=state)
        return await async_client.post("/api/v1/inventory/sync-ams-weights")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_sync_updates_a_locked_spool_and_releases_the_lock(
    async_client: AsyncClient, db_session, printer_factory
):
    spool_id = await _assigned_spool(db_session, printer_factory, locked=True)

    response = await _sync(async_client, _printer("IDLE"))

    assert response.status_code == 200
    assert response.json()["synced"] == 1
    spool = await db_session.get(Spool, spool_id)
    await db_session.refresh(spool)
    assert (spool.weight_used, spool.weight_locked) == (500, False)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("gcode_state", ["PREPARE", "RUNNING", "PAUSE"])
async def test_a_printing_printers_spool_is_left_alone(
    async_client: AsyncClient, db_session, printer_factory, gcode_state
):
    spool_id = await _assigned_spool(db_session, printer_factory, locked=False)

    response = await _sync(async_client, _printer(gcode_state))

    assert (response.json()["synced"], response.json()["printing"]) == (0, 1)
    spool = await db_session.get(Spool, spool_id)
    await db_session.refresh(spool)
    assert spool.weight_used == 100


@pytest.mark.asyncio
@pytest.mark.integration
async def test_weighing_on_the_scale_locks_the_spool(async_client: AsyncClient, db_session):
    db_session.add(
        SpoolBuddyDevice(
            device_id="sb-1",
            hostname="spoolbuddy-1",
            ip_address="10.0.0.1",
            last_seen=datetime.now(timezone.utc),
        )
    )
    spool = Spool(material="PLA", label_weight=1000, core_weight=250, weight_used=100)
    db_session.add(spool)
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/spoolbuddy/scale/update-spool-weight", json={"spool_id": spool.id, "weight_grams": 600}
    )

    assert response.status_code == 200
    await db_session.refresh(spool)
    assert (spool.weight_used, spool.weight_locked) == (650, True)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_print_whose_completion_is_still_being_charged_counts_as_printing(
    async_client: AsyncClient, db_session, printer_factory
):
    """The printer already reports FINISH; the usage session is still open
    until completion has charged the print."""
    from backend.app.services.usage_tracker import PrintSession, _active_sessions

    spool_id = await _assigned_spool(db_session, printer_factory, locked=False)
    printer_id = await db_session.scalar(select(SpoolAssignment.printer_id).where(SpoolAssignment.spool_id == spool_id))
    _active_sessions[printer_id] = PrintSession(
        printer_id=printer_id, print_name="x", started_at=datetime.now(timezone.utc)
    )
    try:
        response = await _sync(async_client, _printer("FINISH"))
    finally:
        _active_sessions.pop(printer_id, None)

    assert (response.json()["synced"], response.json()["printing"]) == (0, 1)
    spool = await db_session.get(Spool, spool_id)
    await db_session.refresh(spool)
    assert spool.weight_used == 100


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_disconnected_printers_last_report_is_not_used(async_client: AsyncClient, db_session, printer_factory):
    spool_id = await _assigned_spool(db_session, printer_factory, locked=False)

    response = await _sync(async_client, _printer("unknown", connected=False))

    assert response.json()["synced"] == 0
    spool = await db_session.get(Spool, spool_id)
    await db_session.refresh(spool)
    assert spool.weight_used == 100


def _broadcast_types(ws) -> list[str]:
    return [c.args[0]["type"] for c in ws.broadcast.call_args_list]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_synced_weight_is_announced(async_client: AsyncClient, db_session, printer_factory):
    """WU-9: other open tabs refresh only on inventory_changed."""
    from unittest.mock import AsyncMock

    await _assigned_spool(db_session, printer_factory, locked=False)

    with patch("backend.app.api.routes.inventory.ws_manager") as ws:
        ws.broadcast = AsyncMock()
        await _sync(async_client, _printer("IDLE"))

    assert "inventory_changed" in _broadcast_types(ws)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_weighed_spool_is_announced(async_client: AsyncClient, db_session):
    from unittest.mock import AsyncMock

    db_session.add(
        SpoolBuddyDevice(
            device_id="sb-1", hostname="spoolbuddy-1", ip_address="10.0.0.1", last_seen=datetime.now(timezone.utc)
        )
    )
    spool = Spool(material="PLA", label_weight=1000, core_weight=250, weight_used=100)
    db_session.add(spool)
    await db_session.commit()

    with patch("backend.app.api.routes.spoolbuddy.ws_manager") as ws:
        ws.broadcast = AsyncMock()
        await async_client.post(
            "/api/v1/spoolbuddy/scale/update-spool-weight", json={"spool_id": spool.id, "weight_grams": 600}
        )

    assert "inventory_changed" in _broadcast_types(ws)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_print_whose_usage_is_being_booked_counts_as_printing(
    async_client: AsyncClient, db_session, printer_factory
):
    """WU-3: the tracker has taken the session but not charged the print yet."""
    from backend.app.services import usage_tracker

    spool_id = await _assigned_spool(db_session, printer_factory, locked=False)
    printer_id = await db_session.scalar(select(SpoolAssignment.printer_id).where(SpoolAssignment.spool_id == spool_id))
    usage_tracker._completing.add(printer_id)
    try:
        response = await _sync(async_client, _printer("FINISH"))
    finally:
        usage_tracker._completing.discard(printer_id)

    assert (response.json()["synced"], response.json()["printing"]) == (0, 1)
