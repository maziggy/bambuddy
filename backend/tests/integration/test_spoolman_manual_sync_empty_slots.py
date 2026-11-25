"""The manual Spoolman sync and slots that read empty.

The automatic pass unlinks a Spoolman slot only when it reads empty while the
printer is not printing and the presence bit does not say a spool is there.
The Sync buttons in Settings deleted the row whenever the tray had no type or
colour: mid-print (a runout reads empty), or a non-RFID spool whose tray type
was never set.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.settings import Settings
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment


@pytest.fixture
async def printer_id(db_session, printer_factory):
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    printer = await printer_factory()
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=0, spoolman_spool_id=42))
    await db_session.commit()
    return printer.id


async def _sync(async_client, printer_id, path, gcode_state, exists):
    tray = {"id": 0, "tray_type": "", "tray_color": ""}
    if exists is not None:
        tray["exists"] = exists
    state = SimpleNamespace(state=gcode_state, raw_data={"ams": [{"id": 0, "tray": [tray]}]})
    client = MagicMock()
    client.health_check = AsyncMock(return_value=True)
    client.get_spools = AsyncMock(return_value=[])
    client.parse_ams_tray = MagicMock(return_value=None)
    with (
        patch("backend.app.api.routes.spoolman.get_spoolman_client", AsyncMock(return_value=client)),
        patch("backend.app.api.routes.spoolman.printer_manager") as pm,
    ):
        pm.get_status = MagicMock(return_value=state)
        response = await async_client.post(path.format(printer_id=printer_id))
    assert response.status_code == 200, response.text


PATHS = ["/api/v1/spoolman/sync/{printer_id}", "/api/v1/spoolman/sync-all"]


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("gcode_state", ["RUNNING", "PAUSE"])
async def test_a_slot_reading_empty_mid_print_keeps_its_spool(
    async_client: AsyncClient, db_session, printer_id, path, gcode_state
):
    await _sync(async_client, printer_id, path, gcode_state, exists=None)

    rows = (await db_session.execute(select(SpoolmanSlotAssignment))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("path", PATHS)
async def test_a_slot_the_presence_bit_calls_occupied_keeps_its_spool(
    async_client: AsyncClient, db_session, printer_id, path
):
    await _sync(async_client, printer_id, path, "IDLE", exists=True)

    rows = (await db_session.execute(select(SpoolmanSlotAssignment))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("path", PATHS)
async def test_an_empty_slot_of_an_idle_printer_is_unlinked(async_client: AsyncClient, db_session, printer_id, path):
    await _sync(async_client, printer_id, path, "IDLE", exists=False)

    rows = (await db_session.execute(select(SpoolmanSlotAssignment))).scalars().all()
    assert rows == []
